from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_transcriptpage_editor_document"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="avatar_consent_confirmed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_image_original",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_image_processed",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_image_status",
            field=models.CharField(default="idle", max_length=30),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_last_rendered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_lipsync_engine",
            field=models.CharField(default="musetalk", max_length=40),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_model_version",
            field=models.CharField(default="liveportrait+musetalk:v1", max_length=80),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_motion_preset",
            field=models.CharField(default="natural", max_length=40),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_preview_video",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_quality_preset",
            field=models.CharField(default="high", max_length=40),
        ),
        migrations.AlterField(
            model_name="job",
            name="job_type",
            field=models.CharField(
                choices=[
                    ("tts_render", "TTS Render"),
                    ("video_export", "Video Export"),
                    ("pptx_export", "PPTX Export"),
                    ("sync", "Data Sync"),
                    ("avatar_preprocess", "Avatar Preprocess"),
                    ("avatar_render", "Avatar Render"),
                ],
                max_length=50,
            ),
        ),
        migrations.CreateModel(
            name="AvatarRenderJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("avatar_version", models.CharField(default="liveportrait+musetalk:v1", max_length=80)),
                ("source_image_hash", models.CharField(max_length=64)),
                ("tts_audio_hash", models.CharField(max_length=64)),
                ("lesson_text_hash", models.CharField(blank=True, max_length=64)),
                ("slide_hash", models.CharField(blank=True, max_length=64)),
                ("engine_used", models.CharField(default="none", max_length=40)),
                (
                    "render_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("done", "Done"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("render_error", models.TextField(blank=True)),
                ("output_path", models.CharField(blank=True, max_length=500)),
                ("fallback_chain_used", models.JSONField(blank=True, default=list)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "lesson",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="avatar_render_jobs", to="core.project"),
                ),
                (
                    "teacher",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="avatar_render_jobs", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="avatarrenderjob",
            index=models.Index(fields=["lesson", "teacher", "render_status"], name="core_avatar_lesson__547222_idx"),
        ),
        migrations.AddIndex(
            model_name="avatarrenderjob",
            index=models.Index(fields=["source_image_hash", "tts_audio_hash"], name="core_avatar_source__d78f8c_idx"),
        ),
    ]
