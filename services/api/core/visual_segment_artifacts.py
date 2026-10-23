"""Private bounded media pins and reuse-only audio eligibility; no job/API writes.

TrustSource is a privileged server dependency, not deserialized request data.
There is deliberately no production receipt source implementation in this phase.
Storage uses descriptor-relative, no-follow traversal and atomic no-overwrite
links. The old PrivateRenderAssets image/font contract is not widened or changed.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import threading
import time
import uuid
import wave
from typing import Protocol

from django.conf import settings

from core.visual_design.schemas import DesignError, fields, require, content_hash, canonical_json, digest
from core.visual_design.render_snapshot import FrozenRenderSnapshot
from core.visual_design.segment_contracts import (SegmentAudioProof, SegmentRequest, LIMITS, _asset,
    _bounded, _uuid, page_identity)
from core.visual_design.segment_dependencies import dependencies_from_verified_audio, MEDIA_VALIDATOR_VERSION

PIN_VERSION = "visual-segment-media-pin.v1"
ROLE_TYPES = {"narration": ("m4a", LIMITS["max_audio_bytes"]),
              "source_wav": ("wav", LIMITS["max_audio_bytes"]),
              "voice_sample": ("wav", LIMITS["max_audio_bytes"]),
              "segment": ("mp4", LIMITS["max_mp4_bytes"]),
              "srt": ("srt", LIMITS["max_caption_bytes"]),
              "vtt": ("vtt", LIMITS["max_caption_bytes"])}
CHUNK_BYTES = 64 * 1024


def _component(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) is not None,
            "asset_path", "Only opaque internal path components are permitted")
    return value


def _numeric_id(value):
    require(type(value) in (str, int) and re.fullmatch(r"[1-9][0-9]{0,18}", str(value)) is not None,
            "audio_scope", "Explicit numeric owner/project required")
    return str(value)


def _digest_fd(fd, expected):
    _asset(expected, (expected["extension"],), max(v[1] for v in ROLE_TYPES.values()))
    before = os.fstat(fd)
    require(stat.S_ISREG(before.st_mode) and before.st_size == expected["size"], "artifact_integrity", "Missing, truncated or oversized media")
    os.lseek(fd, 0, os.SEEK_SET)
    hasher = hashlib.sha256(); count = 0
    while True:
        block = os.read(fd, min(CHUNK_BYTES, expected["size"] - count + 1))
        if not block: break
        count += len(block)
        require(count <= expected["size"], "artifact_integrity", "Media grew during verification")
        hasher.update(block)
    after = os.fstat(fd)
    require(count == expected["size"] and "sha256:" + hasher.hexdigest() == expected["sha256"]
            and _stat_identity(before) == _stat_identity(after), "artifact_integrity", "Media changed or differs from preexisting expected digest")
    os.lseek(fd, 0, os.SEEK_SET)
    return after


def _stat_identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _configured_root(raw):
    require(isinstance(raw, (str, Path)) and bool(str(raw).strip()) and Path(raw).is_absolute()
            and not any(part == ".." for part in Path(raw).parts), "private_storage", "Explicit absolute persistent private root required")
    root = Path(raw)
    try: resolved = root.resolve()
    except (OSError, RuntimeError) as exc: raise DesignError("private_storage", "Invalid private root resolution") from exc
    for key in ("STORAGE_ROOT", "STATIC_ROOT", "MEDIA_ROOT"):
        public = getattr(settings, key, None)
        if public:
            public = Path(public).resolve()
            require(not resolved.is_relative_to(public) and not public.is_relative_to(resolved),
                    "private_storage", "Private media must be separate from public storage/static/media")
    require(root != Path("/"), "private_storage", "Filesystem root cannot be private media storage")
    return root


def _directory(fd, name, *, create):
    if create:
        try: os.mkdir(name, 0o700, dir_fd=fd)
        except FileExistsError: pass
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    return child


@contextmanager
def _root_fd(root, *, create):
    require(os.name == "posix" and hasattr(os, "O_NOFOLLOW") and Path("/proc/self/fd").is_dir(),
            "private_storage", "POSIX no-follow storage descriptors required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            next_fd = _directory(fd, part, create=create)
            os.close(fd); fd = next_fd
        info = os.fstat(fd)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                "private_storage", "Private root must be owned by runtime UID with mode 0700")
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _descend(root, components, *, create):
    with _root_fd(root, create=create) as base:
        fd = os.dup(base)
        try:
            for part in components:
                _component(part)
                next_fd = _directory(fd, part, create=create)
                os.close(fd); fd = next_fd
                info = os.fstat(fd)
                require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                        "private_storage", "Private media directories require runtime UID and mode 0700")
            yield fd
        finally:
            os.close(fd)


def _open_media(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and bool(info.st_mode & stat.S_IRUSR)
                and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                "artifact_integrity", "Readable private regular media file required")
        return fd
    except BaseException:
        os.close(fd)
        raise


class MediaPinBudget:
    """One explicit attempt's in-process unique-object budget, safe for threads.

    Future persisted Runs must carry/reconstruct their aggregate budget; creating
    a new budget is not a way to authorize another file in the same attempt.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._sizes = {}; self._pending = {}; self._committed = set()

    @contextmanager
    def claim(self, key, size):
        require(type(size) is int and 0 < size <= LIMITS["max_mp4_bytes"], "asset_limit", "Bounded positive media claim required")
        with self._lock:
            if key not in self._sizes:
                require(len(self._sizes) < 128, "asset_limit", "Attempt object count exceeded")
                require(sum(self._sizes.values()) + size <= LIMITS["max_attempt_bytes"], "asset_limit", "Attempt media budget exceeded")
                self._sizes[key] = size
            require(self._sizes[key] == size, "artifact_integrity", "Conflicting object size")
            self._pending[key] = self._pending.get(key, 0) + 1
        success = False
        try:
            yield
            success = True
        finally:
            with self._lock:
                if success: self._committed.add(key)
                self._pending[key] -= 1
                if not self._pending[key]:
                    self._pending.pop(key)
                    if key not in self._committed: self._sizes.pop(key)

    @property
    def bytes_claimed(self):
        with self._lock: return sum(self._sizes.values())


class PrivateSegmentMedia:
    def __init__(self, owner_id, project_id, *, budget=None):
        self.owner_id, self.project_id = _numeric_id(owner_id), _numeric_id(project_id)
        self.root = _configured_root(getattr(settings, "VISUAL_RENDER_PRIVATE_ROOT", os.environ.get("VISUAL_RENDER_PRIVATE_ROOT")))
        self.budget = budget if budget is not None else MediaPinBudget()

    def reference(self, asset, role):
        require(type(role) is str and role in ROLE_TYPES, "asset_type", "Unsupported private media role")
        extension, maximum = ROLE_TYPES[role]
        _asset(asset, (extension,), maximum)
        return {"schema_version": PIN_VERSION, "owner_id": self.owner_id, "project_id": self.project_id,
                "role": role, "asset": json.loads(canonical_json(asset))}

    def _validate(self, pin):
        _bounded(pin)
        fields(pin, ("schema_version", "owner_id", "project_id", "role", "asset"))
        require(pin["schema_version"] == PIN_VERSION and pin["owner_id"] == self.owner_id and pin["project_id"] == self.project_id,
                "audio_scope", "Foreign or unsupported private media pin")
        self.reference(pin["asset"], pin["role"])

    def _location(self, pin):
        self._validate(pin)
        return ("segments-v1", self.owner_id, self.project_id, "objects", pin["role"]), pin["asset"]["sha256"][7:] + "." + pin["asset"]["extension"]

    @contextmanager
    def open_verified(self, pin):
        components, name = self._location(pin)
        with _descend(self.root, components, create=False) as directory:
            fd = _open_media(directory, name)
            try:
                before = _digest_fd(fd, pin["asset"])
                yield fd
                require(_stat_identity(os.fstat(fd)) == _stat_identity(before), "artifact_integrity", "Pinned input mutated during read")
                _digest_fd(fd, pin["asset"])
            finally:
                os.close(fd)

    def verify(self, pin):
        with self.open_verified(pin): pass
        return pin

    def read(self, pin):
        with self.open_verified(pin) as fd:
            data = bytearray()
            while True:
                part = os.read(fd, CHUNK_BYTES)
                if not part: break
                data.extend(part)
                require(len(data) <= pin["asset"]["size"], "artifact_integrity", "Pinned media grew")
            require(len(data) == pin["asset"]["size"] and "sha256:"+hashlib.sha256(data).hexdigest() == pin["asset"]["sha256"],
                    "artifact_integrity", "Read bytes differ from pin")
            return bytes(data)

    def pin_from_fd(self, source_fd, asset, role, *, validate_staged=None):
        """Copy verified bytes only; source_fd comes from a server-owned namespace.

        An optional verifier runs on the private staged descriptor BEFORE its
        atomic link. No caller-provided filesystem locator is accepted here.
        """
        pin = self.reference(asset, role)
        components, name = self._location(pin)
        key = (self.owner_id, self.project_id, role, asset["sha256"])
        with self.budget.claim(key, asset["size"]), _descend(self.root, components, create=True) as directory:
            before = _digest_fd(source_fd, asset)
            temporary = "pin-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            linked = False
            try:
                total = 0; hasher = hashlib.sha256()
                while True:
                    block = os.read(source_fd, min(CHUNK_BYTES, asset["size"] - total + 1))
                    if not block: break
                    total += len(block)
                    require(total <= asset["size"], "artifact_integrity", "Source exceeds expected size")
                    hasher.update(block)
                    view = memoryview(block)
                    while view:
                        written = os.write(fd, view)
                        require(written > 0, "artifact_integrity", "Private media write stalled")
                        view = view[written:]
                require(total == asset["size"] and "sha256:" + hasher.hexdigest() == asset["sha256"]
                        and _stat_identity(before) == _stat_identity(os.fstat(source_fd)), "artifact_integrity", "Source changed while pinning")
                os.fsync(fd)
                _digest_fd(fd, asset)
                if validate_staged is not None:
                    validate_staged(fd)
                    _digest_fd(fd, asset)
                try:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    linked = True
                except FileExistsError:
                    pass  # Verify existing object; never overwrite or repair it silently.
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
                self.verify(pin)  # Fresh descriptor traversal checks reachability too.
            except BaseException:
                if linked:
                    # Remove only our own still-matching inode, never a foreign replacement.
                    try:
                        saved = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if (saved.st_dev, saved.st_ino) == (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                            os.unlink(name, dir_fd=directory)
                    except FileNotFoundError: pass
                raise
            finally:
                os.close(fd)
                try: os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError: pass
        return pin


@dataclass(frozen=True)
class ProducerArtifact:
    """Server-owned opaque import handle, never a relative/absolute path."""
    namespace: str
    object_id: str
    asset_json: str

    def asset(self):
        from core.visual_design.segment_contracts import parse_contract_json
        return parse_contract_json(self.asset_json)


@dataclass(frozen=True)
class ProducerAssociation:
    receipt_id: str
    producer_id: str
    expected_proof_hash: str
    owner_id: str
    project_id: str
    page_key: str
    source_slide_index: int
    split_index: int
    baseline_job_id: str
    baseline_run_id: str
    baseline_manifest_hash: str
    artifacts: tuple[tuple[str, ProducerArtifact], ...]
    engineering_only: bool = False


class ProducerTrustSource(Protocol):
    """Injected privileged authentication boundary. Not a payload/self-hash API.

    Implementations must independently authenticate producer records and their
    expected digests/scope/import handles. No production implementation exists
    yet; unconfigured and legacy baselines are deliberately ineligible.
    """
    def lookup(self, receipt_id: str) -> ProducerAssociation | None: ...
    def currently_authorized(self, association: ProducerAssociation) -> bool: ...


class ApprovedProducerArtifacts:
    """Explicit server-configured, private, owner/project/run-scoped import roots."""
    @contextmanager
    def open(self, association, artifact):
        _component(artifact.namespace); _component(artifact.object_id)
        roots = getattr(settings, "VISUAL_SEGMENT_PRODUCER_ROOTS", {})
        require(type(roots) is dict and artifact.namespace in roots, "audio_provenance_unavailable", "Approved producer namespace unavailable")
        root = _configured_root(roots[artifact.namespace])
        asset = artifact.asset()
        extension = asset.get("extension")
        require(extension in ("m4a", "wav"), "asset_type", "Only attested audio imports supported")
        with _descend(root, (_numeric_id(association.owner_id), _numeric_id(association.project_id), _component(association.baseline_run_id)), create=False) as directory:
            fd = _open_media(directory, artifact.object_id + "." + extension)
            try:
                _digest_fd(fd, asset)
                yield fd
                _digest_fd(fd, asset)
            finally:
                os.close(fd)


class NativeAudioValidator:
    """Read-only native validation; tool identities are supplied by the operator.

    Pure contract tests do not import/call this boundary. The supplied tools are
    server configuration, never selected by a request/proof. No speech or video
    encoding occurs: one packet-copy hash and one bounded PCM decode validate the
    existing bytes. Energy is not evidence of the spoken text.
    """
    def __init__(self, *, ffmpeg, ffprobe, expected_ffmpeg_hash, expected_ffprobe_hash):
        self.ffmpeg = self._tool(ffmpeg, expected_ffmpeg_hash)
        self.ffprobe = self._tool(ffprobe, expected_ffprobe_hash)
        self.runtime = {"ffmpeg_hash": expected_ffmpeg_hash, "ffprobe_hash": expected_ffprobe_hash, "validator_version": MEDIA_VALIDATOR_VERSION}

    @staticmethod
    def _tool(raw, expected):
        digest(expected)
        require(type(raw) is str and Path(raw).is_absolute(), "audio_runtime", "Explicit installed tool required")
        try:
            path = Path(raw).resolve(strict=True)
            require(path.is_file() and os.access(path, os.X_OK), "audio_runtime", "Executable media runtime unavailable")
            with path.open("rb") as file:
                hasher = hashlib.file_digest(file, "sha256")
        except OSError as exc:
            raise DesignError("audio_runtime", "Native media runtime unavailable") from exc
        require("sha256:" + hasher.hexdigest() == expected, "audio_runtime", "Unexpected native media build")
        return str(path)

    def _run(self, command, fd, *, output_limit, consume=None):
        """Bound both pipes while streaming; deadlines include the whole command."""
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       pass_fds=(fd,), close_fds=True)
        except OSError as exc:
            raise DesignError("audio_runtime", "Native verifier could not start") from exc
        selector = selectors.DefaultSelector(); output = bytearray(); error = bytearray(); total = 0
        selector.register(process.stdout, selectors.EVENT_READ, "out")
        selector.register(process.stderr, selectors.EVENT_READ, "err")
        deadline = time.monotonic() + LIMITS["probe_timeout_seconds"]
        try:
            while selector.get_map():
                require(time.monotonic() < deadline, "audio_runtime", "Native audio verification timed out")
                for entry, _ in selector.select(min(0.1, max(0, deadline-time.monotonic()))):
                    data = os.read(entry.fileobj.fileno(), CHUNK_BYTES)
                    if not data:
                        selector.unregister(entry.fileobj); continue
                    if entry.data == "err":
                        require(len(error)+len(data) <= 65536, "audio_format", "Native decoder error budget exceeded")
                        error.extend(data)
                    else:
                        total += len(data)
                        require(total <= output_limit, "audio_format", "Native media output budget exceeded")
                        if consume is None: output.extend(data)
                        else: consume(data)
            require(process.wait(timeout=max(0.01, deadline-time.monotonic())) == 0, "audio_format", "Native media verification failed")
            return bytes(output), total
        except BaseException:
            process.kill(); process.wait(timeout=2)
            raise
        finally:
            selector.close(); process.stdout.close(); process.stderr.close()

    def validate(self, fd, proof):
        # Recheck runtime files for replacement between construction and use.
        self._tool(self.ffmpeg, self.runtime["ffmpeg_hash"])
        self._tool(self.ffprobe, self.runtime["ffprobe_hash"])
        before = _digest_fd(fd, proof["audio"]["asset"])
        source = "/proc/self/fd/" + str(fd)
        options = ["-v", "error", "-protocol_whitelist", "file,pipe", "-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0"]
        raw, _ = self._run([self.ffprobe, *options, "-i", source, "-show_entries",
            "format=format_name,duration,size:stream=codec_type,codec_name,profile,sample_rate,channels,duration,start_time", "-of", "json"], fd, output_limit=256*1024)
        try: probe = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc: raise DesignError("audio_format", "Invalid native probe result") from exc
        streams = probe.get("streams", [])
        require(len(streams) == 1 and streams[0].get("codec_type") == "audio", "audio_format", "Exactly one audio stream required")
        stream = streams[0]; expected = proof["audio"]
        require(stream.get("codec_name") == "aac" and stream.get("profile") == "LC"
                and expected["codec"] == "aac" and expected["profile"] == "LC"
                and int(stream.get("sample_rate", 0)) == expected["sample_rate"] and stream.get("channels") == expected["channels"],
                "audio_format", "Audio codec/profile/sample format differs from producer proof")
        try: duration_ms = round(float(stream["duration"]) * 1000)
        except (ValueError, KeyError, OverflowError) as exc: raise DesignError("audio_format", "Missing finite measured duration") from exc
        require(0 < duration_ms <= LIMITS["max_duration_ms"] and abs(duration_ms-expected["duration_ms"]) <= LIMITS["duration_tolerance_ms"],
                "audio_format", "Measured audio duration differs from attestation")
        require("mov" in probe.get("format", {}).get("format_name", "").split(",")
                and int(probe["format"]["size"]) == expected["asset"]["size"], "audio_format", "Unexpected audio container")
        try:
            container_duration_ms = round(float(probe["format"]["duration"]) * 1000)
            start_ms = round(float(stream["start_time"]) * 1000)
        except (ValueError, KeyError, OverflowError) as exc:
            raise DesignError("audio_format", "Finite container/local start timing required") from exc
        require(abs(container_duration_ms-expected["duration_ms"]) <= LIMITS["duration_tolerance_ms"] and start_ms == 0,
                "audio_format", "Audio must have matching container duration and page-local zero origin")
        packets = hashlib.sha256()
        _, packet_size = self._run([self.ffmpeg, "-nostdin", *options, "-i", source, "-map", "0:a:0", "-c", "copy", "-f", "data", "pipe:1"],
                                  fd, output_limit=LIMITS["max_audio_bytes"], consume=packets.update)
        require(packet_size > 0 and "sha256:"+packets.hexdigest() == expected["packet_payload_hash"],
                "artifact_integrity", "Encoded AAC packets differ from producer proof")
        pcm = {"count": 0, "peak": 0, "carry": b""}
        def decoded(data):
            from array import array
            import sys
            data = pcm["carry"] + data
            pcm["carry"] = data[-1:] if len(data)%2 else b""
            values = array("h"); values.frombytes(data[:len(data)//2*2])
            if sys.byteorder != "little": values.byteswap()
            pcm["count"] += len(values)
            pcm["peak"] = max(pcm["peak"], max((abs(v) for v in values), default=0))
        _, decoded_size = self._run([self.ffmpeg, "-nostdin", *options, "-i", source, "-map", "0:a:0", "-t", "120.08", "-f", "s16le", "-c:a", "pcm_s16le", "pipe:1"],
                                   fd, output_limit=48*1024*1024, consume=decoded)
        require(decoded_size > 0 and not pcm["carry"] and pcm["peak"] > 32, "audio_format", "No nonempty audible decoded audio")
        decoded_ms = round(pcm["count"]*1000/expected["sample_rate"]/expected["channels"])
        require(abs(decoded_ms-expected["duration_ms"]) <= LIMITS["duration_tolerance_ms"], "audio_format", "Decoded duration mismatch")
        require(_stat_identity(before) == _stat_identity(os.fstat(fd)), "artifact_integrity", "Audio changed during native verification")
        _digest_fd(fd, expected["asset"])
        return {"schema_version": MEDIA_VALIDATOR_VERSION, "audio_sha256": expected["asset"]["sha256"],
                "packet_payload_hash": "sha256:"+packets.hexdigest(), "duration_ms": duration_ms,
                "container_duration_ms": container_duration_ms, "start_ms": start_ms,
                "sample_rate": expected["sample_rate"], "channels": expected["channels"], "decoded_bytes": decoded_size,
                "runtime": self.runtime, "spoken_content_verified_by_energy": False}


def _validate_wav(fd):
    # Structural completeness only. The producer receipt establishes origin/text.
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), "rb") as file:
        with wave.open(file) as audio:
            require(audio.getcomptype() == "NONE" and 1 <= audio.getnchannels() <= 2 and audio.getsampwidth() in (1, 2, 3, 4)
                    and 8000 <= audio.getframerate() <= 96000 and 0 < audio.getnframes()/audio.getframerate() <= 120,
                    "audio_format", "Unsupported attested source WAV")
            remaining = audio.getnframes(); size = audio.getnchannels()*audio.getsampwidth()
            while remaining:
                count = min(4096, remaining); data = audio.readframes(count)
                require(len(data) == count*size, "audio_format", "Truncated source WAV")
                remaining -= count
    os.lseek(fd, 0, os.SEEK_SET)


@dataclass(frozen=True)
class AudioReuseEligibility:
    eligible: bool
    reason_code: str
    proof_validated: bool = False
    producer_authenticated: bool = False
    bytes_verified: bool = False
    media_verified: bool = False
    pin_verified: bool = False
    execution_hash: str | None = None
    pin_json: str | None = None
    dependencies_json: str | None = None
    verification_json: str | None = None
    supporting_pins_json: str | None = None
    detail_code: str | None = None
    schema_version: str = field(default="visual-segment-audio-eligibility.v1", init=False)
    execution_enabled: bool = field(default=False, init=False)

    @property
    def pin(self): return json.loads(self.pin_json) if self.pin_json else None
    @property
    def dependencies(self): return json.loads(self.dependencies_json) if self.dependencies_json else None
    @property
    def supporting_pins(self): return json.loads(self.supporting_pins_json) if self.supporting_pins_json else {}


def pin_verified_narration(value, *, snapshot, snapshot_id, expected_snapshot_hash, owner_id, project_id,
        store, trusted_source=None, verifier=None, supported_runtime_hashes=frozenset(), allow_engineering=False):
    """Independently authenticate -> bind frozen inputs -> pin -> inspect -> recheck.

    All object dependencies are server-controlled. A request may supply identities
    but never a TrustSource, root, native command or expected producer digest.
    No live producer is retroactively enrolled, no TTS fallback is possible.
    """
    flags = {}; stage = "provenance"
    def result(code, **extra): return AudioReuseEligibility(eligible=False, reason_code=code, **flags, **extra)
    try:
        _bounded(value)
        require(isinstance(store, PrivateSegmentMedia), "private_storage", "Private media store required")
        require(isinstance(snapshot, FrozenRenderSnapshot), "snapshot", "Frozen snapshot required")
        raw = snapshot.to_dict()
        require(raw["owner_id"] == _numeric_id(owner_id) == store.owner_id and raw["project_id"] == _numeric_id(project_id) == store.project_id,
                "audio_scope", "Requested owner/project scope mismatch")
        if trusted_source is None: return result("audio_provenance_unavailable")
        claimed = value.get("audio_proof", {})
        require(type(claimed) is dict, "provenance", "Producer proof required")
        _uuid(claimed.get("receipt_id"))
        try: association = trusted_source.lookup(claimed["receipt_id"])
        except Exception: return result("audio_provenance_unavailable", detail_code="trust_source_unavailable")
        if association is None: return result("audio_provenance_unavailable")
        require(isinstance(association, ProducerAssociation) and association.receipt_id == claimed["receipt_id"], "provenance", "Invalid trusted producer association")
        require(type(association.engineering_only) is bool and (not association.engineering_only or allow_engineering is True), "provenance", "Synthetic engineering proof disabled")
        try: authorized = trusted_source.currently_authorized(association)
        except Exception: authorized = False
        if authorized is not True: return result("audio_ineligible", detail_code="producer_revoked_or_unavailable")
        proof = SegmentAudioProof(claimed, expected_hash=association.expected_proof_hash, expected_producer_id=association.producer_id,
                                  allow_synthetic=association.engineering_only and allow_engineering is True)
        p = proof.to_dict()
        require((p["producer"]["attestation_kind"] == "synthetic_engineering_attestation.v1") == association.engineering_only,
                "provenance", "Engineering/production attestation boundary mismatch")
        flags.update(proof_validated=True, producer_authenticated=True)
        require(p["owner_id"] == association.owner_id == raw["owner_id"] and p["project_id"] == association.project_id == raw["project_id"]
                and canonical_json(p["page"]) == canonical_json({"page_key":association.page_key,"source_slide_index":association.source_slide_index,"split_index":association.split_index})
                and p["baseline"]["job_id"] == association.baseline_job_id and p["baseline"]["run_id"] == association.baseline_run_id
                and p["baseline"]["manifest_hash"] == association.baseline_manifest_hash, "audio_scope", "Producer scope/baseline mismatch")
        stage = "binding"
        from core.visual_design.segment_contracts import snapshot_page
        selected_page = snapshot_page(snapshot, value.get("page", {}).get("page_key"))
        require(selected_page["narration_text"] == p["narration"]["protected_text"], "narration", "Protected narration differs from producer association")
        request = SegmentRequest(value, snapshot=snapshot, snapshot_id=snapshot_id, expected_snapshot_hash=expected_snapshot_hash,
                                 audio_proof=proof, supported_runtime_hashes=supported_runtime_hashes)
        artifacts = dict(association.artifacts)
        require(len(artifacts) == len(association.artifacts) and "narration" in artifacts, "provenance", "Explicit expected audio import association required")
        expected_roles = {"narration":p["audio"]["asset"]}
        if p["processing"]["input_asset"]["extension"] == "wav": expected_roles["source_wav"] = p["processing"]["input_asset"]
        if p["synthesis"]["dependencies"]["sample"] is not None: expected_roles["voice_sample"] = p["synthesis"]["dependencies"]["sample"]
        require(set(artifacts) == set(expected_roles), "provenance", "Required producer artifact associations unavailable")
        for role, asset in expected_roles.items():
            require(isinstance(artifacts[role], ProducerArtifact) and canonical_json(artifacts[role].asset()) == canonical_json(asset),
                    "provenance", "Expected media digest must come from independent association")
        if verifier is None: return result("unsafe_or_unavailable_runtime")
        require(isinstance(verifier, NativeAudioValidator), "audio_runtime", "Verified native audio runtime required")
        storage = ApprovedProducerArtifacts()
        stage = "storage"; verification = {}; supporting_pins = {}
        # Validate/pin supporting sources first. None can authorize narration alone.
        for role in ("source_wav", "voice_sample", "narration"):
            if role not in artifacts: continue
            with storage.open(association, artifacts[role]) as fd:
                if role == "narration":
                    def validate(staged_fd): verification.update(verifier.validate(staged_fd, p))
                else:
                    validate = _validate_wav
                pin = store.pin_from_fd(fd, expected_roles[role], role, validate_staged=validate)
                if role == "narration": narration_pin = pin
                else: supporting_pins[role] = pin
        require(verification.get("schema_version") == MEDIA_VALIDATOR_VERSION
                and verification.get("audio_sha256") == p["audio"]["asset"]["sha256"]
                and verification.get("packet_payload_hash") == p["audio"]["packet_payload_hash"], "audio_format", "Native verification receipt unavailable")
        flags.update(bytes_verified=True, media_verified=True)
        stage = "pin"
        store.verify(narration_pin)
        flags["pin_verified"] = True
        try: authorized = trusted_source.currently_authorized(association)
        except Exception: authorized = False
        if authorized is not True: return result("audio_ineligible", detail_code="producer_revoked_or_unavailable")
        dependencies = dependencies_from_verified_audio(proof, content_hash(MEDIA_VALIDATOR_VERSION, verification))
        return AudioReuseEligibility(eligible=True, reason_code="eligible_verified_reuse", **flags,
            execution_hash=request.execution_hash, pin_json=canonical_json(narration_pin), dependencies_json=dependencies.to_json(), verification_json=canonical_json(verification), supporting_pins_json=canonical_json(supporting_pins))
    except (OSError, DesignError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError, wave.Error) as exc:
        code = exc.code if isinstance(exc, DesignError) else type(exc).__name__
        if code in ("audio_scope",): reason = "invalid_audio_scope"
        elif code == "narration": reason = "incompatible_narration"
        elif code in ("voice", "audio_settings"): reason = "incompatible_synthesis_dependencies"
        elif code in ("audio_runtime", "runtime", "private_storage", "TimeoutExpired"): reason = "unsafe_or_unavailable_runtime"
        elif code in ("audio_format", "audio_settings_format") or isinstance(exc, wave.Error): reason = "unsupported_audio_format"
        elif code in ("snapshot", "identity", "scene", "index", "captions", "timing"): reason = "structural_page_mismatch"
        elif stage == "pin" and isinstance(exc, FileNotFoundError): reason = "missing_private_pin"
        elif stage in ("storage", "pin"): reason = "invalid_or_corrupt_artifact"
        elif stage == "binding": reason = "incompatible_synthesis_dependencies"
        else: reason = "audio_provenance_unavailable" if code in ("provenance", "audio_provenance_unavailable") else "audio_ineligible"
        return result(reason, detail_code=code)
