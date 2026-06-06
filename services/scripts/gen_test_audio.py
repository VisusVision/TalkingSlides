"""Generate a test WAV file for MuseTalk lip-sync testing.

Tries the stack's TTS service first (real speech = best for sync testing).
Falls back to an ffmpeg-generated speech-like WAV if TTS is unavailable.

Usage (inside worker container):
    python /app/scripts/gen_test_audio.py --output /tmp/test_speech.wav
"""
import argparse
import json
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

TEXT_LONG = (
    "Welcome to AI Academy. "
    "In this lesson you will learn about machine learning and neural networks. "
    "Artificial intelligence is transforming the world around us."
)
TEXT_SHORT = "Hello, I am your AI teacher. Welcome to your lesson today."

TTS_URL = "http://tts_service:8001/synthesize"


def try_tts(output: Path, text: str) -> bool:
    """Try the stack's TTS service. Returns True on success."""
    payload = json.dumps({"text": text, "language": "en"}).encode("utf-8")
    req = urllib.request.Request(
        TTS_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            # Service may return JSON with a path, or raw audio bytes
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                body = json.loads(data)
                # e.g. {"audio_path": "/tmp/xxx.wav"} or {"url": "/audio/xxx.wav"}
                audio_path = body.get("audio_path") or body.get("file")
                if audio_path:
                    import shutil
                    shutil.copy2(audio_path, output)
                    print(f"[gen_test_audio] TTS JSON path copied: {audio_path} → {output}")
                    return True
                # Handle audio_url (absolute URL to MP3/WAV)
                audio_url = body.get("audio_url") or body.get("url")
                if audio_url:
                    import tempfile, os
                    # Download to a temp file
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                        tmp_mp3 = tf.name
                    with urllib.request.urlopen(audio_url, timeout=30) as r2:
                        Path(tmp_mp3).write_bytes(r2.read())
                    # Convert to 16kHz mono PCM WAV (required by Whisper encoder)
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-v", "warning",
                         "-i", tmp_mp3,
                         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                         str(output)],
                        capture_output=True, text=True,
                    )
                    os.unlink(tmp_mp3)
                    if r.returncode == 0 and output.exists():
                        print(f"[gen_test_audio] TTS audio_url → WAV: {audio_url} → {output}")
                        return True
                    print(f"[gen_test_audio] ffmpeg convert failed: {r.stderr[-200:]}")
                    return False
                print(f"[gen_test_audio] TTS JSON response unrecognised: {body}")
                return False
            else:
                # Raw audio bytes
                output.write_bytes(data)
                print(f"[gen_test_audio] TTS raw audio written ({len(data)} bytes) → {output}")
                return True
    except Exception as exc:
        print(f"[gen_test_audio] TTS service unavailable: {exc}")
        return False


def ffmpeg_fallback(output: Path) -> bool:
    """Generate a speech-like WAV with ffmpeg (AM-modulated sine = buzzy voice)."""
    # 6-second audio: mix of frequencies that sounds vaguely like speech
    # amfm_chromium: amplitude-modulate a 180Hz carrier with a 4Hz envelope
    filter_chain = (
        "sine=frequency=180:sample_rate=16000:duration=6,"
        "volume=enable='lt(mod(t,0.25),0.18)':volume=0:eval=frame"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=180:sample_rate=16000:duration=6",
        "-af", "volume=enable='lt(mod(t,0.4),0.22)':volume=0:eval=frame",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(output),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and output.exists() and output.stat().st_size > 0:
        print(f"[gen_test_audio] ffmpeg fallback audio written → {output}")
        return True
    print(f"[gen_test_audio] ffmpeg fallback failed: {r.stderr[-300:]}")
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="/tmp/test_speech.wav")
    p.add_argument(
        "--text",
        default="long",
        help="'short' for a ~4s sentence, 'long' for full paragraph, or any custom text",
    )
    args = p.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.text == "short":
        text = TEXT_SHORT
    elif args.text == "long":
        text = TEXT_LONG
    else:
        text = args.text

    print(f"[gen_test_audio] Generating audio for: {text[:80]!r}")
    if try_tts(output, text):
        pass
    elif ffmpeg_fallback(output):
        pass
    else:
        print("[gen_test_audio] FAILED: no TTS or ffmpeg fallback worked")
        return 1

    # Print duration
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
        capture_output=True, text=True,
    )
    dur = r.stdout.strip()
    print(f"[gen_test_audio] Output: {output}  duration={dur}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
