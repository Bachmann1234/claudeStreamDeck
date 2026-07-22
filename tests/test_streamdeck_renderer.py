"""Unit tests for the hardware renderer, exercised against a FakeDeck.

PILHelper runs for real (it's pure image processing); only the USB device is
faked, so these tests prove the exact image-formatting + change-detection +
press-forwarding logic that runs on the physical board.
"""

from __future__ import annotations

from fakes import FakeDeck

from streamdeckd.state import KeyState, appearance_for
from streamdeckd.streamdeck_renderer import StreamDeckRenderer, blank_frame


def _frame(states, key_count=15):
    keys = blank_frame(key_count)
    for i, s in states.items():
        keys[i] = appearance_for(s)
    return keys


def test_init_resets_and_sets_brightness():
    deck = FakeDeck()
    StreamDeckRenderer(deck, brightness=42)
    assert deck.reset_count == 1
    assert deck.brightness == 42
    assert deck.callback is not None


def test_render_paints_every_key_first_frame():
    deck = FakeDeck()
    r = StreamDeckRenderer(deck)
    r.render(blank_frame(deck.key_count()))
    assert set(deck.images) == set(range(deck.key_count()))
    # All native frames are non-empty JPEG bytes.
    assert all(isinstance(b, bytes) and b for b in deck.images.values())


def test_render_skips_unchanged_keys():
    deck = FakeDeck()
    r = StreamDeckRenderer(deck)
    r.render(_frame({0: KeyState.WORKING}))
    deck.images.clear()  # forget what was painted
    # Re-render an identical frame: nothing should be pushed again.
    r.render(_frame({0: KeyState.WORKING}))
    assert deck.images == {}


def test_render_repaints_only_changed_key():
    deck = FakeDeck()
    r = StreamDeckRenderer(deck)
    r.render(_frame({0: KeyState.WORKING, 1: KeyState.DONE}))
    deck.images.clear()
    r.render(_frame({0: KeyState.ATTENTION, 1: KeyState.DONE}))  # only key 0 changed
    assert set(deck.images) == {0}


def test_native_bytes_cached_across_keys():
    deck = FakeDeck()
    r = StreamDeckRenderer(deck)
    # Two keys with the identical (labelless) appearance -> one cache entry.
    r.render(_frame({0: KeyState.WORKING, 1: KeyState.WORKING}))
    assert len(r._native_cache) == 2  # EMPTY (the other 13) + WORKING


def test_press_forwards_down_edge_only():
    deck = FakeDeck()
    presses: list[int] = []
    StreamDeckRenderer(deck, on_press=presses.append)
    deck.press(7)  # one down + one up
    assert presses == [7]  # up edge ignored


def test_press_without_handler_is_safe():
    deck = FakeDeck()
    StreamDeckRenderer(deck)  # no on_press
    deck.press(3)  # must not raise


def test_close_blanks_and_releases():
    deck = FakeDeck()
    r = StreamDeckRenderer(deck)
    r.close()
    assert deck.closed is True
    assert deck.reset_count == 2  # one at init, one at close


def test_key_count_from_device():
    deck = FakeDeck(keys=15)
    r = StreamDeckRenderer(deck)
    assert r.key_count == 15
