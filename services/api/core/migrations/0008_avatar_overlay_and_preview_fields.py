from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_avatar_pipeline_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="avatar_enabled_override",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_engine_fallback",
            field=models.CharField(default="sadtalker,wav2lip", max_length=40),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_engine_primary",
            field=models.CharField(default="liveportrait", max_length=40),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_last_preview_job_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_last_preview_path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_last_preview_status",
            field=models.CharField(default="idle", max_length=30),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_overlay_default_position",
            field=models.CharField(default="top-right", max_length=40),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_overlay_size",
            field=models.CharField(default="medium", max_length=30),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_overlay_visible",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_version_hash",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.CreateModel(
            name="AvatarOverlayPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("anchor", models.CharField(choices=[("top-right", "Top Right"), ("top-left", "Top Left"), ("bottom-right", "Bottom Right"), ("bottom-left", "Bottom Left"), ("custom", "Custom")], default="top-right", max_length=20)),
                ("x_percent", models.FloatField(default=72.0)),
                ("y_percent", models.FloatField(default=8.0)),
                ("width_percent", models.FloatField(default=24.0)),
                ("visible", models.BooleanField(default=True)),
                ("pinned", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="avatar_overlay_preferences", to="core.project")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="avatar_overlay_preferences", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "unique_together": {("user", "lesson")},
            },
        ),
    ]
