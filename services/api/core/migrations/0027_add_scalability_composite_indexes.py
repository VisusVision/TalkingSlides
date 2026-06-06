from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_notification"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="project",
            index=models.Index(
                fields=["is_published", "status", "moderation_status", "-created_at"],
                name="c_proj_pub_stat_mod_cr_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="project",
            index=models.Index(
                fields=["user", "-created_at"],
                name="c_proj_user_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(
                fields=["project", "job_type", "status", "-created_at"],
                name="c_job_proj_type_stat_cr_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(
                fields=["project", "-created_at"],
                name="c_job_proj_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="lessonprogress",
            index=models.Index(
                fields=["project", "-updated_at"],
                name="c_lp_proj_updated_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="lessonprogress",
            index=models.Index(
                fields=["user", "-updated_at"],
                name="c_lp_user_updated_idx",
            ),
        ),
    ]
