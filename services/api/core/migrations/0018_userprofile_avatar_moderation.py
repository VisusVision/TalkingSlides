from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_merge_subtitle_translation_moderation"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="avatar_moderation_status",
            field=models.CharField(default="not_scanned", max_length=30),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_moderation_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_last_moderation_run_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
