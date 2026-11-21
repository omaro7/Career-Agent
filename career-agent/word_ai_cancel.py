"""Per-run cooperative cancellation for the single-worker local career-agent.

Cancellation is checked for every Ollama streaming token. A cancelled run ID
received before its stream starts is retained briefly to cover request races.
"""
from __future__ import annotations

import time
from threading import Event, Lock
from uuid import UUID

_lock = Lock()
_events: dict[str, tuple[Event, float]] = {}
_active: set[str] = set()
_TTL_SECONDS = 1800.0


class WordAICancelled(Exception):
    """Intentional user-requested cancellation, not a model failure."""


def _key(run_id: UUID | str) -> str:
    return str(UUID(str(run_id)))


def _prune(now: float) -> None:
    for key, (_, last) in list(_events.items()):
        if key not in _active and now - last > _TTL_SECONDS:
            _events.pop(key, None)


def register(run_id: UUID | str) -> Event:
    key = _key(run_id)
    with _lock:
        now = time.monotonic()
        _prune(now)
        event = _events.get(key, (Event(), now))[0]
        _events[key] = (event, now)
        _active.add(key)
        return event


def signal_cancel(run_id: UUID | str) -> bool:
    """Return whether a stream for this run ID is currently active."""
    key = _key(run_id)
    with _lock:
        now = time.monotonic()
        _prune(now)
        event = _events.get(key, (Event(), now))[0]
        event.set()
        _events[key] = (event, now)
        return key in _active


def finish(run_id: UUID | str) -> None:
    key = _key(run_id)
    with _lock:
        _active.discard(key)
        _events.pop(key, None)


def check(event: Event) -> None:
    if event.is_set():
        raise WordAICancelled('사용자가 AI 분석을 취소했습니다.')
