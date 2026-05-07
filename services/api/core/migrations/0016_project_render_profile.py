from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_job_request_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="render_profile",
            field=models.CharField(
                choices=[("fast", "Fast"), ("balanced", "Balanced"), ("quality", "Quality")],
                default="balanced",
                max_length=20,
            ),
        ),
    ]
