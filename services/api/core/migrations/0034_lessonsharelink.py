from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0033_userprofile_profile_asset_moderation"),
    ]

    operations = [
        migrations.CreateModel(
            name="LessonShareLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("revoked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_accessed_at", models.DateTimeField(blank=True, null=True)),
                ("access_count", models.PositiveIntegerField(default=0)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lesson_share_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share_links",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="lessonsharelink",
            index=models.Index(fields=["project", "-created_at"], name="c_lsl_project_created_idx"),
        ),
        migrations.AddIndex(
            model_name="lessonsharelink",
            index=models.Index(fields=["owner", "-created_at"], name="c_lsl_owner_created_idx"),
        ),
        migrations.AddIndex(
            model_name="lessonsharelink",
            index=models.Index(fields=["project", "revoked_at", "expires_at"], name="c_lsl_project_active_idx"),
        ),
    ]
