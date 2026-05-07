from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_job_cancelled_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobActionAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "action",
                    models.CharField(
                        choices=[("cancel_requested", "Cancel Requested"), ("cancel_rejected", "Cancel Rejected")],
                        max_length=40,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="job_action_audits",
                        to="auth.user",
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="action_audits", to="core.job"),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="job_action_audits",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="jobactionaudit",
            index=models.Index(fields=["project", "action", "created_at"], name="core_jobact_project_658cd4_idx"),
        ),
        migrations.AddIndex(
            model_name="jobactionaudit",
            index=models.Index(fields=["actor", "created_at"], name="core_jobact_actor_i_89ed75_idx"),
        ),
    ]
