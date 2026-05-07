from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_jobcheckpoint"),
    ]

    operations = [
        migrations.AlterField(
            model_name="job",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("done", "Done"),
                    ("cancelled", "Cancelled"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
