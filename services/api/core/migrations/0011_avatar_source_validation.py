from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_project_cover_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="avatar_source_valid",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_source_validation_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_source_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_source_image_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_source_video_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_source_reference_type",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_preview_source_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_preview_stale",
            field=models.BooleanField(default=False),
        ),
    ]
