from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_job_idempotency_unique_constraint"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobCheckpoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("stage_name", models.CharField(max_length=80)),
                (
                    "stage_status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "job",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="checkpoints", to="core.job"),
                ),
            ],
            options={
                "ordering": ["job", "created_at", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="jobcheckpoint",
            constraint=models.UniqueConstraint(fields=("job", "stage_name"), name="uq_job_checkpoint_stage"),
        ),
        migrations.AddIndex(
            model_name="jobcheckpoint",
            index=models.Index(fields=["job", "stage_status"], name="core_jobche_job_id_e4fc60_idx"),
        ),
        migrations.AddIndex(
            model_name="jobcheckpoint",
            index=models.Index(fields=["updated_at"], name="core_jobche_updated_5b6f3f_idx"),
        ),
    ]
