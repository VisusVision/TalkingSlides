from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .policy_engine import PolicyEngine
from .providers.local_rules_provider import LocalRulesProvider
from .providers.ollama_provider import OllamaProvider
from .providers.translation_provider import TranslationModerationProvider, TranslationResult
from .schemas import AgentFindingSchema, AgentResultSchema, FindingLocation


TEXT_AGENT_SLUG = "text_moderation_local_rules"
TEXT_AGENT_VERSION = "local-rules:v1"


@dataclass(frozen=True)
class TextContentItem:
    text: str
    location: FindingLocation


class TextModerationAgent:
    def __init__(
        self,
        provider: LocalRulesProvider | None = None,
        policy_engine: PolicyEngine | None = None,
        ollama_provider: OllamaProvider | None = None,
        translation_provider: TranslationModerationProvider | None = None,
    ) -> None:
        self.provider = provider or LocalRulesProvider()
        self.policy_engine = policy_engine or PolicyEngine()
        self.ollama_provider = ollama_provider or OllamaProvider()
        self.translation_provider = translation_provider or TranslationModerationProvider()

    def scan_project(self, project) -> AgentResultSchema:
        items = list(self._content_items(project))
        findings: list[AgentFindingSchema] = []
        hasher = _hash_items(items)

        for item in items:
            text = str(item.text or "")
            findings.extend(self.provider.scan_text(text, item.location))

        local_findings = list(findings)
        local_decision = self.policy_engine.combine_findings(local_findings)
        review_text = self._review_text(items)
        ollama_result = None
        if self._should_review_with_ollama(local_decision, local_findings):
            ollama_result = self.ollama_provider.review_text(
                review_text,
                FindingLocation(
                    project_id=int(project.id),
                    field_name="project_text",
                    ui_anchor=f"project-{project.id}-moderation-review",
                ),
            )
            findings.extend(ollama_result.findings)
        translation_result = None
        if self._should_review_with_translation(local_decision, local_findings, review_text):
            translation_result = self._review_with_translation(review_text, int(project.id))
            findings.extend(translation_result.findings)

        local_result = AgentResultSchema(
            agent_slug=TEXT_AGENT_SLUG,
            agent_version=TEXT_AGENT_VERSION,
            modality="text",
            provider=self.provider.provider_name,
            decision=local_decision,
            confidence=max((finding.confidence for finding in local_findings), default=0.0),
            findings=local_findings,
            metadata={},
        )
        secondary_results = [
            result
            for result in (ollama_result, translation_result)
            if result is not None
        ]
        decision = self.policy_engine.combine_results(
            [local_result, *secondary_results]
        )
        confidence = max((finding.confidence for finding in findings), default=0.0)
        provider_parts = [self.provider.provider_name, *[result.provider for result in secondary_results]]
        return AgentResultSchema(
            agent_slug=TEXT_AGENT_SLUG,
            agent_version=TEXT_AGENT_VERSION,
            modality="text",
            provider="+".join(provider_parts),
            decision=decision,
            confidence=confidence,
            findings=findings,
            metadata={
                "input_hash": hasher,
                "scanned_field_count": len(items),
                "ollama_enabled": self.ollama_provider.is_enabled(),
                "ollama_called": ollama_result is not None,
                "ollama_metadata": ollama_result.metadata if ollama_result is not None else {},
                "translation_enabled": self.translation_provider.is_enabled(),
                "translation_called": translation_result is not None,
                "translation_metadata": translation_result.metadata if translation_result is not None else {},
            },
        )

    def input_hash_for_project(self, project) -> str:
        return _hash_items(list(self._content_items(project)))

    def _should_review_with_ollama(self, local_decision: str, findings: list[AgentFindingSchema]) -> bool:
        if not self.ollama_provider.is_enabled():
            return False
        if not findings:
            return False
        if any(
            finding.decision == "block" and finding.confidence >= self.policy_engine.block_confidence_threshold
            for finding in findings
        ):
            return False
        return local_decision in {"warn", "needs_admin_review"}

    def _should_review_with_translation(
        self,
        local_decision: str,
        findings: list[AgentFindingSchema],
        review_text: str,
    ) -> bool:
        if not self.translation_provider.is_enabled():
            return False
        if not str(review_text or "").strip():
            return False
        if any(
            finding.decision == "block" and finding.confidence >= self.policy_engine.block_confidence_threshold
            for finding in findings
        ):
            return False
        return local_decision in {"allow", "warn", "needs_admin_review"}

    def _review_with_translation(self, review_text: str, project_id: int) -> AgentResultSchema:
        location = FindingLocation(
            project_id=project_id,
            field_name="translated_text",
            ui_anchor=f"project-{project_id}-translation-moderation-review",
        )
        try:
            translation = self.translation_provider.translate_text(review_text)
        except Exception as exc:  # noqa: BLE001
            translation = TranslationResult(
                success=False,
                provider=getattr(self.translation_provider, "provider_name", "translation_moderation"),
                target_language="en",
                error_message=str(exc)[:240],
                metadata={
                    "skipped": True,
                    "reason": "translation_provider_error",
                    "error": exc.__class__.__name__,
                },
            )

        translated_text = str(translation.translated_text or "").strip()
        translated_findings: list[AgentFindingSchema] = []
        if translation.success and translated_text:
            translated_findings = [
                self._cap_translation_finding(finding, translation)
                for finding in self.provider.scan_text(translated_text, location)
            ]

        return AgentResultSchema(
            agent_slug=TEXT_AGENT_SLUG,
            agent_version=TEXT_AGENT_VERSION,
            modality="text",
            provider=f"translation_moderation:{translation.provider}",
            decision=self.policy_engine.combine_findings(translated_findings),
            confidence=max((finding.confidence for finding in translated_findings), default=0.0),
            findings=translated_findings,
            metadata={
                "advisory_only": True,
                "provider": translation.provider,
                "success": translation.success,
                "source_language": translation.source_language,
                "target_language": translation.target_language,
                "error_message": translation.error_message,
                "translated_text_excerpt": _short_excerpt(translated_text),
                **translation.metadata,
            },
        )

    def _cap_translation_finding(
        self,
        finding: AgentFindingSchema,
        translation: TranslationResult,
    ) -> AgentFindingSchema:
        decision = finding.decision
        confidence = finding.confidence
        admin_note = (
            f"Secondary translation moderation provider={translation.provider}; "
            f"source={translation.source_language}; target={translation.target_language}."
        )
        if finding.decision == "block":
            decision = "needs_admin_review"
            confidence = min(confidence, 0.74)
            admin_note += " Translated block decision capped to admin review."
        return finding.model_copy(
            update={
                "decision": decision,
                "confidence": confidence,
                "user_message": (
                    "A secondary English translation raised a moderation concern. "
                    "Please review this lesson or ask an admin to review it."
                ),
                "admin_message": f"{finding.admin_message} {admin_note}".strip(),
                "evidence_excerpt": _short_excerpt(finding.evidence_excerpt),
            }
        )

    def _review_text(self, items: list[TextContentItem], max_chars: int = 6000) -> str:
        chunks = []
        for item in items:
            text = str(item.text or "").strip()
            if not text:
                continue
            field_name = item.location.field_name or "text"
            chunks.append(f"[{field_name}]\n{text}")
        return "\n\n".join(chunks)[:max_chars]

    def _content_items(self, project) -> list[TextContentItem]:
        project_id = int(project.id)
        items = [
            TextContentItem(
                text=getattr(project, "title", "") or "",
                location=FindingLocation(
                    project_id=project_id,
                    field_name="title",
                    ui_anchor=f"project-{project_id}-title",
                ),
            ),
            TextContentItem(
                text=getattr(project, "description", "") or "",
                location=FindingLocation(
                    project_id=project_id,
                    field_name="description",
                    ui_anchor=f"project-{project_id}-description",
                ),
            ),
        ]

        pages = project.transcript_pages.all()
        if hasattr(pages, "filter"):
            try:
                pages = pages.filter(is_active=True)
            except Exception:
                pass
        if hasattr(pages, "order_by"):
            pages = pages.order_by("order", "id")

        for page in pages:
            base_location = {
                "project_id": project_id,
                "transcript_page_id": int(page.id),
                "page_key": str(getattr(page, "page_key", "") or ""),
                "slide_order": int(getattr(page, "order", 0) or 0),
            }
            for field_name in ("original_text", "narration_text"):
                items.append(
                    TextContentItem(
                        text=getattr(page, field_name, "") or "",
                        location=FindingLocation(
                            **base_location,
                            field_name=field_name,
                            ui_anchor=f"transcript-page-{page.id}",
                        ),
                    )
                )
        return items


def project_text_input_hash(project) -> str:
    return TextModerationAgent().input_hash_for_project(project)


def _hash_items(items: list[TextContentItem]) -> str:
    hasher = hashlib.sha256()
    for item in items:
        hasher.update(str(item.text or "").encode("utf-8", errors="ignore"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def _short_excerpt(text: str, limit: int = 220) -> str:
    return str(text or "").strip()[:limit]
