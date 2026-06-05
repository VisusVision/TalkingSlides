from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_renderfollowupintent_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="banner_image_moderation_status",
            field=models.CharField(default="not_scanned", max_length=30),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="banner_image_moderation_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="banner_image_pending_original",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="banner_image_pending_processed",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="logo_image_moderation_status",
            field=models.CharField(default="not_scanned", max_length=30),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="logo_image_moderation_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="logo_image_pending_original",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="logo_image_pending_processed",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
