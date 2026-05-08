from __future__ import annotations

from collections.abc import Iterable

from .schemas import AgentFindingSchema, AgentResultSchema, Decision, Severity


SEVERITY_RANK: dict[str, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

PROJECT_STATUS_BY_DECISION: dict[str, str] = {
    "allow": "approved",
    "warn": "approved",
    "needs_admin_review": "needs_admin_review",
    "block": "revision_required",
}


class PolicyEngine:
    block_confidence_threshold = 0.85

    def combine_findings(self, findings: Iterable[AgentFindingSchema]) -> Decision:
        finding_list = list(findings)
        if any(
            finding.decision == "block" and finding.confidence >= self.block_confidence_threshold
            for finding in finding_list
        ):
            return "block"
        if any(finding.decision == "needs_admin_review" for finding in finding_list):
            return "needs_admin_review"
        if any(finding.decision == "block" for finding in finding_list):
            return "needs_admin_review"
        if any(finding.decision == "warn" for finding in finding_list):
            return "warn"
        return "allow"

    def combine_results(self, results: Iterable[AgentResultSchema]) -> Decision:
        findings: list[AgentFindingSchema] = []
        for result in results:
            findings.extend(result.findings)
            if result.decision != "allow" and not result.findings:
                findings.append(
                    AgentFindingSchema(
                        category="unknown",
                        severity="medium",
                        confidence=result.confidence,
                        decision=result.decision,
                        user_message="This lesson may require review before publishing.",
                    )
                )
        return self.combine_findings(findings)

    def project_status_for_decision(self, decision: Decision) -> str:
        return PROJECT_STATUS_BY_DECISION.get(decision, "failed")

    def highest_severity(self, findings: Iterable[AgentFindingSchema]) -> Severity:
        highest = "low"
        for finding in findings:
            if SEVERITY_RANK[finding.severity] > SEVERITY_RANK[highest]:
                highest = finding.severity
        return highest  # type: ignore[return-value]

    def highest_priority_finding(self, findings: Iterable[AgentFindingSchema]) -> AgentFindingSchema | None:
        finding_list = list(findings)
        if not finding_list:
            return None
        return sorted(
            finding_list,
            key=lambda finding: (
                SEVERITY_RANK[finding.severity],
                finding.confidence,
                1 if finding.decision == "block" else 0,
            ),
            reverse=True,
        )[0]
