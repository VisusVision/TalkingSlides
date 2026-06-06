from django.conf import settings
from django.db import models


class AgentDefinition(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=50)
    modality = models.CharField(max_length=50)
    version = models.CharField(max_length=50)
    enabled = models.BooleanField(default=True)
    is_blocking = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return f"{self.slug}@{self.version}"


class AgentRun(models.Model):
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="agent_runs",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="triggered_agent_runs",
    )
    purpose = models.CharField(max_length=50)
    phase = models.CharField(max_length=50)
    status = models.CharField(max_length=30)
    final_decision = models.CharField(max_length=30, blank=True)
    policy_version = models.CharField(max_length=50, default="moderation:v1")
    input_hash = models.CharField(max_length=64, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "purpose", "status"]),
            models.Index(fields=["phase", "status"]),
        ]

    def __str__(self):
        return f"AgentRun project={self.project_id} purpose={self.purpose} status={self.status}"


class AgentFinding(models.Model):
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    agent_slug = models.CharField(max_length=100)
    agent_version = models.CharField(max_length=50)
    content_type = models.CharField(max_length=50)
    object_type = models.CharField(max_length=50, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    location = models.JSONField(default=dict, blank=True)
    category = models.CharField(max_length=80)
    severity = models.CharField(max_length=30)
    confidence = models.FloatField(default=0.0)
    decision = models.CharField(max_length=30)
    user_message = models.TextField(blank=True)
    admin_message = models.TextField(blank=True)
    evidence_excerpt = models.TextField(blank=True)
    provider = models.CharField(max_length=80, blank=True)
    provider_raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["run", "decision"]),
            models.Index(fields=["category", "severity"]),
            models.Index(fields=["content_type", "object_type"]),
        ]

    def __str__(self):
        return f"{self.agent_slug} {self.decision} {self.category}"


class PublicationBlockEvent(models.Model):
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="publication_blocks",
    )
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="publication_block_events",
    )
    blocked_by = models.CharField(max_length=100)
    reason_category = models.CharField(max_length=80)
    highest_severity = models.CharField(max_length=30)
    message_to_user = models.TextField()
    message_to_admin = models.TextField(blank=True)
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_publication_blocks",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "resolved"]),
            models.Index(fields=["reason_category", "highest_severity"]),
        ]

    def __str__(self):
        return f"PublicationBlockEvent project={self.project_id} category={self.reason_category}"


class AdminReviewRequest(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("closed", "Closed"),
    ]

    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="admin_review_requests",
    )
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_review_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_review_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_reviews_done",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="open")
    publisher_message = models.TextField(blank=True)
    admin_response = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["requested_by", "status"]),
        ]

    def __str__(self):
        return f"AdminReviewRequest project={self.project_id} status={self.status}"


class ModerationReport(models.Model):
    CATEGORY_CHOICES = [
        ("inappropriate_content", "Inappropriate content"),
        ("wrong_information", "Wrong information"),
        ("copyright", "Copyright or ownership concern"),
        ("technical_problem", "Technical problem"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("reviewed", "Reviewed"),
        ("resolved", "Resolved"),
        ("dismissed", "Dismissed"),
    ]

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderation_reports",
    )
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="moderation_reports",
    )
    publisher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="publisher_moderation_reports",
    )
    admin_review_request = models.ForeignKey(
        AdminReviewRequest,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderation_reports",
    )
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderation_reports_reviewed",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["project", "status"], name="ai_ag_r_proj_status_idx"),
            models.Index(fields=["reporter", "project", "category", "-created_at"], name="ai_ag_r_reporter_proj_idx"),
            models.Index(fields=["publisher", "status", "-created_at"], name="ai_ag_r_pub_status_idx"),
        ]

    def __str__(self):
        return f"ModerationReport project={self.project_id} category={self.category} status={self.status}"


class ModerationAuditEvent(models.Model):
    ACTION_CHOICES = [
        ("approve", "Approve"),
        ("block", "Block"),
        ("needs_review", "Needs review"),
        ("request_changes", "Request changes"),
        ("add_note", "Add note"),
        ("rescan", "Rescan"),
    ]

    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="moderation_audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderation_audit_events",
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    reason = models.TextField(blank=True)
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "-created_at"], name="ai_agents_m_project_4f4596_idx"),
            models.Index(fields=["action", "-created_at"], name="ai_agents_m_action_55bb62_idx"),
        ]

    def __str__(self):
        return f"ModerationAuditEvent project={self.project_id} action={self.action}"
