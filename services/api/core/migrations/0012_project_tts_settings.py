from django.db import migrations, models
import core.models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_avatar_source_validation"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="tts_settings",
            field=models.JSONField(blank=True, default=core.models.default_project_tts_settings),
        ),
    ]
