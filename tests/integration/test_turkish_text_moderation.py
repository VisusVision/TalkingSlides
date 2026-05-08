# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings

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

from ai_agents.models import AgentFinding  # noqa: E402
from core.models import Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, title: str = "Turkish moderation lesson", description: str = "") -> Project:
    return Project.objects.create(
        title=title,
        description=description,
        user=_make_teacher(username),
        status="ready",
    )


def _add_page(project: Project, text: str, page_key: str = "slide-1") -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key=page_key,
        original_text=text,
        narration_text="",
    )


def _run(project: Project) -> dict:
    return ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)


@pytest.mark.django_db
def test_turkish_profanity_creates_finding_and_revision_required():
    project = _make_project(
        "turkish_profanity_teacher",
        description="Bu kahrolası teknolojiler yüzünden insanlar ölüyor!",
    )

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"
    finding = AgentFinding.objects.get(run__project=project)
    assert finding.category == "profanity"
    assert finding.decision == "block"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text",
    [
        "Seni öldüreceğim.",
        "Onu vuracağım.",
        "Bu gruba saldıracağız.",
    ],
)
def test_turkish_direct_violent_threat_blocks(text):
    project = _make_project("turkish_threat_teacher")
    _add_page(project, text)

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"
    assert AgentFinding.objects.filter(run__project=project, category="violence", decision="block").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text",
    [
        "Osmanlı tarihinde savaşlar ve ölümler yaşanmıştır.",
        "Bu derste şiddetin toplum üzerindeki etkilerini inceleyeceğiz.",
        "Trafik kazalarında ölümler nasıl azaltılır?",
    ],
)
def test_turkish_educational_violence_context_is_allowed(text):
    project = _make_project("turkish_educational_teacher", title="Tarih ve toplum")
    _add_page(project, text)

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "allow"
    assert project.moderation_status == "approved"
    assert AgentFinding.objects.filter(run__project=project).count() == 0


@pytest.mark.django_db
def test_turkish_ambiguous_harmful_rhetoric_needs_admin_review():
    project = _make_project("turkish_review_teacher")
    _add_page(project, "Bu insanlar yüzünden herkes zarar görüyor.")

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "needs_admin_review"
    assert project.moderation_status == "needs_admin_review"
    finding = AgentFinding.objects.get(run__project=project)
    assert finding.category == "political_or_targeted_abuse"
    assert finding.decision == "needs_admin_review"


@pytest.mark.django_db
def test_turkish_group_elimination_statement_blocks_or_requires_revision():
    project = _make_project("turkish_group_harm_teacher")
    _add_page(project, "Bu grup tamamen yok olmalı.")

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"
    finding = AgentFinding.objects.get(run__project=project)
    assert finding.category == "hate_or_harassment"
    assert finding.decision == "block"


@pytest.mark.django_db
@pytest.mark.parametrize("text", ["SENİ ÖLDÜRECEĞİM.", "Seni oldurecegim."])
def test_turkish_threat_normalization_handles_case_diacritics_and_ascii(text):
    project = _make_project("turkish_normalization_teacher")
    _add_page(project, text)

    _run(project)

    project.refresh_from_db()
    assert project.moderation_status == "revision_required"
    assert AgentFinding.objects.filter(run__project=project, category="violence", decision="block").exists()


@pytest.mark.django_db
def test_auto_source_moderation_uses_turkish_rules(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    project = Project.objects.create(
        title="Turkish auto source",
        user=_make_teacher("turkish_auto_source_teacher"),
        status="processing",
    )
    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "slide_num": 1,
                "page_key": "slide-1",
                "notes_text": "Seni oldurecegim.",
                "original_text": "Seni oldurecegim.",
                "narration_text": "Seni oldurecegim.",
            }
        ],
    )

    result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

    project.refresh_from_db()
    assert result["moderation_status"] == "revision_required"
    assert project.moderation_status == "revision_required"
    assert AgentFinding.objects.filter(run__project=project, category="violence").exists()
