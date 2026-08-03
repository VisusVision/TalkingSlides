from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_digital_twin_v2"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="avatar_preview_quality_report",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
