"""The wire protocol between Claude Code hooks and the daemon.

One message per line of JSON on the unix socket. The shape (a superset of the
M2 spec — the extra fields let the hook do correlation itself, see
``docs/correlation-rationale.md``)::

    {"session_id": "abc123", "event": "SessionStart", "state": "starting",
     "label": "repo-x", "tty": "/dev/ttys004", "uuid": "<ghostty-surface>",
     "cwd": "/Users/me/code/repo-x"}

Only ``session_id`` is strictly required. ``event`` (Claude Code's
``hook_event_name``) and/or ``state`` drive the key; ``uuid`` is the resolved
Ghostty surface used for focus. Everything else is best-effort metadata.

Parsing is intentionally lenient about *unknown* keys (forward compatibility)
but strict about a missing ``session_id`` — a message we can't attribute to a
session is useless, so we reject it loudly rather than silently dropping state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


class ProtocolError(ValueError):
    """A socket line was not a valid, attributable message."""


@dataclass(frozen=True)
class Message:
    """A single parsed state report from a hook."""

    session_id: str
    event: str | None = None
    state: str | None = None
    label: str | None = None
    tty: str | None = None
    uuid: str | None = None
    cwd: str | None = None

    def to_json(self) -> str:
        payload = {
            "session_id": self.session_id,
            "event": self.event,
            "state": self.state,
            "label": self.label,
            "tty": self.tty,
            "uuid": self.uuid,
            "cwd": self.cwd,
        }
        # Drop Nones so a fire-and-forget hook sends a compact line.
        return json.dumps({k: v for k, v in payload.items() if v is not None})


def _clean(value: object) -> str | None:
    """Coerce a JSON scalar to a non-empty stripped string, else None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_message(line: str) -> Message:
    """Parse one JSON line into a :class:`Message`.

    Raises :class:`ProtocolError` on malformed JSON, a non-object payload, or a
    missing ``session_id``. This is the daemon's trust boundary — everything
    past it can assume a well-formed, attributable message.
    """
    text = line.strip()
    if not text:
        raise ProtocolError("empty line")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ProtocolError(f"expected a JSON object, got {type(raw).__name__}")

    session_id = _clean(raw.get("session_id"))
    if not session_id:
        raise ProtocolError("missing required field 'session_id'")

    return Message(
        session_id=session_id,
        event=_clean(raw.get("event")),
        state=_clean(raw.get("state")),
        label=_clean(raw.get("label")),
        tty=_clean(raw.get("tty")),
        uuid=_clean(raw.get("uuid")),
        cwd=_clean(raw.get("cwd")),
    )
