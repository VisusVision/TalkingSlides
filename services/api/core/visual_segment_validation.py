"""Independent native verification of private segment pins, never publication.

Only this module can mint an in-process acceptance witness after probing actual
bytes. A JSON receipt, self-hash, boolean or report-only eligibility is not one.
No worker import is required in the API image. There is no HTTP entry point.
"""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageStat, ImageChops
from core.visual_design.schemas import DesignError, require, content_hash, canonical_json
from core.visual_design.segment_contracts import SegmentRequest, SegmentReceipt, RECEIPT_VERSION, SCENE_VERSION, LIMITS
from core.visual_segment_artifacts import PrivateSegmentMedia, _digest_fd
from core.visual_render_assets import PrivateRenderAssets

_VALIDATED = object()


@dataclass(frozen=True, init=False)
class _VerifiedSegment:
    receipt_json: str
    pins_json: str
    frame_json: str
    generation: int
    claim_token: str
    _authority: object

    def __init__(self, receipt, pins, frame, generation, claim_token, *, _authority):
        require(_authority is _VALIDATED, "media_validation_unavailable", "Native validation witness required")
        for key, value in (("receipt_json", receipt.to_json()), ("pins_json", canonical_json(pins)),
                           ("frame_json", canonical_json(frame)), ("generation", generation),
                           ("claim_token", str(claim_token)), ("_authority", _authority)):
            object.__setattr__(self, key, value)


def require_media_witness(value):
    require(type(value) is _VerifiedSegment and value._authority is _VALIDATED,
            "media_validation_unavailable", "Only independently probed media can complete a Run")
    return value


def installed_runtime(verifier):
    try:
        return _observe_runtime(verifier)
    except DesignError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise DesignError("runtime", "Supported installed media build unavailable") from exc


def _observe_runtime(verifier):
    """Observe installed tools/libs; NOT an allowlist or producer trust grant.

    Execution compares this with the separately configured/frozen server runtime.
    Linux dynamic builds with librsvg/fontconfig only; static/unknown builds fail.
    """
    verifier._tool(verifier.ffmpeg, verifier.runtime["ffmpeg_hash"])
    verifier._tool(verifier.ffprobe, verifier.runtime["ffprobe_hash"])
    version = subprocess.run([verifier.ffmpeg, "-version"], capture_output=True, timeout=5, check=True).stdout
    require(0 < len(version) < 65536, "runtime", "Bounded FFmpeg identity required")
    linked = subprocess.run(["ldd", verifier.ffmpeg], capture_output=True, timeout=5, check=True).stdout.decode()
    require(len(linked) < 65536, "runtime", "Bounded native library identity required")
    libraries = {}
    for name, path in re.findall(r"\s+(\S+) => (/\S+) ", linked):
        # Pin the complete linked build, including raster/font/codec dependencies.
        with Path(path).resolve(strict=True).open("rb") as file:
            libraries[name] = "sha256:" + hashlib.file_digest(file, "sha256").hexdigest()
    require(any("librsvg" in name for name in libraries) and "libfontconfig.so.1" in libraries,
            "runtime", "Supported librsvg/fontconfig runtime unavailable")
    return {"build_id": "private-segment-linux.v1", "ffmpeg_version": "version-sha256." + hashlib.sha256(version).hexdigest(),
        "ffmpeg_binary_hash": verifier.runtime["ffmpeg_hash"], "rasterizer_id": "librsvg.v1",
        "rasterizer_build_hash": content_hash("visual-segment-linked-runtime.v1", libraries),
        "fontconfig_build_hash": libraries["libfontconfig.so.1"]}


def _ms(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DesignError("media_timing", "Invalid native duration") from exc
    require(math.isfinite(number) and 0 <= number <= 120.08, "media_timing", "Finite bounded duration required")
    return round(number * 1000)


def _caption_text(request, role):
    """Exact serialization, using established millisecond formatters.

    General helpers strip/normalize/drop cues. This strict adapter rejects text
    the sidecar formats cannot represent literally, and never edits it.
    """
    from scripts.ffmpeg_helpers import _format_srt_time, _format_vtt_time
    req = request.to_dict(); sub = req["subtitles"]
    require(content_hash("visual-segment-cues.v1", {"page": sub["page"], "cues": sub["cues"]}) == sub["cues_hash"],
            "captions", "Frozen caption hash changed")
    formatter = _format_srt_time if role == "srt" else _format_vtt_time
    blocks = []
    for i, cue in enumerate(sub["cues"], 1):
        text = cue["text"]
        require(text and "\r" not in text and "\x00" not in text and "\n\n" not in text
                and not text.startswith("\n") and not text.endswith("\n") and "-->" not in text
                and "<" not in text and "&" not in text
                and all(ord(c) >= 32 or c in "\n\t" for c in text),
                "captions", "Cue cannot be represented verbatim as safe SRT and WebVTT")
        require(0 <= cue["start_ms"] < cue["end_ms"] <= sub["audio_duration_ms"], "captions", "Cue extends into hold")
        blocks.append(f'{i}\n{formatter(cue["start_ms"]/1000)} --> {formatter(cue["end_ms"]/1000)}\n{text}\n')
    result = (("WEBVTT\n\n" if role == "vtt" else "") + "\n".join(blocks)).encode("utf-8")
    require(0 < len(result) <= LIMITS["max_caption_bytes"], "captions", "Caption output exceeds limits")
    return result


def caption_bytes(request, role):
    require(isinstance(request, SegmentRequest) and role in ("srt", "vtt"), "captions", "Strict request and sidecar role required")
    return _caption_text(request, role)


def validate_captions(data, request, role):
    """Parse independently; compare every cue and canonical exact bytes."""
    require(type(data) is bytes and 0 < len(data) <= LIMITS["max_caption_bytes"], "captions", "Bounded caption bytes required")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise DesignError("captions", "Caption UTF-8 is malformed") from exc
    if role == "vtt":
        require(text.startswith("WEBVTT\n\n"), "captions", "Missing WebVTT header")
        text = text[8:]
    blocks = text.removesuffix("\n").split("\n\n")
    cues = request.to_dict()["subtitles"]["cues"]
    require(len(blocks) == len(cues), "captions", "Caption count changed")
    separator = "," if role == "srt" else "."
    pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2})" + re.escape(separator) + r"(\d{3})")
    def timestamp(raw):
        match = pattern.fullmatch(raw)
        require(match is not None, "captions", "Invalid caption timestamp")
        h, m, s, ms = map(int, match.groups())
        require(m < 60 and s < 60, "captions", "Invalid caption timestamp bounds")
        return h*3600000 + m*60000 + s*1000 + ms
    for i, (block, cue) in enumerate(zip(blocks, cues), 1):
        lines = block.split("\n")
        require(len(lines) >= 3 and lines[0] == str(i) and len(lines[1].split(" --> ")) == 2, "captions", "Malformed caption block")
        start, end = map(timestamp, lines[1].split(" --> "))
        require((start, end, "\n".join(lines[2:])) == (cue["start_ms"], cue["end_ms"], cue["text"]), "captions", "Caption text/timeline changed")
    require(data == caption_bytes(request, role), "captions", "Noncanonical caption output")


def validate_segment_outputs(request, *, frame_asset, pins, verifier, generation, claim_token):
    """No paths/URLs accepted. Hash/probe pins independently of the compositor.

    The frame identity is supplied by the validated Phase 3A compositor. Later
    completion rechecks it and all pins outside locks, then fences under locks.
    """
    require(isinstance(request, SegmentRequest), "request", "Validated immutable request required")
    req = request.to_dict()
    require(installed_runtime(verifier) == req["recipe"]["runtime"], "runtime", "Frozen runtime differs from installed build")
    require(type(pins) is dict and set(pins) == {"mp4", "srt", "vtt"}, "outputs", "Exact three private outputs required")
    store = PrivateSegmentMedia(req["snapshot"]["owner_id"], req["snapshot"]["project_id"])
    assets = PrivateRenderAssets(req["snapshot"]["project_id"])
    frame = assets.read(frame_asset)
    from io import BytesIO
    with Image.open(BytesIO(frame)) as image:
        require(image.format == "PNG" and image.size == (1920,1080) and max(ImageStat.Stat(image.convert("RGB")).stddev) > 1,
                "frame", "Invalid/blank input frame")
    with Image.open(BytesIO(frame)) as original:
        expected_raster = original.convert("RGB").resize((480,270))
    for role in ("srt", "vtt"):
        require(pins[role]["role"] == role, "outputs", "Incorrect caption role")
        validate_captions(store.read(pins[role]), request, role)
    require(pins["mp4"]["role"] == "segment", "outputs", "Incorrect MP4 role")
    proof = req["audio_proof"]; audio = proof["audio"]
    # Verify the original pin again using the authenticated proof. MP4 decoding
    # below establishes content integrity; producer provenance establishes words.
    with store.open_verified(store.reference(audio["asset"], "narration")) as fd:
        verifier.validate(fd, proof)
    with store.open_verified(pins["mp4"]) as fd:
        src = f"/proc/self/fd/{fd}"
        opts = ["-v", "error", "-protocol_whitelist", "file,pipe", "-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0", "-i", src]
        raw, _ = verifier._run([verifier.ffprobe, *opts, "-count_frames", "-show_streams", "-show_format", "-of", "json"], fd, output_limit=262144)
        probe = json.loads(raw)
        streams = probe.get("streams", [])
        require(len(streams) == 2 and sorted(s.get("codec_type", "") for s in streams) == ["audio", "video"], "streams", "Exactly one video and audio required")
        v = next(s for s in streams if s["codec_type"] == "video")
        a = next(s for s in streams if s["codec_type"] == "audio")
        require(probe["format"].get("major_brand", probe["format"].get("tags", {}).get("major_brand")) in ("isom", "mp42")
                and "mp4" in probe["format"]["format_name"].split(",") and int(probe["format"]["size"]) == pins["mp4"]["asset"]["size"], "streams", "Unexpected MP4 container")
        require(v.get("codec_name") == "h264" and v.get("pix_fmt") == "yuv420p" and (v.get("width"),v.get("height")) == (1920,1080)
                and Fraction(v.get("avg_frame_rate", "0")) == 25 and Fraction(v.get("r_frame_rate", "0")) == 25
                and _ms(v.get("start_time")) == 0 and _ms(a.get("start_time")) == 0, "streams", "Unsupported video geometry/rate/origin")
        require(a.get("codec_name") == audio["codec"] and a.get("profile") == audio["profile"]
                and int(a.get("sample_rate",0)) == audio["sample_rate"] and a.get("channels") == audio["channels"], "streams", "Audio format changed")
        packets = hashlib.sha256()
        _, count = verifier._run([verifier.ffmpeg, "-nostdin", *opts, "-map", "0:a:0", "-c", "copy", "-f", "data", "pipe:1"], fd,
                                 output_limit=LIMITS["max_audio_bytes"], consume=packets.update)
        require(count > 0 and "sha256:"+packets.hexdigest() == audio["packet_payload_hash"], "artifact_integrity", "AAC payload changed")
        # Full decode (both streams) catches truncated/invalid frames, not just headers.
        verifier._run([verifier.ffmpeg, "-nostdin", "-xerror", *opts, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], fd, output_limit=1024)
        frame_count = int(v.get("nb_frames",0))
        require(0 < frame_count <= 3002 and int(v.get("nb_read_frames",0)) == frame_count,
                "streams", "Missing or inconsistent decoded frame count")
        # libx264 records its actual parameters in the encoded SEI. Reject an
        # incompatible encode even if container metadata merely says H.264.
        headers = bytearray()
        def video_headers(data):
            if len(headers) < 65536: headers.extend(data[:65536-len(headers)])
        verifier._run([verifier.ffmpeg,"-nostdin",*opts,"-map","0:v:0","-c","copy","-f","data","pipe:1"],fd,
            output_limit=LIMITS["max_mp4_bytes"],consume=video_headers)
        require(b"x264 - core" in headers and all(option in headers for option in
            (b"rc=crf ",b"crf=23.0 ",b"subme=7 ",b"me=hex ",b"ref=3 ",b"trellis=1 ",b"rc_lookahead=40 ")),
            "streams", "Encoded video does not attest the supported CRF-23/medium x264 options")
        for index in (0, frame_count//2, frame_count-1):
            raster, _ = verifier._run([verifier.ffmpeg, "-nostdin", *opts, "-map", "0:v:0", "-vf", f"select=eq(n\\,{index}),scale=480:270",
                "-frames:v", "1", "-threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], fd, output_limit=480*270*3)
            require(len(raster) == 480*270*3, "frame", "Missing decoded frame")
            image = Image.frombytes("RGB", (480,270), raster)
            require(max(ImageStat.Stat(image).stddev) > 1, "blank_frame", "Blank decoded output")
            # Lossy H.264 is not pixel-identical. Bound visible deviation from
            # the independently validated exact scene, on this supported build.
            require(max(ImageStat.Stat(ImageChops.difference(image, expected_raster)).rms) < 12,
                    "frame", "Decoded output does not match the frozen visual")
        measured = {"video": {"codec": "h264", "width":1920, "height":1080,"fps":25,"pixel_format":"yuv420p",
                "duration_ms":_ms(v["duration"]),"frame_count":frame_count},
            "audio":{"codec":"aac","profile":"LC","sample_rate":audio["sample_rate"],"channels":audio["channels"],
                "duration_ms":_ms(a["duration"]),"packet_payload_hash":"sha256:"+packets.hexdigest()},
            "container_duration_ms":_ms(probe["format"]["duration"]),"caption_end_ms":req["subtitles"]["cues"][-1]["end_ms"]}
        _digest_fd(fd, pins["mp4"]["asset"])
    value = {"schema_version": RECEIPT_VERSION, "execution_hash":request.execution_hash,"snapshot":req["snapshot"],"page":req["page"],
        "scene_hash":content_hash(SCENE_VERSION,req["scene"]),"design":{"source_hash":req["scene"]["source_hash"],"source_revision":req["scene"]["source_revision"],"selected":req["scene"]["design"]},
        "inputs":{"frame_asset":frame_asset,"audio_asset":audio["asset"],"audio_proof_hash":req["narration"]["audio_proof_hash"],"cues_hash":req["subtitles"]["cues_hash"]},
        "producer":{"receipt_id":proof["receipt_id"],"producer_id":proof["producer"]["id"],"baseline":proof["baseline"]},
        "recipe_hash":content_hash("visual-segment-recipe.v1",req["recipe"]),"runtime":req["recipe"]["runtime"],"attempt_generation":generation,
        "outputs":{k:v["asset"] for k,v in pins.items()},"streams":measured,
        "acceptance":{"status":"accepted_private.v1","validator_version":"visual-segment-validator.v1","integrity":"all_output_hashes_verified.v1","warnings":[]},"publishing_allowed":False}
    receipt = SegmentReceipt(value, request=request, expected_frame_asset=frame_asset, expected_attempt_generation=generation)
    return _VerifiedSegment(receipt,pins,frame_asset,generation,claim_token,_authority=_VALIDATED)
