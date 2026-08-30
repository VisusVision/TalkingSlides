#!/usr/bin/env python3
"""Local-only MuseTalk fault proxy for repeatable recovery smoke tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class RecoveryFaultState:
    PRE_INFER_HTTP_503 = "pre_infer_http_503"
    POST_INFER_DISCONNECT = "post_infer_disconnect"
    FAULT_MODES = (PRE_INFER_HTTP_503, POST_INFER_DISCONNECT)

    def __init__(
        self,
        *,
        fail_infer_count: int,
        state_path: Path | None = None,
        fault_mode: str = PRE_INFER_HTTP_503,
    ) -> None:
        self.fail_infer_count = max(int(fail_infer_count), 0)
        self.state_path = state_path
        if fault_mode not in self.FAULT_MODES:
            raise ValueError(f"unsupported MuseTalk recovery fault mode: {fault_mode}")
        self.fault_mode = fault_mode
        self.infer_requests = 0
        self.injected_failures = 0
        self.forwarded_requests = 0
        self.forwarded_infer_requests = 0
        self.upstream_responses = 0
        self.dropped_responses = 0
        self.response_audit: list[dict[str, Any]] = []
        self.started_at_epoch = time.time()
        self._lock = threading.Lock()
        self._write()

    def register(self, *, method: str, path: str) -> tuple[str, int]:
        with self._lock:
            action = ""
            request_index = 0
            if method == "POST" and path == "/infer":
                self.infer_requests += 1
                request_index = self.infer_requests
                should_inject = self.injected_failures < self.fail_infer_count
                if should_inject:
                    self.injected_failures += 1
                    action = self.fault_mode
                if action != self.PRE_INFER_HTTP_503:
                    self.forwarded_requests += 1
                    self.forwarded_infer_requests += 1
            else:
                self.forwarded_requests += 1
            self._write()
            return action, request_index

    def record_upstream_response(
        self,
        *,
        method: str,
        path: str,
        request_index: int,
        status: int,
        request_body: bytes | None,
        response_body: bytes,
        dropped: bool,
    ) -> None:
        if method != "POST" or path != "/infer":
            return
        request_payload = self._json_dict(request_body or b"")
        response_payload = self._json_dict(response_body)
        output_path = str(
            response_payload.get("output_path")
            or request_payload.get("output_path")
            or ""
        )
        output_observation = self._output_observation(output_path)
        run_payload = request_payload.get("run")
        run_id = str(run_payload.get("run_id") or "") if isinstance(run_payload, dict) else ""
        audit = {
            "request_index": int(request_index),
            "status": int(status),
            "dropped": bool(dropped),
            "run_id": run_id,
            "success": bool(response_payload.get("success")),
            "idempotency_status": str(response_payload.get("idempotency_status") or ""),
            "idempotency_replayed": bool(response_payload.get("idempotency_replayed")),
            "response_sha256": hashlib.sha256(response_body).hexdigest(),
            "output": output_observation,
        }
        with self._lock:
            self.upstream_responses += 1
            if dropped:
                self.dropped_responses += 1
            self.response_audit.append(audit)
            self._write()

    @staticmethod
    def _json_dict(body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _output_observation(raw_path: str) -> dict[str, Any]:
        path = Path(raw_path) if raw_path else None
        if path is None or not path.is_file():
            return {"present": False, "size_bytes": 0, "sha256": ""}
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return {
            "present": True,
            "size_bytes": int(path.stat().st_size),
            "sha256": digest.hexdigest(),
        }

    def payload(self) -> dict[str, Any]:
        return {
            "version": "musetalk-recovery-fault-v2",
            "started_at_epoch": round(self.started_at_epoch, 6),
            "fault_mode": self.fault_mode,
            "fail_infer_count": self.fail_infer_count,
            "infer_requests": self.infer_requests,
            "injected_failures": self.injected_failures,
            "forwarded_requests": self.forwarded_requests,
            "forwarded_infer_requests": self.forwarded_infer_requests,
            "upstream_responses": self.upstream_responses,
            "dropped_responses": self.dropped_responses,
            "response_audit": list(self.response_audit),
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
            fault_action, request_index = state.register(method=self.command, path=self.path)
            if fault_action == RecoveryFaultState.PRE_INFER_HTTP_503:
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
                    status = int(response.status)
                    response_body = response.read()
                    content_type = response.headers.get("Content-Type", "application/json")
                    drop_response = (
                        fault_action == RecoveryFaultState.POST_INFER_DISCONNECT
                        and 200 <= status < 300
                    )
                    state.record_upstream_response(
                        method=self.command,
                        path=self.path,
                        request_index=request_index,
                        status=status,
                        request_body=body,
                        response_body=response_body,
                        dropped=drop_response,
                    )
                    if drop_response:
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        self.connection.close()
                        return
                    self._respond(status, response_body, content_type)
            except urllib.error.HTTPError as exc:
                response_body = exc.read()
                state.record_upstream_response(
                    method=self.command,
                    path=self.path,
                    request_index=request_index,
                    status=int(exc.code),
                    request_body=body,
                    response_body=response_body,
                    dropped=False,
                )
                self._respond(
                    int(exc.code),
                    response_body,
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
    parser.add_argument(
        "--fault-mode",
        choices=RecoveryFaultState.FAULT_MODES,
        default=RecoveryFaultState.PRE_INFER_HTTP_503,
    )
    parser.add_argument("--state-path", type=Path)
    args = parser.parse_args()
    if args.listen_port == args.upstream_port:
        parser.error("listen and upstream ports must differ")

    state = RecoveryFaultState(
        fail_infer_count=args.fail_infer_count,
        state_path=args.state_path,
        fault_mode=args.fault_mode,
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
                "fault_mode": args.fault_mode,
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
