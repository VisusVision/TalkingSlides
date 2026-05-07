from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_userprofile_publisher_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="request_id",
            field=models.CharField(blank=True, db_index=True, max_length=120),
        ),
    ]

