from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_video_avatar_and_lesson_segments"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="cover_image_original",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="project",
            name="cover_image_processed",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
