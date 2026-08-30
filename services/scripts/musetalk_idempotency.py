"""Thread-safe in-memory coordination for MuseTalk request idempotency."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IdempotencyEntry:
    run_id: str
    fingerprint: str
    output_path: str
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    state: str = "in_progress"
    status_code: int = 0
    payload: dict[str, Any] | None = None
    completed: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class IdempotencyReservation:
    action: str
    entry: IdempotencyEntry | None = None


class IdempotencyCoordinator:
    """Coordinate owners, followers, and bounded completed-result replay."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, IdempotencyEntry] = {}

    def reserve(
        self,
        *,
        run_id: str,
        fingerprint: str,
        output_path: str,
        ttl_seconds: float,
        max_entries: int,
    ) -> IdempotencyReservation:
        if not run_id:
            return IdempotencyReservation("bypass")

        with self._lock:
            self._prune_locked(ttl_seconds=ttl_seconds, max_entries=max_entries)
            existing = self._entries.get(run_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    return IdempotencyReservation("conflict", existing)
                if existing.state == "in_progress":
                    return IdempotencyReservation("follower", existing)
                return IdempotencyReservation("replay", existing)

            entry = IdempotencyEntry(
                run_id=run_id,
                fingerprint=fingerprint,
                output_path=output_path,
            )
            self._entries[run_id] = entry
            self._prune_locked(ttl_seconds=ttl_seconds, max_entries=max_entries)
            return IdempotencyReservation("owner", entry)

    def complete(
        self,
        entry: IdempotencyEntry,
        *,
        status_code: int,
        payload: dict[str, Any],
        retain: bool,
    ) -> None:
        with self._lock:
            entry.status_code = int(status_code)
            entry.payload = dict(payload)
            entry.state = "completed"
            entry.updated_at = time.monotonic()
            entry.completed.set()
            if not retain and self._entries.get(entry.run_id) is entry:
                self._entries.pop(entry.run_id, None)

    def wait(
        self,
        entry: IdempotencyEntry,
        *,
        timeout_seconds: float,
    ) -> tuple[int, dict[str, Any]] | None:
        if not entry.completed.wait(timeout=max(float(timeout_seconds), 0.0)):
            return None
        if entry.payload is None or entry.status_code <= 0:
            return None
        return int(entry.status_code), dict(entry.payload)

    def forget(self, entry: IdempotencyEntry) -> None:
        with self._lock:
            if self._entries.get(entry.run_id) is entry:
                self._entries.pop(entry.run_id, None)

    def stats(self, *, ttl_seconds: float, max_entries: int) -> dict[str, int | float]:
        with self._lock:
            self._prune_locked(ttl_seconds=ttl_seconds, max_entries=max_entries)
            entries = list(self._entries.values())
            return {
                "entries": len(entries),
                "in_progress": sum(entry.state == "in_progress" for entry in entries),
                "completed": sum(entry.state == "completed" for entry in entries),
                "ttl_seconds": round(float(ttl_seconds), 3),
                "max_entries": int(max_entries),
            }

    def clear(self) -> None:
        """Clear coordinator state; intended for isolated tests."""
        with self._lock:
            self._entries.clear()

    def _prune_locked(self, *, ttl_seconds: float, max_entries: int) -> None:
        now = time.monotonic()
        expired = [
            run_id
            for run_id, entry in self._entries.items()
            if entry.state == "completed" and now - entry.updated_at > max(float(ttl_seconds), 0.0)
        ]
        for run_id in expired:
            self._entries.pop(run_id, None)

        completed = sorted(
            (entry for entry in self._entries.values() if entry.state == "completed"),
            key=lambda entry: entry.updated_at,
        )
        overflow = max(len(self._entries) - max(int(max_entries), 1), 0)
        for entry in completed[:overflow]:
            self._entries.pop(entry.run_id, None)
