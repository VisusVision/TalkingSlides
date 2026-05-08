from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.management import get_commands, load_command_class
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


KNOWN_MODERATION_COMMANDS = [
    "run_moderation_scan",
    "create_moderation_review_request",
    "create_moderation_smoke_project",
    "run_visual_moderation_scan",
    "run_ocr_bridge",
    "sample_video_frames",
    "cleanup_video_frame_audit_files",
    "moderation_smoke_checklist",
    "moderation_system_status",
]


class Command(BaseCommand):
    help = "Report read-only operational diagnostics for the moderation system."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Output diagnostics as JSON.")
        parser.add_argument("--check-imports", action="store_true", help="Include detailed import check output.")

    def handle(self, *args, **options):
        status = collect_moderation_system_status(check_imports=bool(options.get("check_imports")))
        if options.get("json"):
            self.stdout.write(json.dumps(status, indent=2, sort_keys=True, default=str))
            return
        self._write_human(status=status, show_import_errors=bool(options.get("check_imports")))

    def _write_human(self, *, status: dict[str, Any], show_import_errors: bool) -> None:
        self.stdout.write("Moderation database")
        database = status["database"]
        self.stdout.write(f"  ai_agents app installed: {_yes_no(database['ai_agents_app_installed'])}")
        self.stdout.write(f"  models importable: {_yes_no(database['models_importable'])}")
        self.stdout.write(f"  Project moderation fields: {_yes_no(database['project_moderation_fields_exist'])}")
        self.stdout.write(f"  latest migrations: {_format_migrations(database['latest_migrations'])}")
        self.stdout.write(f"  moderation tables exist: {_yes_no(database['moderation_tables_exist'])}")
        if database.get("count_error"):
            self.stdout.write(f"  count error: {database['count_error']}")
        self.stdout.write(f"  AgentRuns: {database['agent_run_count']}")
        self.stdout.write(f"  AgentFindings: {database['agent_finding_count']}")
        self.stdout.write(f"  Open admin review requests: {database['open_admin_review_request_count']}")

        self.stdout.write("")
        self.stdout.write("Text providers")
        text = status["text_providers"]
        self.stdout.write(f"  local text rules available: {_yes_no(text['local_text_rules_available'])}")
        self.stdout.write(f"  Ollama enabled: {_yes_no(text['ollama_enabled'])}")
        self.stdout.write(f"  Ollama base URL: {text['ollama_base_url']}")
        self.stdout.write(f"  Ollama text model: {text['ollama_text_model']}")
        self.stdout.write(f"  Ollama timeout seconds: {text['ollama_timeout_seconds']}")

        self.stdout.write("")
        self.stdout.write("Visual/OCR/video providers")
        providers = status["visual_ocr_video_providers"]
        self.stdout.write(f"  visual provider interface available: {_yes_no(providers['visual_provider_interface_available'])}")
        self.stdout.write(f"  local image rules provider available: {_yes_no(providers['local_image_rules_provider_available'])}")
        self.stdout.write(f"  visual auto moderation enabled: {_yes_no(providers['visual_auto_enabled'])}")
        self.stdout.write(f"  visual block render on rejection: {_yes_no(providers['visual_block_render_on_rejection'])}")
        self.stdout.write(f"  visual block publish on rejection: {_yes_no(providers['visual_block_publish_on_rejection'])}")
        self.stdout.write(f"  visual moderation phase: {providers['visual_moderation_phase']}")
        self.stdout.write(f"  visual safety provider: {providers['visual_safety_provider']}")
        self.stdout.write(f"  visual safety classifier enabled: {_yes_no(providers['visual_safety_classifier_enabled'])}")
        self.stdout.write(f"  visual safety timeout seconds: {providers['visual_safety_timeout_seconds']}")
        self.stdout.write(f"  visual safety max image bytes: {providers['visual_safety_max_image_bytes']}")
        self.stdout.write(f"  Azure Content Safety enabled: {_yes_no(providers['azure_content_safety_enabled'])}")
        self.stdout.write(
            f"  Azure Content Safety endpoint configured: {_yes_no(providers['azure_content_safety_endpoint_configured'])}"
        )
        self.stdout.write(
            f"  Azure Content Safety key configured: {_yes_no(providers['azure_content_safety_key_configured'])}"
        )
        self.stdout.write(f"  Azure Content Safety API version: {providers['azure_content_safety_api_version']}")
        self.stdout.write(f"  Azure Content Safety categories: {', '.join(providers['azure_content_safety_categories'])}")
        self.stdout.write(f"  Azure Content Safety block severity: {providers['azure_content_safety_block_severity']}")
        self.stdout.write(f"  avatar image moderation auto enabled: {_yes_no(providers['avatar_image_moderation_auto_enabled'])}")
        self.stdout.write(f"  avatar image block on rejection: {_yes_no(providers['avatar_image_moderation_block_on_rejection'])}")
        self.stdout.write(f"  avatar image require approval: {_yes_no(providers['avatar_image_moderation_require_approval'])}")
        self.stdout.write(f"  OCR bridge provider available: {_yes_no(providers['ocr_bridge_provider_available'])}")
        self.stdout.write(f"  OCR auto moderation enabled: {_yes_no(providers['ocr_auto_enabled'])}")
        self.stdout.write(f"  OCR block render on rejection: {_yes_no(providers['ocr_block_render_on_rejection'])}")
        self.stdout.write(f"  OCR moderation phase: {providers['ocr_moderation_phase']}")
        self.stdout.write(f"  OCR moderation provider: {providers['ocr_moderation_provider']}")
        self.stdout.write(f"  Azure OCR enabled: {_yes_no(providers['azure_ocr_enabled'])}")
        self.stdout.write(f"  Azure OCR endpoint configured: {_yes_no(providers['azure_ocr_endpoint_configured'])}")
        self.stdout.write(f"  Azure OCR key configured: {_yes_no(providers['azure_ocr_key_configured'])}")
        self.stdout.write(f"  Azure OCR model: {providers['azure_ocr_model']}")
        self.stdout.write(f"  Azure OCR API version: {providers['azure_ocr_api_version']}")
        self.stdout.write(f"  video frame sampling helper available: {_yes_no(providers['video_frame_sampling_helper_available'])}")
        self.stdout.write(f"  video frame audit auto enabled: {_yes_no(providers['video_frame_audit_auto_enabled'])}")
        self.stdout.write(f"  video frame audit phase: {providers['video_frame_audit_phase']}")
        self.stdout.write(f"  video frame audit every seconds: {providers['video_frame_audit_every_seconds']}")
        self.stdout.write(f"  video frame audit max frames: {providers['video_frame_audit_max_frames']}")
        self.stdout.write(f"  video frame audit visual check: {_yes_no(providers['video_frame_audit_run_visual_check'])}")
        self.stdout.write(f"  video frame audit OCR: {_yes_no(providers['video_frame_audit_run_ocr'])}")
        self.stdout.write(f"  video frame audit publish gate: {_yes_no(providers['video_frame_audit_block_publish_on_rejection'])}")
        self.stdout.write(f"  video frame audit retain frames: {_yes_no(providers['video_frame_audit_retain_frames'])}")
        self.stdout.write(f"  video frame audit retention days: {providers['video_frame_audit_frame_retention_days']}")
        self.stdout.write(f"  video frame audit cleanup on success: {_yes_no(providers['video_frame_audit_cleanup_on_success'])}")
        self.stdout.write(f"  video frame audit storage path: {providers['video_frame_audit_storage_path']}")
        self.stdout.write(f"  video frame audit storage exists: {_yes_no(providers['video_frame_audit_storage_exists'])}")
        self.stdout.write(f"  video frame audit stored files: {providers['video_frame_audit_storage_file_count']}")

        self.stdout.write("")
        self.stdout.write("Runtime imports")
        runtime = status["runtime"]
        self.stdout.write(f"  worker.ai_agents importable: {_yes_no(runtime['worker_ai_agents_importable'])}")
        self.stdout.write(f"  Pillow importable: {_yes_no(runtime['pillow_importable'])}")
        self.stdout.write(f"  ffmpeg available: {_yes_no(runtime['ffmpeg_available'])}")
        self.stdout.write(f"  ffmpeg path: {runtime['ffmpeg_path']}")
        self.stdout.write(f"  container role guess: {runtime['container_role_guess']}")
        self.stdout.write(f"  current working directory: {runtime['cwd']}")
        self.stdout.write("  sys.path summary:")
        for path in runtime["sys_path_summary"]:
            self.stdout.write(f"    - {path}")
        if show_import_errors and runtime.get("worker_ai_agents_import_error"):
            self.stdout.write(f"  worker.ai_agents import error: {runtime['worker_ai_agents_import_error']}")

        self.stdout.write("")
        self.stdout.write("Docker guidance")
        self.stdout.write(f"  {status['docker_guidance']['sync_scan_recommendation']}")
        self.stdout.write("  In Docker, prefer running sync moderation smoke commands from the worker container.")

        self.stdout.write("")
        self.stdout.write("Available commands")
        for command_name, command_status in status["commands"].items():
            self.stdout.write(
                f"  {command_name}: discoverable={_yes_no(command_status['discoverable'])} "
                f"importable={_yes_no(command_status['importable'])}"
            )
            if show_import_errors and command_status.get("error"):
                self.stdout.write(f"    error: {command_status['error']}")

        self.stdout.write("")
        self.stdout.write("Recommended smoke commands")
        for command in status["recommended_smoke_commands"]:
            self.stdout.write(f"  {command}")


def collect_moderation_system_status(*, check_imports: bool = False) -> dict[str, Any]:
    database = _database_status()
    worker_import = _import_status("worker.ai_agents")
    pillow_import = _import_status("PIL")
    runtime = {
        "worker_ai_agents_importable": worker_import["available"],
        "worker_ai_agents_import_error": worker_import["error"],
        "pillow_importable": pillow_import["available"],
        "pillow_import_error": pillow_import["error"],
        "ffmpeg_available": bool(shutil.which("ffmpeg")),
        "ffmpeg_path": shutil.which("ffmpeg") or "",
        "container_role_guess": _container_role_guess(worker_importable=worker_import["available"]),
        "cwd": str(Path.cwd()),
        "python_executable": sys.executable,
        "sys_path_summary": _sys_path_summary(),
        "check_imports": check_imports,
    }
    return {
        "database": database,
        "text_providers": _text_provider_status(),
        "visual_ocr_video_providers": _visual_ocr_video_status(),
        "runtime": runtime,
        "docker_guidance": {
            "sync_scan_recommendation": (
                "Sync moderation commands can run in this container."
                if worker_import["available"]
                else "Run sync moderation scan commands from the worker container in Docker."
            ),
        },
        "commands": _command_statuses(),
        "recommended_smoke_commands": [
            "python manage.py moderation_system_status",
            "python manage.py moderation_smoke_checklist",
            "python manage.py create_moderation_smoke_project --kind profanity --user-id 1 --scan --request-review --review-message \"Docker smoke test from worker\"",
            "python manage.py run_moderation_scan --project-id <project_id> --sync",
            "python manage.py run_visual_moderation_scan --project-id <project_id> --cover-path <path> --sync",
            "python manage.py run_ocr_bridge --image-path <path> --asset-type slide_image --slide-order 0",
            "python manage.py sample_video_frames --video-path <path> --output-dir <frames_dir> --max-frames 1",
        ],
    }


def _database_status() -> dict[str, Any]:
    model_import = _import_status("ai_agents.models")
    project_fields = _project_moderation_fields()
    moderation_tables = _moderation_tables_exist()
    counts = {
        "agent_run_count": 0,
        "agent_finding_count": 0,
        "open_admin_review_request_count": 0,
        "count_error": "",
    }
    if model_import["available"] and moderation_tables:
        from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun

        try:
            counts = {
                "agent_run_count": AgentRun.objects.count(),
                "agent_finding_count": AgentFinding.objects.count(),
                "open_admin_review_request_count": AdminReviewRequest.objects.filter(status="open").count(),
                "count_error": "",
            }
        except Exception as exc:  # noqa: BLE001
            counts["count_error"] = f"{exc.__class__.__name__}: {exc}"
    elif model_import["available"]:
        counts["count_error"] = "Moderation database tables are missing. Run migrations before using persisted moderation data."
    return {
        "ai_agents_app_installed": apps.is_installed("ai_agents"),
        "models_importable": model_import["available"],
        "models_import_error": model_import["error"],
        "project_moderation_fields": project_fields,
        "project_moderation_fields_exist": all(project_fields.values()),
        "moderation_tables_exist": moderation_tables,
        "latest_migrations": _latest_migration_state(),
        **counts,
    }


def _text_provider_status() -> dict[str, Any]:
    return {
        "local_text_rules_available": _import_status("worker.ai_agents.providers.local_rules_provider")["available"],
        "ollama_enabled": bool(getattr(settings, "AI_AGENTS_LOCAL_LLM_ENABLED", False)),
        "ollama_base_url": str(getattr(settings, "AI_AGENTS_OLLAMA_BASE_URL", "")),
        "ollama_text_model": str(getattr(settings, "AI_AGENTS_TEXT_MODEL", "")),
        "ollama_timeout_seconds": getattr(settings, "AI_AGENTS_LLM_TIMEOUT_SECONDS", None),
    }


def _visual_ocr_video_status() -> dict[str, Any]:
    return {
        "visual_provider_interface_available": _import_status("worker.ai_agents.providers.base")["available"],
        "local_image_rules_provider_available": _import_status("worker.ai_agents.providers.local_image_rules_provider")[
            "available"
        ],
        "visual_auto_enabled": bool(getattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", False)),
        "visual_block_render_on_rejection": bool(getattr(settings, "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", False)),
        "visual_block_publish_on_rejection": bool(
            getattr(settings, "VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION", False)
        ),
        "visual_moderation_phase": str(getattr(settings, "VISUAL_MODERATION_PHASE", "")),
        "visual_safety_provider_available": _import_status("worker.ai_agents.providers.visual_safety_provider")[
            "available"
        ],
        "visual_safety_provider": str(getattr(settings, "VISUAL_SAFETY_PROVIDER", "none") or "none").strip().lower(),
        "visual_safety_classifier_enabled": bool(getattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False)),
        "visual_safety_timeout_seconds": getattr(settings, "VISUAL_SAFETY_TIMEOUT_SECONDS", None),
        "visual_safety_max_image_bytes": getattr(settings, "VISUAL_SAFETY_MAX_IMAGE_BYTES", None),
        "azure_content_safety_enabled": bool(getattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", False)),
        "azure_content_safety_endpoint_configured": bool(
            str(getattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "") or "").strip()
        ),
        "azure_content_safety_key_configured": bool(
            str(getattr(settings, "AZURE_CONTENT_SAFETY_KEY", "") or "").strip()
        ),
        "azure_content_safety_api_version": str(getattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "")),
        "azure_content_safety_categories": [
            item.strip()
            for item in str(getattr(settings, "AZURE_CONTENT_SAFETY_CATEGORIES", "") or "").split(",")
            if item.strip()
        ],
        "azure_content_safety_block_severity": getattr(settings, "AZURE_CONTENT_SAFETY_BLOCK_SEVERITY", None),
        "avatar_image_moderation_auto_enabled": bool(
            getattr(settings, "AVATAR_IMAGE_MODERATION_AUTO_ENABLED", False)
        ),
        "avatar_image_moderation_block_on_rejection": bool(
            getattr(settings, "AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION", True)
        ),
        "avatar_image_moderation_require_approval": bool(
            getattr(settings, "AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL", False)
        ),
        "ocr_bridge_provider_available": _import_status("worker.ai_agents.providers.noop_ocr_provider")["available"],
        "ocr_auto_enabled": bool(getattr(settings, "OCR_MODERATION_AUTO_ENABLED", False)),
        "ocr_block_render_on_rejection": bool(getattr(settings, "OCR_MODERATION_BLOCK_RENDER_ON_REJECTION", False)),
        "ocr_moderation_phase": str(getattr(settings, "OCR_MODERATION_PHASE", "")),
        "ocr_moderation_provider": str(getattr(settings, "OCR_MODERATION_PROVIDER", "")),
        "azure_ocr_enabled": bool(getattr(settings, "AZURE_OCR_ENABLED", False)),
        "azure_ocr_endpoint_configured": bool(str(getattr(settings, "AZURE_OCR_ENDPOINT", "") or "").strip()),
        "azure_ocr_key_configured": bool(str(getattr(settings, "AZURE_OCR_KEY", "") or "").strip()),
        "azure_ocr_model": str(getattr(settings, "AZURE_OCR_MODEL", "")),
        "azure_ocr_api_version": str(getattr(settings, "AZURE_OCR_API_VERSION", "")),
        "video_frame_sampling_helper_available": _has_attr(
            "worker.ai_agents.video_frame_moderation",
            "sample_video_frames",
        ),
        "video_frame_audit_auto_enabled": bool(getattr(settings, "VIDEO_FRAME_AUDIT_AUTO_ENABLED", False)),
        "video_frame_audit_phase": str(getattr(settings, "VIDEO_FRAME_AUDIT_PHASE", "")),
        "video_frame_audit_every_seconds": getattr(settings, "VIDEO_FRAME_AUDIT_EVERY_SECONDS", None),
        "video_frame_audit_max_frames": getattr(settings, "VIDEO_FRAME_AUDIT_MAX_FRAMES", None),
        "video_frame_audit_run_visual_check": bool(getattr(settings, "VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK", False)),
        "video_frame_audit_run_ocr": bool(getattr(settings, "VIDEO_FRAME_AUDIT_RUN_OCR", False)),
        "video_frame_audit_block_publish_on_rejection": bool(
            getattr(settings, "VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION", False)
        ),
        "video_frame_audit_retain_frames": bool(getattr(settings, "VIDEO_FRAME_AUDIT_RETAIN_FRAMES", False)),
        "video_frame_audit_frame_retention_days": getattr(settings, "VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS", None),
        "video_frame_audit_cleanup_on_success": bool(getattr(settings, "VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS", True)),
        **_video_frame_audit_storage_status(),
    }


def _video_frame_audit_storage_status() -> dict[str, Any]:
    try:
        base = (Path(str(getattr(settings, "STORAGE_ROOT", "storage_local"))) / "moderation" / "video_frames").resolve()
        if not base.exists():
            return {
                "video_frame_audit_storage_path": str(base),
                "video_frame_audit_storage_exists": False,
                "video_frame_audit_storage_file_count": 0,
            }
        return {
            "video_frame_audit_storage_path": str(base),
            "video_frame_audit_storage_exists": True,
            "video_frame_audit_storage_file_count": sum(1 for item in base.rglob("*") if item.is_file()),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "video_frame_audit_storage_path": "",
            "video_frame_audit_storage_exists": False,
            "video_frame_audit_storage_file_count": 0,
            "video_frame_audit_storage_error": f"{exc.__class__.__name__}: {exc}",
        }


def _project_moderation_fields() -> dict[str, bool]:
    from core.models import Project

    fields = {field.name for field in Project._meta.get_fields()}
    return {
        "moderation_status": "moderation_status" in fields,
        "moderation_summary": "moderation_summary" in fields,
        "last_moderation_run_id": "last_moderation_run_id" in fields,
    }


def _latest_migration_state() -> dict[str, str]:
    try:
        if MigrationRecorder.Migration._meta.db_table not in connection.introspection.table_names():
            return {}
        rows = (
            MigrationRecorder.Migration.objects.filter(app__in=["ai_agents", "core"])
            .order_by("app", "-applied", "-name")
            .values("app", "name")
        )
        latest: dict[str, str] = {}
        for row in rows:
            latest.setdefault(str(row["app"]), str(row["name"]))
        return latest
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{exc.__class__.__name__}: {exc}"}


def _moderation_tables_exist() -> bool:
    try:
        table_names = set(connection.introspection.table_names())
        return {"ai_agents_agentrun", "ai_agents_agentfinding", "ai_agents_adminreviewrequest"}.issubset(table_names)
    except Exception:
        return False


def _command_statuses() -> dict[str, dict[str, Any]]:
    discovered = get_commands()
    statuses = {}
    for command_name in KNOWN_MODERATION_COMMANDS:
        app_name = discovered.get(command_name)
        command_status = {
            "discoverable": bool(app_name),
            "importable": False,
            "app_name": app_name or "",
            "error": "",
        }
        if app_name:
            try:
                load_command_class(app_name, command_name)
                command_status["importable"] = True
            except Exception as exc:  # noqa: BLE001
                command_status["error"] = f"{exc.__class__.__name__}: {exc}"
        statuses[command_name] = command_status
    return statuses


def _import_status(module_name: str) -> dict[str, Any]:
    try:
        importlib.import_module(module_name)
        return {"available": True, "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{exc.__class__.__name__}: {exc}"}


def _has_attr(module_name: str, attr_name: str) -> bool:
    try:
        module = importlib.import_module(module_name)
        return hasattr(module, attr_name)
    except Exception:
        return False


def _sys_path_summary(limit: int = 12) -> list[str]:
    values = [str(path or ".") for path in sys.path[:limit]]
    if len(sys.path) > limit:
        values.append(f"... ({len(sys.path) - limit} more)")
    return values


def _container_role_guess(*, worker_importable: bool) -> str:
    hostname = os.environ.get("HOSTNAME", "").lower()
    cwd = str(Path.cwd()).replace("\\", "/").lower()
    in_docker = Path("/.dockerenv").exists()
    if "worker" in hostname:
        return "worker"
    if "api" in hostname:
        return "api"
    if cwd.endswith("/app/api") and worker_importable:
        return "worker-or-api-with-worker-path"
    if cwd.endswith("/app/api"):
        return "api-like"
    if in_docker:
        return "docker-unknown"
    return "local-or-unknown"


def _format_migrations(value: dict[str, str]) -> str:
    if not value:
        return "unknown"
    if "error" in value:
        return value["error"]
    return ", ".join(f"{app}={name}" for app, name in sorted(value.items()))


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
