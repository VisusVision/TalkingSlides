# Generated during VISUS VidLab to TalkingSlides developer sync.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_project_render_profile"),
        ("core", "0022_project_draft_data"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="project",
            name="render_profile",
        ),
    ]
