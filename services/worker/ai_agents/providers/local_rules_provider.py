from __future__ import annotations

from dataclasses import dataclass
import re

from ..schemas import AgentFindingSchema, Decision, FindingLocation, ModerationCategory, Severity
from .base import TextModerationProvider


TURKISH_ASCII_TRANSLATION = str.maketrans(
    {
        "ç": "c",
        "Ç": "C",
        "ğ": "g",
        "Ğ": "G",
        "ı": "i",
        "I": "I",
        "İ": "I",
        "ö": "o",
        "Ö": "O",
        "ş": "s",
        "Ş": "S",
        "ü": "u",
        "Ü": "U",
    }
)


EDUCATIONAL_CONTEXT_PATTERNS = [
    r"\bhistory\b",
    r"\bhistorical\b",
    r"\bpolitics\b",
    r"\bpolitical science\b",
    r"\bmedicine\b",
    r"\bmedical\b",
    r"\bbiology\b",
    r"\bbiological\b",
    r"\bwar\b",
    r"\bbattle\b",
    r"\breligion\b",
    r"\breligious\b",
    r"\bculture\b",
    r"\bcultural\b",
    r"\beducational\b",
    r"\bdocumentary\b",
    r"\btarih(?:i|te|sel|inde)?\b",
    r"\bsiyaset\b",
    r"\bpolitika\b",
    r"\btip\b",
    r"\btibbi\b",
    r"\bbiyoloji\b",
    r"\bsavas(?:lar|ta|in)?\b",
    r"\bmuharebe\b",
    r"\bdin(?:i)?\b",
    r"\bkultur(?:el|u)?\b",
    r"\begitim(?:sel)?\b",
    r"\bogretim\b",
    r"\bbelgesel\b",
]


@dataclass(frozen=True)
class LocalRule:
    category: ModerationCategory
    severity: Severity
    decision: Decision
    confidence: float
    patterns: tuple[str, ...]
    user_message: str
    admin_message: str
    downgrade_in_educational_context: bool = False


LOCAL_RULES: tuple[LocalRule, ...] = (
    LocalRule(
        category="self_harm",
        severity="critical",
        decision="block",
        confidence=0.96,
        patterns=(
            r"\bkill yourself\b",
            r"\byou should kill yourself\b",
            r"\bgo commit suicide\b",
            r"\bhow to commit suicide\b",
        ),
        user_message="This lesson cannot be published because it appears to encourage self-harm.",
        admin_message="Local rule matched direct self-harm encouragement.",
    ),
    LocalRule(
        category="self_harm",
        severity="critical",
        decision="block",
        confidence=0.95,
        patterns=(
            r"\bkendini oldur\b",
            r"\bkendini oldurmelisin\b",
            r"\bintihar et\b",
            r"\bnasil intihar edilir\b",
        ),
        user_message="This lesson cannot be published because it appears to encourage self-harm.",
        admin_message="Local rule matched Turkish self-harm encouragement.",
    ),
    LocalRule(
        category="violence",
        severity="critical",
        decision="block",
        confidence=0.95,
        patterns=(
            r"\bi will kill you\b",
            r"\bkill you\b",
            r"\bmurder you\b",
            r"\bshoot you\b",
            r"\bstab you\b",
            r"\bbeat you to death\b",
            r"\bdeserve to die\b",
        ),
        user_message="This lesson cannot be published because it appears to contain a direct violent threat.",
        admin_message="Local rule matched direct violent threat wording.",
    ),
    LocalRule(
        category="violence",
        severity="critical",
        decision="block",
        confidence=0.95,
        patterns=(
            r"\b(?:seni|onu|sizi|hepinizi)\s+oldurecegim\b",
            r"\b(?:seni|onu|sizi|hepinizi)\s+oldurucem\b",
            r"\b(?:seni|onu|sizi|hepinizi)\s+vuracagim\b",
            r"\b(?:seni|onu|sizi|hepinizi)\s+vurucam\b",
            r"\b(?:sana|ona|size|bu gruba)\s+saldiracagiz\b",
            r"\bbu gruba saldiracagim\b",
        ),
        user_message="This lesson cannot be published because it appears to contain a direct violent threat.",
        admin_message="Local rule matched Turkish direct violent threat wording.",
    ),
    LocalRule(
        category="dangerous_instruction",
        severity="critical",
        decision="block",
        confidence=0.93,
        patterns=(
            r"\bhow to make a bomb\b",
            r"\bbuild a bomb\b",
            r"\bmake an explosive\b",
            r"\bmake explosives\b",
            r"\bmake a firearm\b",
        ),
        user_message="This lesson cannot be published because it appears to contain dangerous instructions.",
        admin_message="Local rule matched dangerous-instruction wording.",
    ),
    LocalRule(
        category="dangerous_instruction",
        severity="critical",
        decision="block",
        confidence=0.92,
        patterns=(
            r"\bbomba nasil yapilir\b",
            r"\bbomba yapimi\b",
            r"\bpatlayici nasil yapilir\b",
            r"\bsilah nasil yapilir\b",
        ),
        user_message="This lesson cannot be published because it appears to contain dangerous instructions.",
        admin_message="Local rule matched Turkish dangerous-instruction wording.",
    ),
    LocalRule(
        category="illegal_activity",
        severity="high",
        decision="block",
        confidence=0.9,
        patterns=(
            r"\bsell cocaine\b",
            r"\bbuy cocaine\b",
            r"\bsell heroin\b",
            r"\bbuy heroin\b",
            r"\bfake passport\b",
            r"\bstolen credit card\b",
        ),
        user_message="This lesson cannot be published because it appears to promote illegal activity.",
        admin_message="Local rule matched illegal-activity wording.",
    ),
    LocalRule(
        category="illegal_activity",
        severity="high",
        decision="block",
        confidence=0.88,
        patterns=(
            r"\bkokain sat\b",
            r"\beroin sat\b",
            r"\bsahte pasaport\b",
            r"\bcalinti kredi karti\b",
        ),
        user_message="This lesson cannot be published because it appears to promote illegal activity.",
        admin_message="Local rule matched Turkish illegal-activity wording.",
    ),
    LocalRule(
        category="sexual",
        severity="high",
        decision="block",
        confidence=0.9,
        patterns=(
            r"\bexplicit sexual\b",
            r"\bpornographic\b",
            r"\bsexual assault instructions\b",
        ),
        user_message="This lesson cannot be published yet because one or more parts may contain sexual content.",
        admin_message="Local rule matched explicit sexual wording.",
        downgrade_in_educational_context=True,
    ),
    LocalRule(
        category="sexual",
        severity="high",
        decision="block",
        confidence=0.88,
        patterns=(
            r"\bpornografik\b",
            r"\bmustehcen\b",
            r"\bacik cinsel\b",
            r"\bcinsel saldiri talimatlari\b",
        ),
        user_message="This lesson cannot be published yet because one or more parts may contain sexual content.",
        admin_message="Local rule matched Turkish explicit sexual wording.",
        downgrade_in_educational_context=True,
    ),
    LocalRule(
        category="profanity",
        severity="high",
        decision="block",
        confidence=0.9,
        patterns=(
            r"\bfuck(?:ing|ed|er|s)?\b",
            r"\bshit(?:ty)?\b",
            r"\bbitch(?:es)?\b",
            r"\basshole(?:s)?\b",
            r"\bbastard(?:s)?\b",
        ),
        user_message="This lesson contains inappropriate language. Please remove or replace the highlighted words before publishing.",
        admin_message="Local rule matched profanity.",
        downgrade_in_educational_context=True,
    ),
    LocalRule(
        category="profanity",
        severity="high",
        decision="block",
        confidence=0.88,
        patterns=(
            r"\bkahrolasi\b",
            r"\bkahrolsun\b",
            r"\blanet olasi\b",
            r"\bbok(?:tan)?\b",
        ),
        user_message="This lesson contains inappropriate language. Please remove or replace the highlighted words before publishing.",
        admin_message="Local rule matched Turkish profanity.",
        downgrade_in_educational_context=True,
    ),
    LocalRule(
        category="hate_or_harassment",
        severity="high",
        decision="block",
        confidence=0.88,
        patterns=(
            r"\ball\s+[a-z][a-z\s-]{2,40}\s+are\s+vermin\b",
            r"\ball\s+[a-z][a-z\s-]{2,40}\s+should\s+be\s+eradicated\b",
        ),
        user_message="This lesson may contain hate or harassment and needs revision before publishing.",
        admin_message="Local rule matched conservative hate/harassment placeholder pattern.",
        downgrade_in_educational_context=True,
    ),
    LocalRule(
        category="hate_or_harassment",
        severity="high",
        decision="block",
        confidence=0.88,
        patterns=(
            r"\bbu grup tamamen yok olmali\b",
            r"\b(?:tum|butun)\s+[a-z][a-z\s-]{2,40}\s+yok olmali\b",
            r"\b(?:tum|butun)\s+[a-z][a-z\s-]{2,40}\s+temizlenmeli\b",
        ),
        user_message="This lesson may contain hate or harassment and needs revision before publishing.",
        admin_message="Local rule matched conservative Turkish group-targeting harm pattern.",
        downgrade_in_educational_context=True,
    ),
    LocalRule(
        category="political_or_targeted_abuse",
        severity="medium",
        decision="needs_admin_review",
        confidence=0.72,
        patterns=(
            r"\ball\s+[a-z][a-z\s-]{2,40}\s+voters\s+are\s+traitors\b",
            r"\b[a-z][a-z\s-]{2,40}\s+supporters\s+are\s+enemies\b",
        ),
        user_message="This lesson may contain targeted political or inflammatory content and needs review.",
        admin_message="Local rule matched conservative political/targeted-abuse placeholder pattern.",
    ),
    LocalRule(
        category="political_or_targeted_abuse",
        severity="medium",
        decision="needs_admin_review",
        confidence=0.72,
        patterns=(
            r"\bbu insanlar yuzunden herkes zarar goruyor\b",
            r"\bbu grup yuzunden herkes zarar goruyor\b",
            r"\b(?:tum|butun)\s+[a-z][a-z\s-]{2,40}\s+hain(?:dir)?\b",
        ),
        user_message="This lesson may contain targeted political or inflammatory content and needs review.",
        admin_message="Local rule matched conservative Turkish targeted-abuse pattern.",
    ),
    LocalRule(
        category="hate_or_harassment",
        severity="medium",
        decision="needs_admin_review",
        confidence=0.72,
        patterns=(
            r"\basagilik insanlar\b",
            r"\bgeri zekali(?:lar)?\b",
        ),
        user_message="This lesson may contain demeaning or harassing language and needs review.",
        admin_message="Local rule matched conservative Turkish demeaning-language pattern.",
        downgrade_in_educational_context=True,
    ),
    LocalRule(
        category="violence",
        severity="medium",
        decision="needs_admin_review",
        confidence=0.72,
        patterns=(
            r"\bmassacre\b",
            r"\bgenocide\b",
            r"\btorture\b",
            r"\bexecution\b",
            r"\bexecutions\b",
            r"\bwar crimes\b",
            r"\bkilled civilians\b",
            r"\bgraphic violence\b",
            r"\bbeheading\b",
        ),
        user_message="The AI could not confidently classify this content. If it is educational, historical, medical, or documentary, you may ask an admin to review it.",
        admin_message="Local rule matched ambiguous violent content.",
    ),
)


class LocalRulesProvider(TextModerationProvider):
    provider_name = "local_rules"

    def scan_text(self, text: str, location: FindingLocation) -> list[AgentFindingSchema]:
        clean_text = _normalize_text(text)
        if not clean_text:
            return []

        match_text = _normalize_text_for_matching(clean_text)
        educational_context = _has_educational_context(match_text)
        findings: list[AgentFindingSchema] = []
        seen: set[tuple[str, str, int, int]] = set()
        for rule in LOCAL_RULES:
            for pattern in rule.patterns:
                match = re.search(pattern, match_text, flags=re.IGNORECASE)
                if match is None:
                    continue
                key = (rule.category, pattern, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(_finding_for_match(rule, clean_text, location, match, educational_context))
                break
        return findings


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_text_for_matching(value: str) -> str:
    folded = str(value or "").translate(TURKISH_ASCII_TRANSLATION).casefold()
    return re.sub(r"\s+", " ", folded).strip()


def _has_educational_context(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in EDUCATIONAL_CONTEXT_PATTERNS)


def _finding_for_match(
    rule: LocalRule,
    text: str,
    location: FindingLocation,
    match: re.Match[str],
    educational_context: bool,
) -> AgentFindingSchema:
    decision = rule.decision
    severity = rule.severity
    confidence = rule.confidence
    user_message = rule.user_message
    admin_message = rule.admin_message
    if educational_context and rule.downgrade_in_educational_context and rule.decision == "block":
        decision = "needs_admin_review"
        severity = "medium"
        confidence = min(rule.confidence, 0.74)
        user_message = (
            "The AI could not confidently classify this content. If it is educational, "
            "historical, medical, or documentary, you may ask an admin to review it."
        )
        admin_message = f"{rule.admin_message} Educational context detected; downgraded to admin review."

    return AgentFindingSchema(
        category=rule.category,
        severity=severity,
        confidence=confidence,
        decision=decision,
        location=location.model_copy(
            update={
                "start_char": match.start(),
                "end_char": match.end(),
            }
        ),
        user_message=user_message,
        admin_message=admin_message,
        evidence_excerpt=_excerpt(text, match.start(), match.end()),
    )


def _excerpt(text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    excerpt = text[left:right].strip()
    return excerpt[:220]
