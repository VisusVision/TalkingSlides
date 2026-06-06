from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_avatar_overlay_and_preview_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="avatar_video_original",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_video_processed",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_reference_type",
            field=models.CharField(default="image", max_length=20),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_preview_error",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="LessonSegment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("segment_order", models.PositiveIntegerField(default=0)),
                ("segment_text", models.TextField(blank=True)),
                ("segment_slide_path", models.CharField(blank=True, max_length=500)),
                ("segment_tts_path", models.CharField(blank=True, max_length=500)),
                ("segment_avatar_path", models.CharField(blank=True, max_length=500)),
                ("segment_pause_seconds", models.FloatField(default=2.2)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("ready", "Ready"), ("failed", "Failed")],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="lesson_segments", to="core.project"),
                ),
            ],
            options={
                "ordering": ["project", "segment_order"],
                "unique_together": {("project", "segment_order")},
            },
        ),
    ]
