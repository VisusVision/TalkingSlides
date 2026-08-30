#!/usr/bin/env python3
"""Local-only MuseTalk fault proxy for repeatable recovery smoke tests."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class RecoveryFaultState:
    def __init__(self, *, fail_infer_count: int, state_path: Path | None = None) -> None:
        self.fail_infer_count = max(int(fail_infer_count), 0)
        self.state_path = state_path
        self.infer_requests = 0
        self.injected_failures = 0
        self.forwarded_requests = 0
        self.started_at_epoch = time.time()
        self._lock = threading.Lock()
        self._write()

    def register(self, *, method: str, path: str) -> bool:
        with self._lock:
            inject = False
            if method == "POST" and path == "/infer":
                self.infer_requests += 1
                inject = self.injected_failures < self.fail_infer_count
                if inject:
                    self.injected_failures += 1
                else:
                    self.forwarded_requests += 1
            else:
                self.forwarded_requests += 1
            self._write()
            return inject

    def payload(self) -> dict[str, Any]:
        return {
            "version": "musetalk-recovery-fault-v1",
            "started_at_epoch": round(self.started_at_epoch, 6),
            "fail_infer_count": self.fail_infer_count,
            "infer_requests": self.infer_requests,
            "injected_failures": self.injected_failures,
            "forwarded_requests": self.forwarded_requests,
        }

    def _write(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.payload(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)


def create_server(
    *,
    listen_port: int,
    upstream_port: int,
    state: RecoveryFaultState,
) -> ThreadingHTTPServer:
    upstream = f"http://127.0.0.1:{int(upstream_port)}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "MuseTalkRecoveryProxy/1"

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

        def _handle(self) -> None:
            if state.register(method=self.command, path=self.path):
                self._respond(
                    503,
                    json.dumps(
                        {
                            "success": False,
                            "error": "fault_injection_transient_service_unavailable",
                            "fault": "first_infer_http_503",
                        }
                    ).encode("utf-8"),
                    "application/json",
                )
                return

            content_length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(content_length) if content_length else None
            request = urllib.request.Request(
                upstream + self.path,
                data=body,
                method=self.command,
                headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
            )
            try:
                with urllib.request.urlopen(request, timeout=3600) as response:
                    self._respond(
                        int(response.status),
                        response.read(),
                        response.headers.get("Content-Type", "application/json"),
                    )
            except urllib.error.HTTPError as exc:
                self._respond(
                    int(exc.code),
                    exc.read(),
                    exc.headers.get("Content-Type", "application/json"),
                )
            except Exception as exc:
                self._respond(
                    502,
                    json.dumps({"success": False, "error": f"fault_proxy_upstream_error:{exc}"}).encode("utf-8"),
                    "application/json",
                )

        def _respond(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, message: str, *args: Any) -> None:
            print(
                json.dumps(
                    {
                        "component": "musetalk_recovery_proxy",
                        "client": self.client_address[0],
                        "message": message % args,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    return ThreadingHTTPServer(("127.0.0.1", int(listen_port)), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", type=int, default=17861)
    parser.add_argument("--upstream-port", type=int, default=17860)
    parser.add_argument("--fail-infer-count", type=int, default=1)
    parser.add_argument("--state-path", type=Path)
    args = parser.parse_args()
    if args.listen_port == args.upstream_port:
        parser.error("listen and upstream ports must differ")

    state = RecoveryFaultState(
        fail_infer_count=args.fail_infer_count,
        state_path=args.state_path,
    )
    server = create_server(
        listen_port=args.listen_port,
        upstream_port=args.upstream_port,
        state=state,
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "listen": f"127.0.0.1:{args.listen_port}",
                "upstream": f"127.0.0.1:{args.upstream_port}",
                "fail_infer_count": args.fail_infer_count,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
