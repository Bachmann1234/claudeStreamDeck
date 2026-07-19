"""Shared test doubles for the streamdeckd suite (no osascript, no hardware)."""

from __future__ import annotations

from gsm.applescript import DeadSurface


class FakeGhostty:
    """Minimal Ghostty stand-in for the daemon's focus path.

    Only implements what :class:`gsm.Manager.focus` touches: ``focus`` (which
    raises :class:`DeadSurface` for an unknown uuid, matching stock 1.3.1's
    ``-1728``). ``bind`` never calls Ghostty, so nothing else is needed.
    """

    def __init__(self, live=("U1", "U2", "U3")):
        self.live = set(live)
        self.focused: list[str] = []

    def focus(self, uuid: str) -> None:
        self.focused.append(uuid)
        if uuid not in self.live:
            raise DeadSurface(f"gone: {uuid}", code=-1728)

    def kill(self, uuid: str) -> None:
        self.live.discard(uuid)


class RecordingRenderer:
    """Renderer that keeps every frame it was asked to paint."""

    def __init__(self, key_count: int = 15):
        self.key_count = key_count
        self.frames: list[list] = []
        self.closed = False

    def render(self, keys) -> None:
        assert len(keys) == self.key_count
        self.frames.append(list(keys))

    def close(self) -> None:
        self.closed = True

    @property
    def last(self):
        return self.frames[-1] if self.frames else None
