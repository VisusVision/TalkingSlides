from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_docker_smoke_skips_live_avatar_dependency_downloads() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "--set \"worker.args.INSTALL_OPENMMLAB_DEPS=0\"" in workflow
    assert "--set \"worker.args.DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0\"" in workflow
    assert "docker-smoke-${{ github.ref_name }}" in workflow
    assert "--set \"*.cache-from=type=gha,scope=${DOCKER_BUILD_CACHE_SCOPE}\"" in workflow
    assert "--set \"*.cache-to=type=gha,scope=${DOCKER_BUILD_CACHE_SCOPE},mode=max,ignore-error=true\"" in workflow


def test_worker_dockerfile_keeps_runtime_avatar_dependencies_opt_in() -> None:
    dockerfile = (REPO_ROOT / "infra" / "dockerfiles" / "Dockerfile.worker").read_text(encoding="utf-8")

    assert "ARG INSTALL_OPENMMLAB_DEPS=1" in dockerfile
    assert "ARG DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1" in dockerfile
    assert "mim install \"mmcv==2.0.1\"" in dockerfile
    assert "Skipping OpenMMLab/mmcv dependencies for smoke build." in dockerfile
    assert "Skipping LivePortrait pretrained weights download for smoke build." in dockerfile
