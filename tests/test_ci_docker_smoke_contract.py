from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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
