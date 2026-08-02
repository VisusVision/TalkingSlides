from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_lessonsharelink"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="avatar_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_voice_source",
            field=models.CharField(default="existing", max_length=20),
        ),
    ]
