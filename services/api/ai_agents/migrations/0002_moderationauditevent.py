from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("ai_agents", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ModerationAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("approve", "Approve"),
                            ("block", "Block"),
                            ("needs_review", "Needs review"),
                            ("request_changes", "Request changes"),
                            ("add_note", "Add note"),
                            ("rescan", "Rescan"),
                        ],
                        max_length=40,
                    ),
                ),
                ("reason", models.TextField(blank=True)),
                ("previous_status", models.CharField(blank=True, max_length=30)),
                ("new_status", models.CharField(blank=True, max_length=30)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="moderation_audit_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="moderation_audit_events",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["project", "-created_at"], name="ai_agents_m_project_4f4596_idx"),
                    models.Index(fields=["action", "-created_at"], name="ai_agents_m_action_55bb62_idx"),
                ],
            },
        ),
    ]
