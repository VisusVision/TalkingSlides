from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_transcriptpage_soft_delete"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("teacher", "Teacher"),
                    ("publisher", "Publisher"),
                    ("student", "Student"),
                ],
                default="student",
                max_length=20,
            ),
        ),
    ]
