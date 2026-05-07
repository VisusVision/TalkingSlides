from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_project_render_profile"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="job",
            constraint=models.UniqueConstraint(
                condition=Q(request_id__gt=""),
                fields=("project", "job_type", "request_id"),
                name="uq_job_project_type_request_id_nonempty",
            ),
        ),
    ]
