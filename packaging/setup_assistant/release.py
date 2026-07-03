from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
TAG_PREFIX = "setup-assistant-v"
TAG_PATTERN = re.compile(r"^setup-assistant-v(?P<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))$")
PACKAGE_VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-.][A-Za-z0-9][A-Za-z0-9.-]*)?$"
)
DEFAULT_DEVELOPMENT_VERSION = "0.2.0-dev"
BUILD_VERSION_ENV = "SETUP_ASSISTANT_VERSION"
DISPLAY_VERSION_ENV = "TALKINGSLIDES_SETUP_ASSISTANT_VERSION"
VERSION_HOOK_ENV = "SETUP_ASSISTANT_VERSION_HOOK"
CHECKSUM_NAME = "SHA256SUMS.txt"
RELEASE_NOTES_NAME = "RELEASE_NOTES.md"
RELEASE_NOTES_TEMPLATE = ROOT / "docs" / "SETUP_ASSISTANT_RELEASE_NOTES.md"


@dataclass(frozen=True)
class VersionInfo:
    version: str
    tag: str
    is_release: bool


def parse_release_tag(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError(f"Malformed Setup Assistant release tag: {tag!r}")
    return match.group("version")


def validate_package_version(version: str) -> str:
    if not PACKAGE_VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Invalid Setup Assistant package version: {version!r}")
    return version


def resolve_version(env: Mapping[str, str] | None = None) -> VersionInfo:
    env = env or os.environ
    event_name = env.get("GITHUB_EVENT_NAME", "")
    ref_type = env.get("GITHUB_REF_TYPE", "")
    ref_name = env.get("GITHUB_REF_NAME", "")
    ref = env.get("GITHUB_REF", "")

    tag = ""
    if ref_type == "tag" and ref_name:
        tag = ref_name
    elif ref.startswith("refs/tags/"):
        tag = ref.removeprefix("refs/tags/")

    if tag:
        if tag.startswith(TAG_PREFIX):
            version = parse_release_tag(tag)
            return VersionInfo(version=version, tag=tag, is_release=event_name == "push")
        return VersionInfo(version=DEFAULT_DEVELOPMENT_VERSION, tag="", is_release=False)

    return VersionInfo(version=DEFAULT_DEVELOPMENT_VERSION, tag="", is_release=False)


def release_artifact_names(version: str) -> tuple[str, ...]:
    validate_package_version(version)
    return (
        f"TalkingSlides-Setup-{version}-windows-x64.exe",
        f"talkingslides-setup-cli-{version}-windows-x64.exe",
        f"TalkingSlides-Setup-{version}-linux-x64.tar.gz",
    )


def release_upload_names(version: str) -> tuple[str, ...]:
    return (*release_artifact_names(version), CHECKSUM_NAME)


def write_version_runtime_hook(version: str, target: Path) -> Path:
    validate_package_version(version)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from __future__ import annotations\n\n"
        "import os\n\n"
        f"os.environ.setdefault({DISPLAY_VERSION_ENV!r}, {version!r})\n",
        encoding="utf-8",
    )
    return target


def _copy_required(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Required build output is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def assemble_windows(dist_dir: Path, output_dir: Path, version: str) -> list[Path]:
    names = release_artifact_names(version)
    outputs = [
        output_dir / names[0],
        output_dir / names[1],
    ]
    _copy_required(dist_dir / "TalkingSlides-Setup.exe", outputs[0])
    _copy_required(dist_dir / "talkingslides-setup-cli.exe", outputs[1])
    return outputs


def _tar_add_executable(tar: tarfile.TarFile, source: Path, arcname: str) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Required build output is missing: {source}")
    if not os.access(source, os.X_OK):
        raise PermissionError(f"Linux build output is not executable: {source}")
    info = tar.gettarinfo(os.fspath(source), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    with source.open("rb") as stream:
        tar.addfile(info, stream)


def assemble_linux(dist_dir: Path, output_dir: Path, version: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / release_artifact_names(version)[2]
    sources = (
        (dist_dir / "TalkingSlides-Setup", "TalkingSlides-Setup"),
        (dist_dir / "talkingslides-setup", "talkingslides-setup"),
    )
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for source, arcname in sources:
                    _tar_add_executable(tar, source, arcname)
    return [archive]


def assemble(platform_name: str, dist_dir: Path, output_dir: Path, version: str) -> list[Path]:
    if platform_name == "windows":
        return assemble_windows(dist_dir, output_dir, version)
    if platform_name == "linux":
        return assemble_linux(dist_dir, output_dir, version)
    raise ValueError(f"Unsupported release platform: {platform_name}")


def _iter_user_artifacts(artifact_dir: Path) -> Iterable[Path]:
    ignored = {CHECKSUM_NAME, RELEASE_NOTES_NAME}
    for path in sorted(artifact_dir.rglob("*")):
        if path.is_file() and path.name not in ignored:
            yield path


def validate_artifact_set(artifact_dir: Path, version: str) -> list[Path]:
    expected = release_artifact_names(version)
    by_name: dict[str, Path] = {}
    duplicates: list[str] = []
    for path in _iter_user_artifacts(artifact_dir):
        if path.name in by_name:
            duplicates.append(path.name)
        by_name[path.name] = path

    missing = [name for name in expected if name not in by_name]
    extra = sorted(set(by_name) - set(expected))
    if duplicates:
        raise ValueError(f"Duplicate release artifact names: {', '.join(sorted(set(duplicates)))}")
    if missing:
        raise ValueError(f"Missing release artifacts: {', '.join(missing)}")
    if extra:
        raise ValueError(f"Unexpected release artifacts: {', '.join(extra)}")
    return [by_name[name] for name in expected]


def write_sha256sums(artifact_dir: Path, version: str) -> Path:
    artifacts = validate_artifact_set(artifact_dir, version)
    checksum_path = artifact_dir / CHECKSUM_NAME
    lines = []
    for artifact in artifacts:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}\n")
    checksum_path.write_text("".join(lines), encoding="utf-8")
    return checksum_path


def validate_sha256sums(artifact_dir: Path, version: str) -> None:
    checksum_path = artifact_dir / CHECKSUM_NAME
    if not checksum_path.is_file():
        raise FileNotFoundError(f"Missing checksum file: {checksum_path}")
    expected = list(release_artifact_names(version))
    seen: list[str] = []
    for raw_line in checksum_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            digest, name = raw_line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(f"Malformed checksum line: {raw_line!r}") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Malformed SHA-256 digest for {name!r}")
        seen.append(name)
    if seen != expected:
        raise ValueError(f"Checksum coverage mismatch: expected {expected!r}, got {seen!r}")


def render_release_notes(version: str, tag: str, output: Path, template: Path = RELEASE_NOTES_TEMPLATE) -> Path:
    validate_package_version(version)
    if tag:
        parsed = parse_release_tag(tag)
        if parsed != version:
            raise ValueError(f"Release tag {tag!r} does not match version {version!r}")
    body = template.read_text(encoding="utf-8")
    body = body.replace("{{VERSION}}", version).replace("{{TAG}}", tag or f"{TAG_PREFIX}{version}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    return output


def _github_json_request(method: str, url: str, token: str, payload: dict[str, object]) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "talkingslides-setup-release",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub release request failed: HTTP {exc.code}: {detail}") from exc


def _github_upload_asset(upload_url: str, token: str, asset: Path) -> None:
    base_url = upload_url.split("{", 1)[0]
    url = f"{base_url}?name={urllib.parse.quote(asset.name)}"
    content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    request = urllib.request.Request(
        url,
        data=asset.read_bytes(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "User-Agent": "talkingslides-setup-release",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub asset upload failed for {asset.name}: HTTP {exc.code}: {detail}") from exc


def publish_github_release(artifact_dir: Path, version: str, tag: str, notes: Path, env: Mapping[str, str] | None = None) -> None:
    env = env or os.environ
    parsed = parse_release_tag(tag)
    if parsed != version:
        raise ValueError(f"Release tag {tag!r} does not match version {version!r}")
    artifacts = validate_artifact_set(artifact_dir, version)
    validate_sha256sums(artifact_dir, version)
    checksum_path = artifact_dir / CHECKSUM_NAME
    if not notes.is_file():
        raise FileNotFoundError(f"Missing release notes: {notes}")

    token = env.get("GITHUB_TOKEN")
    repository = env.get("GITHUB_REPOSITORY")
    api_url = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    target_commitish = env.get("GITHUB_SHA", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required to publish a release")
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY is required to publish a release")

    release = _github_json_request(
        "POST",
        f"{api_url}/repos/{repository}/releases",
        token,
        {
            "tag_name": tag,
            "target_commitish": target_commitish,
            "name": f"TalkingSlides Setup Assistant {version}",
            "body": notes.read_text(encoding="utf-8"),
            "draft": False,
            "prerelease": False,
        },
    )
    upload_url = str(release["upload_url"])
    for asset in [*artifacts, checksum_path]:
        _github_upload_asset(upload_url, token, asset)


def _write_github_output(info: VersionInfo) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"version={info.version}\n")
            stream.write(f"tag={info.tag}\n")
            stream.write(f"is_release={str(info.is_release).lower()}\n")
    print(f"version={info.version}")
    print(f"tag={info.tag}")
    print(f"is_release={str(info.is_release).lower()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Setup Assistant release packaging helper")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("github-env", help="Resolve package version from GitHub Actions environment")

    assemble_parser = subcommands.add_parser("assemble", help="Assemble final user-downloadable artifacts")
    assemble_parser.add_argument("--platform", choices=("windows", "linux"), required=True)
    assemble_parser.add_argument("--version", required=True)
    assemble_parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist" / "setup-assistant")
    assemble_parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "release")

    checksums_parser = subcommands.add_parser("checksums", help="Generate SHA256SUMS.txt")
    checksums_parser.add_argument("--version", required=True)
    checksums_parser.add_argument("--artifact-dir", type=Path, default=ROOT / "dist" / "release")

    notes_parser = subcommands.add_parser("notes", help="Render release notes from the template")
    notes_parser.add_argument("--version", required=True)
    notes_parser.add_argument("--tag", required=True)
    notes_parser.add_argument("--output", type=Path, default=ROOT / "dist" / "release" / RELEASE_NOTES_NAME)
    notes_parser.add_argument("--template", type=Path, default=RELEASE_NOTES_TEMPLATE)

    publish_parser = subcommands.add_parser("publish", help="Create a GitHub Release and upload assets")
    publish_parser.add_argument("--version", required=True)
    publish_parser.add_argument("--tag", required=True)
    publish_parser.add_argument("--artifact-dir", type=Path, default=ROOT / "dist" / "release")
    publish_parser.add_argument("--notes", type=Path, default=ROOT / "dist" / "release" / RELEASE_NOTES_NAME)

    args = parser.parse_args(argv)
    try:
        if args.command == "github-env":
            _write_github_output(resolve_version())
            return 0
        if args.command == "assemble":
            outputs = assemble(args.platform, args.dist_dir, args.output_dir, args.version)
            for output in outputs:
                print(output)
            return 0
        if args.command == "checksums":
            print(write_sha256sums(args.artifact_dir, args.version))
            return 0
        if args.command == "notes":
            print(render_release_notes(args.version, args.tag, args.output, args.template))
            return 0
        if args.command == "publish":
            publish_github_release(args.artifact_dir, args.version, args.tag, args.notes)
            return 0
    except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
