"""``StreamDeckRenderer`` — the real hardware :class:`Renderer` (M1/M6).

This is the one place that touches the USB HID device. It formats the same
:class:`~streamdeckd.state.KeyAppearance` frames the :class:`VirtualDeck`
serializes to PNGs, but paints them onto physical keys via the
``streamdeck`` library's ``PILHelper`` (which handles the device-specific key
size, JPEG encoding, and the Original's ``flip=(True, True)``).

The daemon is unchanged: it calls :meth:`render` / :meth:`close` through the
:class:`Renderer` protocol. Presses flow the *other* way — the HID callback
invokes :attr:`on_press`, which ``cli.py`` wires to ``Daemon.press`` — the same
entry point the socket's ``{"press": N}`` control command uses. So a physical
press and a scripted press take the identical focus path.

Import is lazy/guarded so the package still imports on a machine without the
``streamdeck`` library or a deck attached; only constructing this class needs
the hardware.
"""

from __future__ import annotations

import logging
from typing import Callable

from PIL import ImageDraw

from .renderer import _draw_centered, _readable_text_color
from .state import KeyAppearance, KeyState, appearance_for

log = logging.getLogger("streamdeckd.hw")


class StreamDeckRenderer:
    """Paint a real Elgato Stream Deck and forward key presses.

    Construct via :meth:`open_first` (enumerate + open the attached deck) in
    production, or pass an already-opened ``deck`` directly (a fake in tests).
    Only keys whose appearance actually changed are re-pushed each frame, since
    a USB image write is far costlier than the dict comparison that gates it.
    """

    def __init__(
        self,
        deck,
        *,
        brightness: int = 60,
        on_press: Callable[[int], None] | None = None,
    ):
        self.deck = deck
        self.key_count = deck.key_count()
        self.on_press = on_press
        # None means "never painted" so the first render always pushes every key.
        self._last: list[KeyAppearance | None] = [None] * self.key_count
        # Cache native (JPEG) bytes per distinct appearance — labels repeat.
        self._native_cache: dict[KeyAppearance, bytes] = {}

        deck.reset()
        deck.set_brightness(brightness)
        deck.set_key_callback(self._on_hid_event)
        log.info(
            "opened %s — %d keys, brightness %d%%",
            _deck_type(deck),
            self.key_count,
            brightness,
        )

    @classmethod
    def open_first(cls, **kwargs) -> "StreamDeckRenderer":
        """Enumerate, open the first deck, and wrap it. Raises if none found."""
        from StreamDeck.DeviceManager import DeviceManager

        decks = DeviceManager().enumerate()
        if not decks:
            raise RuntimeError(
                "no Stream Deck found — is it plugged in and the Elgato app quit?"
            )
        deck = decks[0]
        deck.open()
        return cls(deck, **kwargs)

    # -- Renderer protocol -------------------------------------------------

    def render(self, keys: list[KeyAppearance]) -> None:
        if len(keys) != self.key_count:
            raise ValueError(f"expected {self.key_count} keys, got {len(keys)}")
        for i, appearance in enumerate(keys):
            if appearance == self._last[i]:
                continue  # unchanged — skip the USB write
            try:
                self.deck.set_key_image(i, self._native_for(appearance))
                self._last[i] = appearance
            except Exception:  # pragma: no cover - transient HID hiccup
                # Don't let one bad key write take the daemon down; drop the
                # cache entry so the next frame retries this key.
                log.exception("failed to paint key %d", i)
                self._last[i] = None

    def close(self) -> None:
        """Blank the deck and release the USB device."""
        try:
            self.deck.reset()
        finally:
            self.deck.close()

    # -- HID press -> daemon ----------------------------------------------

    def _on_hid_event(self, _deck, key: int, pressed: bool) -> None:
        """streamdeck callback: fire :attr:`on_press` on the *down* edge only."""
        if pressed and self.on_press is not None:
            try:
                self.on_press(key)
            except Exception:  # pragma: no cover - never crash the HID thread
                log.exception("on_press handler failed for key %d", key)

    # -- image formatting --------------------------------------------------

    def _native_for(self, appearance: KeyAppearance) -> bytes:
        cached = self._native_cache.get(appearance)
        if cached is None:
            cached = self._render_native(appearance)
            self._native_cache[appearance] = cached
        return cached

    def _render_native(self, appearance: KeyAppearance) -> bytes:
        from StreamDeck.ImageHelpers import PILHelper

        img = PILHelper.create_image(self.deck, background=appearance.color)
        draw = ImageDraw.Draw(img)
        size = img.width  # square key
        if appearance.pulse:
            # Static bright ring so an attention key reads as "look at me" even
            # without an animation tick (mirrors the VirtualDeck PNG).
            draw.rectangle(
                [2, 2, size - 3, size - 3], outline=(255, 255, 255), width=3
            )
        if appearance.label:
            shown = (
                appearance.label
                if len(appearance.label) <= 9
                else appearance.label[:8] + "…"
            )
            _draw_centered(draw, shown, size, _readable_text_color(appearance.color))
        return PILHelper.to_native_format(self.deck, img)


def _deck_type(deck) -> str:
    try:
        return deck.deck_type()
    except Exception:  # pragma: no cover
        return "Stream Deck"


def blank_frame(key_count: int) -> list[KeyAppearance]:
    """A full blank frame — handy for tests and explicit clears."""
    return [appearance_for(KeyState.EMPTY) for _ in range(key_count)]
