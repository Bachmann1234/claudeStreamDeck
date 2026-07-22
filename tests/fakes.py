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
        self.spawns: list[dict] = []
        self.tabs = 0
        self.windows_open = False   # does has_open_window() report a window?
        self.tab_error = None       # set to an Exception to simulate no Accessibility

    def focus(self, uuid: str) -> None:
        self.focused.append(uuid)
        if uuid not in self.live:
            raise DeadSurface(f"gone: {uuid}", code=-1728)

    def spawn_window(self, *, command=None, working_directory=None, **kw) -> str:
        uuid = f"spawned-{len(self.spawns)}"
        self.spawns.append({"command": command, "working_directory": working_directory})
        self.live.add(uuid)
        return uuid

    def has_open_window(self) -> bool:
        return self.windows_open

    def open_new_tab(self) -> None:
        if self.tab_error is not None:
            raise self.tab_error
        self.tabs += 1

    def kill(self, uuid: str) -> None:
        self.live.discard(uuid)


class FakeDeck:
    """A hardware-free stand-in for a ``streamdeck`` device.

    Implements only the surface :class:`StreamDeckRenderer` touches. Reports the
    real Stream Deck Original image format so ``PILHelper`` formats images
    exactly as it would for the physical board (72×72 JPEG, flip both), without
    any USB access. ``set_key_image`` just records the native bytes per key.
    """

    ORIGINAL_FORMAT = {
        "size": (72, 72),
        "format": "JPEG",
        "flip": (True, True),
        "rotation": 0,
    }

    def __init__(self, keys: int = 15):
        self._keys = keys
        self.images: dict[int, bytes] = {}
        self.brightness: int | None = None
        self.callback = None
        self.reset_count = 0
        self.closed = False

    # -- streamdeck device API (subset) ------------------------------------
    def key_count(self) -> int:
        return self._keys

    def key_image_format(self) -> dict:
        return dict(self.ORIGINAL_FORMAT)

    def deck_type(self) -> str:
        return "Stream Deck Original (fake)"

    def set_brightness(self, pct: int) -> None:
        self.brightness = pct

    def set_key_callback(self, cb) -> None:
        self.callback = cb

    def set_key_image(self, key: int, image: bytes) -> None:
        self.images[key] = image

    def reset(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.closed = True

    # -- test helper -------------------------------------------------------
    def press(self, key: int) -> None:
        """Simulate a physical down+up press through the registered callback."""
        if self.callback:
            self.callback(self, key, True)
            self.callback(self, key, False)


class RecordingRenderer:
    """Renderer that keeps every frame it was asked to paint.

    ``animated`` mirrors the real renderers' opt-in flag so the daemon's
    animation ticker can be exercised against it.
    """

    def __init__(self, key_count: int = 15, *, animated: bool = False):
        self.key_count = key_count
        self.animated = animated
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
