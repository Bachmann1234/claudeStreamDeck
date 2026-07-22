"""Persistent tag -> session mapping for the Tier 0 manager.

The registry is the whole point of Tier 0: identity lives in the *manager*, not
in Ghostty (which forgets everything on restart and exposes no caller tag on
1.3.1). It is a small JSON file guarded by an advisory lock so a future daemon
and a CLI invocation can share it safely.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_home() -> Path:
    """Config dir, overridable via ``GSM_HOME`` (used by tests)."""
    env = os.environ.get("GSM_HOME")
    return Path(env).expanduser() if env else Path.home() / ".claudeStreamDeck"


@dataclass
class Session:
    """One tracked Claude Code session bound to a Ghostty surface."""

    tag: str
    uuid: str
    source: str = "spawned"  # "spawned" | "adopted"
    tty: str | None = None
    working_directory: str | None = None
    command: str | None = None
    created_at: str = field(default_factory=_now)
    last_focused_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        known = {f: data.get(f) for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        # created_at may be absent in hand-edited files; fill it.
        if not known.get("created_at"):
            known["created_at"] = _now()
        return cls(**known)


class Registry:
    """A JSON-backed ``tag -> Session`` store with file locking.

    Every mutating operation is a full read-modify-write under an exclusive
    lock, so concurrent CLI/daemon writers never interleave.
    """

    def __init__(self, path: Path | None = None):
        home = default_home()
        self.path = path or (home / "registry.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- lock + io ---------------------------------------------------------

    @contextmanager
    def _locked(self):
        # Lock a sidecar file so we can atomically replace the data file itself.
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Session]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}
        sessions = raw.get("sessions", {})
        return {tag: Session.from_dict(d) for tag, d in sessions.items()}

    def _write(self, sessions: dict[str, Session]) -> None:
        payload = {"sessions": {tag: asdict(s) for tag, s in sessions.items()}}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)  # atomic on POSIX

    # -- public api --------------------------------------------------------

    def all(self) -> dict[str, Session]:
        with self._locked():
            return self._read()

    def get(self, tag: str) -> Session | None:
        with self._locked():
            return self._read().get(tag)

    def upsert(self, session: Session) -> None:
        with self._locked():
            sessions = self._read()
            existing = sessions.get(session.tag)
            if existing is not None:
                # Preserve creation time on update.
                session.created_at = existing.created_at
            sessions[session.tag] = session
            self._write(sessions)

    def remove(self, tag: str) -> bool:
        with self._locked():
            sessions = self._read()
            if tag in sessions:
                del sessions[tag]
                self._write(sessions)
                return True
            return False

    def touch_focused(self, tag: str) -> None:
        with self._locked():
            sessions = self._read()
            if tag in sessions:
                sessions[tag].last_focused_at = _now()
                self._write(sessions)
