from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_userprofile_publisher_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="TranslatedSubtitleTrack",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=16)),
                ("language_label", models.CharField(blank=True, max_length=80)),
                ("source_language_code", models.CharField(blank=True, max_length=16)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("mock", "Mock"),
                            ("deepl", "DeepL"),
                            ("google", "Google"),
                            ("openai", "OpenAI"),
                        ],
                        default="mock",
                        max_length=40,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("srt_path", models.CharField(blank=True, max_length=500)),
                ("vtt_path", models.CharField(blank=True, max_length=500)),
                ("cue_count", models.PositiveIntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="translated_subtitle_tracks",
                        to="core.job",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translated_subtitle_tracks",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ["project", "language_code"],
            },
        ),
        migrations.AddConstraint(
            model_name="translatedsubtitletrack",
            constraint=models.UniqueConstraint(
                fields=("project", "language_code"),
                name="unique_subtitle_track_language_per_project",
            ),
        ),
        migrations.AddIndex(
            model_name="translatedsubtitletrack",
            index=models.Index(fields=["project", "status"], name="core_transl_project_af4d58_idx"),
        ),
        migrations.AddIndex(
            model_name="translatedsubtitletrack",
            index=models.Index(fields=["language_code"], name="core_transl_languag_103c36_idx"),
        ),
    ]
