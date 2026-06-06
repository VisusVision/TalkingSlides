from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_project_tts_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="transcriptpage",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="transcriptpage",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
