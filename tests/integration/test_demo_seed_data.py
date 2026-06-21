import io
import os
import sys
from pathlib import Path

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db.models import Avg  # noqa: E402

from ai_agents.models import AdminReviewRequest  # noqa: E402
from core.management.commands import seed_demo_data  # noqa: E402
from core.models import LessonComment, LessonLike, LessonProgress, Project, TranscriptPage  # noqa: E402


pytestmark = pytest.mark.django_db
FAKE_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2demo-video"


@pytest.fixture(autouse=True)
def _stub_demo_video_generation(monkeypatch):
    monkeypatch.setattr(seed_demo_data, "_generate_demo_video_bytes", lambda: FAKE_MP4)


def _seed(*args: str) -> str:
    output = io.StringIO()
    call_command("seed_demo_data", *args, stdout=output)
    return output.getvalue()


def test_seed_demo_data_creates_demo_users():
    output = _seed("--reset-demo", "--with-analytics-activity")

    jane = User.objects.get(email="jane.doe.demo@example.com")
    ahmet = User.objects.get(email="ahmet.yilmaz.demo@example.com")
    staff = User.objects.get(email="demo.staff@example.com")

    assert jane.check_password("visus-demo-local")
    assert jane.profile.role == "publisher"
    assert jane.profile.display_name == "Jane Doe"
    assert jane.profile.bio == "Biology and academic writing instructor."
    assert ahmet.profile.display_name == "Ahmet Yılmaz"
    assert ahmet.profile.bio == "Turkish STEM educator."
    assert staff.is_staff is True
    assert staff.profile.role == "teacher"
    assert "Local demo password for all demo accounts" in output


def test_seed_demo_data_creates_realistic_non_placeholder_lessons():
    _seed("--reset-demo")

    long_lesson = Project.objects.get(title="Cell Structure and Organelles")
    assert long_lesson.is_published is True
    assert long_lesson.status == "ready"
    assert long_lesson.moderation_status == "approved"
    assert long_lesson.jobs.filter(job_type="video_export", status="done").exists()
    assert TranscriptPage.objects.filter(project=long_lesson, is_active=True).count() >= 30
    combined = " ".join(
        TranscriptPage.objects.filter(project=long_lesson).values_list("narration_text", flat=True)
    )
    assert "mitochondria" in combined.lower()
    assert "ribosomes" in combined.lower()
    assert "placeholder" not in combined.lower()

    turkish = Project.objects.get(title="Bitkilerde Fotosentez ve Enerji Üretimi")
    turkish_text = " ".join(turkish.transcript_pages.values_list("narration_text", flat=True))
    assert "Fotosentez" in turkish.title
    assert "klorofil" in turkish_text.lower()
    assert turkish.moderation_summary["demo_seed"]["language"] == "tr"

    poor = Project.objects.get(title="Vague Notes About Databases")
    assert poor.transcript_pages.count() == 8
    assert "Tables store data" in poor.transcript_pages.order_by("order").first().narration_text


def test_seed_demo_data_is_idempotent():
    _seed("--reset-demo", "--with-moderation-fixtures")
    counts = {
        "users": User.objects.filter(email__endswith=".demo@example.com").count(),
        "projects": Project.objects.count(),
        "pages": TranscriptPage.objects.count(),
        "progress": LessonProgress.objects.count(),
        "likes": LessonLike.objects.count(),
        "comments": LessonComment.objects.count(),
    }

    _seed("--with-moderation-fixtures")

    assert User.objects.filter(email__endswith=".demo@example.com").count() == counts["users"]
    assert Project.objects.count() == counts["projects"]
    assert TranscriptPage.objects.count() == counts["pages"]
    assert LessonProgress.objects.count() == counts["progress"]
    assert LessonLike.objects.count() == counts["likes"]
    assert LessonComment.objects.count() == counts["comments"]


def test_seed_demo_data_creates_meaningful_analytics_activity():
    _seed("--reset-demo", "--with-analytics-activity")

    strong = Project.objects.get(title="How to Write a Strong Academic Abstract")
    vague = Project.objects.get(title="Vague Notes About Databases")
    neural = Project.objects.get(title="Introduction to Neural Network Optimization")

    assert strong.progress_records.aggregate(avg=Avg("progress_pct"))["avg"] >= 80
    assert vague.progress_records.aggregate(avg=Avg("progress_pct"))["avg"] <= 55
    assert neural.progress_records.aggregate(avg=Avg("progress_pct"))["avg"] <= 55
    assert strong.likes.count() >= 2
    assert vague.likes.count() == 0
    assert LessonComment.objects.filter(
        project=vague,
        text__icontains="database relationships make sense",
    ).exists()
    assert LessonComment.objects.filter(
        project=neural,
        text__icontains="learning rate with a simple analogy",
    ).exists()


def test_seed_demo_data_creates_safe_moderation_fixtures():
    output = _seed("--reset-demo", "--with-moderation-fixtures", "--run-moderation")

    fixtures = [
        project
        for project in Project.objects.all()
        if project.moderation_summary.get("demo_seed", {}).get("moderation_fixture")
    ]
    assert len(fixtures) == 7
    all_text = " ".join(
        TranscriptPage.objects.filter(project__in=fixtures).values_list("narration_text", flat=True)
    ).lower()

    assert "how to commit suicide" not in all_text
    assert "how to make a bomb" not in all_text
    assert "build a bomb" not in all_text
    assert "weapon-making" not in all_text
    assert "sexual assault instructions" not in all_text
    assert "graphic violence" not in all_text
    assert "explicit sexual content placeholder only" in all_text
    assert Project.objects.filter(title="Moderation Test: OCR Text Image").exists()
    assert "Moderation smoke results" in output
    assert AdminReviewRequest.objects.filter(project__in=fixtures, status="open").exists()
    assert "review=" in output
