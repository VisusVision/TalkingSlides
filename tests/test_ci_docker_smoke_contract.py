from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]


def _compose_service_block(service_name: str) -> str:
    compose = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^  {re.escape(service_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:|\Z)",
        compose,
    )
    assert match, f"service block not found: {service_name}"
    return match.group("body")


def test_docker_smoke_skips_live_avatar_dependency_downloads() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "--set \"worker.args.INSTALL_AVATAR_RUNTIME_DEPS=0\"" in workflow
    assert "--set \"worker.args.INSTALL_OPENMMLAB_DEPS=0\"" in workflow
    assert "--set \"worker.args.DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0\"" in workflow
    assert "docker-smoke-${{ github.ref_name }}" in workflow
    assert "--set \"*.cache-from=type=gha,scope=${DOCKER_BUILD_CACHE_SCOPE}\"" in workflow
    assert "--set \"*.cache-to=type=gha,scope=${DOCKER_BUILD_CACHE_SCOPE},mode=max,ignore-error=true\"" in workflow


def test_worker_dockerfile_keeps_runtime_avatar_dependencies_opt_in() -> None:
    dockerfile = (REPO_ROOT / "infra" / "dockerfiles" / "Dockerfile.worker").read_text(encoding="utf-8")

    assert "ARG INSTALL_AVATAR_RUNTIME_DEPS=1" in dockerfile
    assert "ARG INSTALL_OPENMMLAB_DEPS=1" in dockerfile
    assert "ARG DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1" in dockerfile
    assert "ARG MMCV_VERSION=2.0.1" in dockerfile
    assert "ARG MMCV_FIND_LINKS=https://download.openmmlab.com/mmcv/dist/cu118/torch2.0.0/index.html" in dockerfile
    assert 'ARG MMCV_WHEEL_URL=""' in dockerfile
    assert "ARG MMCV_LOCAL_WHEEL=local_wheels/mmcv.whl" in dockerfile
    assert "--mount=type=bind,source=.,target=/build-context,ro" in dockerfile
    assert '[ -f "/build-context/$MMCV_LOCAL_WHEEL" ]' in dockerfile
    assert 'pip install --only-binary=:all: "/build-context/$MMCV_LOCAL_WHEEL"' in dockerfile
    assert 'if [ -n "$MMCV_WHEEL_URL" ]; then' in dockerfile
    assert 'pip install --only-binary=:all: "$MMCV_WHEEL_URL"' in dockerfile
    assert "pip install \"mmcv==${MMCV_VERSION}\" -f \"$MMCV_FIND_LINKS\" --only-binary=:all:" in dockerfile
    assert "mim install" not in dockerfile
    assert "openmim" not in dockerfile
    assert "pip install --no-build-isolation mmcv" not in dockerfile
    assert "MMCV_FIND_LINKS is not reachable or does not expose a prebuilt wheel index" in dockerfile
    assert "Put a compatible wheel at ${MMCV_LOCAL_WHEEL}" in dockerfile
    assert 'if [ "$INSTALL_AVATAR_RUNTIME_DEPS" = "1" ]; then' in dockerfile
    assert "Skipping avatar runtime dependencies for smoke build because INSTALL_AVATAR_RUNTIME_DEPS=${INSTALL_AVATAR_RUNTIME_DEPS}." in dockerfile
    assert "mkdir -p /opt/musetalk /opt/liveportrait/pretrained_weights /app/storage_local/models/musetalk" in dockerfile
    assert "Skipping OpenMMLab/mmcv dependencies for smoke build because INSTALL_OPENMMLAB_DEPS=${INSTALL_OPENMMLAB_DEPS}." in dockerfile
    assert "Skipping LivePortrait pretrained weights download for smoke build." in dockerfile
    assert dockerfile.index('"/build-context/$MMCV_LOCAL_WHEEL"') < dockerfile.index('"$MMCV_WHEEL_URL"')


def test_local_compose_builds_avatar_worker_with_heavy_deps_by_default() -> None:
    compose = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")

    assert compose.count('INSTALL_AVATAR_RUNTIME_DEPS: "${INSTALL_AVATAR_RUNTIME_DEPS:-1}"') >= 2
    assert compose.count('INSTALL_OPENMMLAB_DEPS: "${INSTALL_OPENMMLAB_DEPS:-1}"') >= 2
    assert compose.count('DOWNLOAD_LIVEPORTRAIT_WEIGHTS: "${DOWNLOAD_LIVEPORTRAIT_WEIGHTS:-1}"') >= 2
    assert compose.count('MMCV_VERSION: "${MMCV_VERSION:-2.0.1}"') >= 2
    assert compose.count('MMCV_FIND_LINKS: "${MMCV_FIND_LINKS:-https://download.openmmlab.com/mmcv/dist/cu118/torch2.0.0/index.html}"') >= 2
    assert compose.count('MMCV_WHEEL_URL: "${MMCV_WHEEL_URL:-}"') >= 2
    assert compose.count('MMCV_LOCAL_WHEEL: "${MMCV_LOCAL_WHEEL:-local_wheels/mmcv.whl}"') >= 2


def test_worker_avatar_is_behind_explicit_compose_avatar_profile() -> None:
    worker_avatar = _compose_service_block("worker-avatar")
    worker = _compose_service_block("worker")

    assert "profiles:" in worker_avatar
    assert "- avatar" in worker_avatar
    assert "profiles:" not in worker
    assert 'AVATAR_BOOTSTRAP_ON_WORKER_STARTUP: "1"' in worker_avatar
    assert 'AVATAR_BOOTSTRAP_ON_WORKER_STARTUP: "0"' in worker


def test_worker_avatar_uses_volatile_runtime_cache_dirs() -> None:
    worker_avatar = _compose_service_block("worker-avatar")

    assert "XDG_CACHE_HOME: /tmp/visus-cache" in worker_avatar
    assert "MPLCONFIGDIR: /tmp/visus-cache/matplotlib" in worker_avatar
    assert "NUMBA_CACHE_DIR: /tmp/visus-cache/numba" in worker_avatar
    assert 'mkdir -p "$${XDG_CACHE_HOME:-/tmp/visus-cache}"' in worker_avatar
    assert "chown -R" not in worker_avatar
    assert "chmod -R" not in worker_avatar


def test_windows_runtime_passes_avatar_profile_only_for_avatar_services() -> None:
    script = (REPO_ROOT / "scripts" / "windows-runtime.ps1").read_text(encoding="utf-8")

    assert 'function Test-UsesAvatarProfile' in script
    assert 'return $Services -contains "worker-avatar"' in script
    assert '$args += @("--profile", "avatar")' in script
    assert '"core" { return $core }' in script
    assert '"avatar" { return $core + @("tts_service", "worker", "worker-avatar") }' in script
    assert '"full" { return $core + @("tts_service", "worker", "worker-avatar", "libretranslate") }' in script
    assert '$composeArgs += @("up", "-d", "--no-build", "--pull", "never")' in script
    assert "Remove-Item" not in script
    assert "docker pull" not in script


def test_windows_runtime_supports_backend_only_no_frontend_tts_mode() -> None:
    runtime = (REPO_ROOT / "scripts" / "windows-runtime.ps1").read_text(encoding="utf-8")
    health = (REPO_ROOT / "scripts" / "windows-runtime-health.ps1").read_text(encoding="utf-8")
    preflight = (REPO_ROOT / "scripts" / "windows-preflight.ps1").read_text(encoding="utf-8")

    assert "[switch]$NoFrontend" in runtime
    assert "[switch]$NoFrontend" in health
    assert "[switch]$NoFrontend" in preflight
    assert '[bool]$ExcludeFrontend = $false' in runtime
    assert 'if (-not $ExcludeFrontend)' in runtime
    service_selection = '$services = @(Get-ProfileServices -SelectedProfile $selectedProfile -ExcludeFrontend:$NoFrontend)'
    assert runtime.count(service_selection) == 3
    assert runtime.count("-PassNoFrontend:$NoFrontend") == 5
    assert '"tts" { return $core + @("tts_service", "worker") }' in runtime
    assert '"tts" { return $core + @("tts_service", "worker", "worker-avatar") }' not in runtime
    assert '"worker-avatar"' in runtime
    assert 'return $Services -contains "worker-avatar"' in runtime
    assert '$composeArgs += @("up", "-d", "--no-build", "--pull", "never")' in runtime
    assert '@("build"' not in runtime
    assert '@("pull"' not in runtime
    assert "down -v" not in runtime.lower()
    assert "prune" not in runtime.lower()

    assert "Frontend: excluded by -NoFrontend" in health
    assert '$requiredHttpNames = @("API health", "API readiness", "capabilities")' in health
    assert 'if (-not $NoFrontend)' in health
    assert "Test-HttpEndpointWithRetry" in health
    assert "[int]$StartupWaitSeconds = 90" in health
    assert "$startupDeadline = (Get-Date).AddSeconds([Math]::Max(0, $StartupWaitSeconds))" in health
    assert "Get-RemainingWaitSeconds -Deadline $startupDeadline" in health
    assert "use_startup_budget = $TtsSelected" in health
    assert "TTS is still warming or not ready" in health
    assert '$hasRequiredEndpointFailure = [bool]($Results | Where-Object { $_.category -eq "HTTP endpoints" -and $_.status -eq "FAIL" })' in health
    assert '$exitCode = if ($hasCoreFailure -or $hasRequiredEndpointFailure) { 1 } else { 0 }' in health
    assert '[pscustomobject]@{ name = "TTS readiness"; status = $ttsReadinessStatus }' in health

    assert "Skipped because -NoFrontend requests a backend-only runtime." in preflight
    assert 'if (-not $NoFrontend)' in preflight


def test_docs_cover_backend_only_no_frontend_smoke_mode() -> None:
    docs = "\n".join(
        [
            (REPO_ROOT / "docs" / "FULL_STACK_LOCAL_RUNTIME.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "INSTALL_WINDOWS.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8"),
        ]
    )

    assert ".\\scripts\\windows-runtime.ps1 -Profile tts -NoFrontend" in docs
    assert ".\\scripts\\windows-runtime-health.ps1 -Profile tts -NoFrontend" in docs
    assert ".\\scripts\\windows-runtime.ps1 -Profile tts -NoFrontend -Stop" in docs
    assert "backend-only" in docs
    assert "frontend" in docs


def test_windows_preflight_and_health_avatar_checks_are_profile_aware_and_read_only() -> None:
    preflight = (REPO_ROOT / "scripts" / "windows-preflight.ps1").read_text(encoding="utf-8")
    health = (REPO_ROOT / "scripts" / "windows-runtime-health.ps1").read_text(encoding="utf-8")

    for script in (preflight, health):
        assert '[string]$Profile = ""' in script
        assert "Add-AvatarRuntimeReadinessChecks" in script
        assert "INSTALL_OPENMMLAB_DEPS=0" in script
        assert "DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0" in script
        assert "mmcv/mmpose/mmdet imports were not run" in script
        assert "storage_local\\models" in script
        assert "docker run" not in script
        assert "docker build" not in script
        assert "docker pull" not in script
        assert "compose\", \"up" not in script


def test_windows_doctor_report_script_contract() -> None:
    script_path = REPO_ROOT / "scripts" / "windows-doctor.ps1"
    script = script_path.read_text(encoding="utf-8")

    assert script_path.exists()
    assert "[switch]$Json" in script
    assert '[string]$OutputPath = ""' in script
    assert "if (-not [string]::IsNullOrWhiteSpace($OutputPath))" in script
    assert "VISUS VidLab Windows doctor" in script
    assert "Read-only: no builds, pulls, installs, service starts, model downloads, volume/image deletes, or avatar jobs." in script
    assert "Secret-like values are reported by variable name only. Values are never printed." in script
    assert "Values were not printed." in script
    assert "secret_values_printed = $false" in script

    for required_name in [
        "DJANGO_SETTINGS_MODULE",
        "SECRET_KEY",
        "POSTGRES_HOST",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "STORAGE_BACKEND",
        "STORAGE_ROOT",
        "MEDIA_TOKEN_SECRET",
        "VITE_API_BASE_URL",
    ]:
        assert required_name in script

    for warning_text in [
        "Google sign-in disabled. Create OAuth credentials in Google Cloud Console if needed.",
        "Email sending disabled. Configure SMTP/Brevo/Mailjet.",
        "Cloud AI disabled. Use Ollama/local fallback if configured.",
        "Local AI disabled.",
        "Translation disabled unless profile configured.",
        "Avatar rendering disabled until avatar profile/image/models are ready.",
        "STORAGE_BACKEND=s3 requires S3 credentials.",
        "DRM is enabled but provider metadata may be incomplete.",
        "Payment/EZDRM-like variables are present, but no runtime payment path was validated.",
    ]:
        assert warning_text in script

    assert '"S3 storage adapter" $EnvMap @("S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY") "WARN" "WARN"' in script

    forbidden_execution_tokens = [
        "Invoke-Expression",
        "Start-Process",
        "Remove-Item",
        "Install-Module",
        "Install-Package",
        "pip install",
    ]
    for token in forbidden_execution_tokens:
        assert token not in script

    forbidden_compose_args = [
        '@("build"',
        '@("pull"',
        '@("up"',
        '@("run"',
        '@("down"',
        '@("stop"',
        '@("rm"',
        '"compose", "build"',
        '"compose", "pull"',
        '"compose", "up"',
        '"compose", "run"',
    ]
    for token in forbidden_compose_args:
        assert token not in script


def test_visus_launcher_entry_points_and_safety_contract() -> None:
    bat_path = REPO_ROOT / "VISUS-VidLab.bat"
    launcher_path = REPO_ROOT / "scripts" / "visus-launcher.ps1"

    assert bat_path.exists()
    assert launcher_path.exists()

    bat = bat_path.read_text(encoding="utf-8")
    launcher = launcher_path.read_text(encoding="utf-8")

    assert "powershell" in bat.lower()
    assert "ExecutionPolicy Bypass" in bat
    assert "scripts\\visus-launcher.ps1" in bat

    for menu_item in [
        "[1] Run Doctor",
        "[2] Start Core Runtime",
        "[3] Start Avatar Runtime",
        "[4] Health Check",
        "[5] Run Quick Tests",
        "[6] Run Full Tests",
        "[7] Stop Runtime",
        "[8] Open Local URLs",
        "[9] Save Doctor Report",
        "[0] Exit",
    ]:
        assert menu_item in launcher

    assert "windows-doctor.ps1" in launcher
    assert "windows-runtime.ps1" in launcher
    assert "windows-runtime-health.ps1" in launcher
    assert '@("-Profile", $selectedProfile, "-Stop")' in launcher
    assert ".\\.venv\\Scripts\\python.exe -m pytest tests\\test_ci_docker_smoke_contract.py -q" in launcher
    assert "scratch\\doctor-reports" in launcher
    assert "Secret values are never printed" in launcher

    forbidden_direct_commands = [
        r"(?i)\bdocker\s+build\b",
        r"(?i)\bdocker\s+pull\b",
        r"(?i)\bdocker\s+compose\s+up\b",
        r"(?i)\bdocker\s+compose\s+down\b",
        r"(?i)\bdown\s+-v\b",
        r"(?i)\bprune\b",
        r"(?i)\bpip\s+install\b",
        r"(?i)\bnpm\s+install\b",
        r"(?i)\bmodel\s+download\b",
    ]
    for pattern in forbidden_direct_commands:
        assert not re.search(pattern, launcher)

    forbidden_direct_invocations = [
        r"(?m)^\s*&\s+docker\b",
        r"(?m)^\s*docker\s+",
        r"(?m)^\s*&\s+pip\b",
        r"(?m)^\s*pip\s+",
        r"(?m)^\s*&\s+npm\s+install\b",
        r"(?m)^\s*npm\s+install\b",
    ]
    for pattern in forbidden_direct_invocations:
        assert not re.search(pattern, launcher)

    assert "Get-Content" not in launcher
    assert "infra\\.env" not in launcher
    assert "$env:" not in launcher


def test_visus_launcher_docs_are_linked_from_windows_docs() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install = (REPO_ROOT / "docs" / "INSTALL_WINDOWS.md").read_text(encoding="utf-8")
    runtime = (REPO_ROOT / "docs" / "FULL_STACK_LOCAL_RUNTIME.md").read_text(encoding="utf-8")

    for doc in (readme, install, runtime):
        assert "VISUS-VidLab.bat" in doc
        assert ".\\scripts\\visus-launcher.ps1" in doc

    combined = "\n".join([readme, install, runtime])
    assert "convenience wrapper" in combined
    assert "Doctor remains read-only" in combined
    assert "scripts/windows-runtime.ps1" in combined
    assert "avatar runtime should only be started intentionally" in combined.lower()


def test_avatar_local_wheels_are_ignored_but_not_dockerignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "local_wheels/" in gitignore
    assert "*.whl" in gitignore
    assert "local_wheels/" not in dockerignore
    assert "*.whl" not in dockerignore


def test_avatar_offline_wheel_and_prebuilt_image_docs_exist() -> None:
    runbook = (REPO_ROOT / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")

    assert "MMCV_LOCAL_WHEEL" in runbook
    assert "local_wheels/" in runbook
    assert "MMCV_WHEEL_URL" in runbook
    assert "prebuilt heavy avatar worker image" in runbook
    assert "Installing `mmcv` into `.venv` does not help `worker-avatar`" in runbook
    assert "--profile avatar" in runbook


def test_windows_avatar_runtime_helper_is_dry_run_command_printer() -> None:
    script_path = REPO_ROOT / "scripts" / "windows-avatar-runtime.ps1"
    script = script_path.read_text(encoding="utf-8")

    assert script_path.exists()
    assert 'ValidateSet("Plan", "Check", "PrintBuildCommand", "PrintSmokeCommand")' in script
    assert '[string]$Action = "Plan"' in script
    assert "This script is non-destructive. It prints plans and commands only." in script
    assert "It does not build images, pull images, start services, install packages, download models, delete volumes, or run avatar jobs." in script
    assert '"Plan" { Write-Plan }' in script
    assert '"Check" { Invoke-LocalCheck }' in script

    forbidden_execution_tokens = [
        "Invoke-Expression",
        "Start-Process",
        "Remove-Item",
        "Install-Module",
        "Install-Package",
        "pip install",
    ]
    for token in forbidden_execution_tokens:
        assert token not in script

    forbidden_invocations = [
        r"(?m)^\s*&\s+docker\b",
        r"(?m)^\s*docker\s+",
        r"(?m)^\s*&\s+powershell\b",
        r"(?m)^\s*&\s+pwsh\b",
    ]
    for pattern in forbidden_invocations:
        assert not re.search(pattern, script)


def test_windows_avatar_runtime_helper_prints_explicit_later_commands() -> None:
    script = (REPO_ROOT / "scripts" / "windows-avatar-runtime.ps1").read_text(encoding="utf-8")

    assert "Review first, then copy/paste manually when you intend to run it later:" in script
    assert "docker compose -f infra\\docker-compose.yml --profile avatar build --progress=plain" in script
    assert "--build-arg INSTALL_AVATAR_RUNTIME_DEPS=1" in script
    assert "--build-arg INSTALL_OPENMMLAB_DEPS=1" in script
    assert "--build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1" in script
    assert "--build-arg MMCV_LOCAL_WHEEL=" in script
    assert "--build-arg MMCV_WHEEL_URL=" in script
    assert "docker pull $env:AVATAR_WORKER_PREBUILT_IMAGE" in script
    assert "docker tag $env:AVATAR_WORKER_PREBUILT_IMAGE ai_academy_worker:local" in script
    assert ".\\scripts\\windows-runtime.ps1 -Profile avatar" in script
    assert "python .\\scripts\\check_avatar_models.py" in script
    assert "--entrypoint python worker-avatar -c" in script
    assert "CELERY_AVATAR_QUEUE=avatar-smoke" in script
    assert "XDG_CACHE_HOME=/tmp/visus-cache" in script
    assert "MPLCONFIGDIR=/tmp/visus-cache/matplotlib" in script
    assert "NUMBA_CACHE_DIR=/tmp/visus-cache/numba" in script
    assert "storage_local\\models" in script
    assert "musetalk\\musetalk.json" in script


def test_docs_separate_core_from_avatar_profile_runtime() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install = (REPO_ROOT / "docs" / "INSTALL_WINDOWS.md").read_text(encoding="utf-8")
    runtime = (REPO_ROOT / "docs" / "FULL_STACK_LOCAL_RUNTIME.md").read_text(encoding="utf-8")

    combined = "\n".join([readme, install, runtime])
    assert "Compose `avatar` profile" in combined
    assert "core runtime does not start it" in combined
    assert "stale/light" in combined
    assert "Do not install `mmcv`, `mmpose`, LivePortrait, or MuseTalk into the Windows virtual environment" in combined


def test_avatar_runtime_docs_keep_parser_caches_off_storage_mount() -> None:
    docs = "\n".join(
        [
            (REPO_ROOT / "docs" / "FULL_STACK_LOCAL_RUNTIME.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8"),
        ]
    )

    assert "/tmp/visus-cache" in docs
    assert "volatile parser/runtime caches" in docs
    assert "broadly chmod/chowning `storage_local`" in docs


def test_avatar_runtime_strategy_docs_cover_safe_build_and_smoke_paths() -> None:
    docs = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "INSTALL_WINDOWS.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "FULL_STACK_LOCAL_RUNTIME.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "ENVIRONMENT_VARIABLES.md").read_text(encoding="utf-8"),
        ]
    )

    assert "windows-avatar-runtime.ps1" in docs
    assert "online OpenMMLab build" in docs
    assert "local `local_wheels/`" in docs
    assert "prebuilt image" in docs
    assert "ai_academy_worker:local" in docs
    assert "storage_local\\models" in docs
    assert "stale/light" in docs
    assert "avatar profile" in docs
    assert "does not run Docker build/pull/up/run commands" in docs

    lower_docs = docs.lower()
    assert "pip install mmcv" not in lower_docs
    assert "python -m pip install mmcv" not in lower_docs
    assert ".venv\\scripts\\python.exe -m pip install" not in lower_docs


def test_windows_doctor_docs_are_onboarding_entry_point() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install = (REPO_ROOT / "docs" / "INSTALL_WINDOWS.md").read_text(encoding="utf-8")
    docs = "\n".join([readme, install])

    assert ".\\scripts\\windows-doctor.ps1" in docs
    assert ".\\scripts\\windows-doctor.ps1 -Json" in docs
    assert ".\\scripts\\windows-doctor.ps1 -OutputPath scratch\\doctor-report.json" in install
    assert "one-command installer readiness report" in docs
    assert "Secret-like values are never printed" in install
    assert "does not build, pull, start services, install packages, download models, delete Docker data, or run avatar jobs" in readme
