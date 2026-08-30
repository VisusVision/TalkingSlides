from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "services" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from musetalk_recovery_proxy import RecoveryFaultState, create_server  # noqa: E402


class _UpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self._reply({"status": "ready", "ready_for_inference": True})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        self._reply({"success": True, "output_path": request.get("output_path")})

    def _reply(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _message: str, *_args) -> None:
        return


def test_proxy_injects_one_infer_failure_then_forwards(tmp_path):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    state_path = tmp_path / "fault-state.json"
    state = RecoveryFaultState(fail_infer_count=1, state_path=state_path)
    proxy = create_server(listen_port=0, upstream_port=upstream.server_port, state=state)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    try:
        health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{proxy.server_port}/health").read())
        assert health["ready_for_inference"] is True

        body = json.dumps({"output_path": "/tmp/avatar.mp4"}).encode("utf-8")
        first = urllib.request.Request(
            f"http://127.0.0.1:{proxy.server_port}/infer",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(first)
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert json.loads(exc.read())["fault"] == "first_infer_http_503"
        else:
            raise AssertionError("first inference request should receive the injected 503")

        second = urllib.request.Request(
            f"http://127.0.0.1:{proxy.server_port}/infer",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        forwarded = json.loads(urllib.request.urlopen(second).read())
        assert forwarded == {"success": True, "output_path": "/tmp/avatar.mp4"}
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()

    audit = json.loads(state_path.read_text(encoding="utf-8"))
    assert audit["version"] == "musetalk-recovery-fault-v2"
    assert audit["fault_mode"] == "pre_infer_http_503"
    assert audit["infer_requests"] == 2
    assert audit["injected_failures"] == 1
    assert audit["forwarded_requests"] == 2
    assert audit["forwarded_infer_requests"] == 1
    assert audit["dropped_responses"] == 0
