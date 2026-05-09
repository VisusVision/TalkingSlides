from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_merge_remove_render_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="avatar_processing_status",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("queued", "Queued"),
                    ("processing", "Processing"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="avatar_processing_message",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="project",
            name="avatar_last_job_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="project",
            name="avatar_visible",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="project",
            name="avatar_output_path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="project",
            name="avatar_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
