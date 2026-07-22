"""Frame animation — the breathing "needs you" key.

Pure functions here; the daemon runs a tiny background ticker that calls
:func:`animate_frame` a few times a second and re-renders. Keeping the math
clock-free and thread-free makes it unit-testable with an explicit ``phase``.

Only :data:`~streamdeckd.state.KeyAppearance.pulse` keys animate (today just
ATTENTION). The fill "breathes" between full and dimmed; the label's contrast
colour is computed from the key's *base* state colour in the renderer, so it
never flickers black↔white as the fill dims. The static white ring stays, so a
still preview (VirtualDeck PNG) still reads as attention without motion.
"""

from __future__ import annotations

import math
from dataclasses import replace

from .state import KeyAppearance

# One full breath, and how dim the fill gets at the bottom of it. A deep swing
# (down to a quarter brightness) makes the "needs you" pulse obvious at a
# glance; the label colour is pinned to the base state so text stays legible
# even at the trough.
PULSE_PERIOD_S = 1.3
PULSE_MIN = 0.25  # fraction of the base colour at the dimmest point
PULSE_MAX = 1.0


def pulse_factor(phase: float) -> float:
    """Breath multiplier for a normalized ``phase`` in [0, 1).

    Smooth cosine ease: dimmest (:data:`PULSE_MIN`) at phase 0/1, full at 0.5.
    """
    wave = (1 - math.cos(2 * math.pi * phase)) / 2  # 0 -> 1 -> 0
    return PULSE_MIN + (PULSE_MAX - PULSE_MIN) * wave


def phase_at(elapsed_s: float) -> float:
    """The breath phase for a monotonic ``elapsed_s`` seconds."""
    return (elapsed_s % PULSE_PERIOD_S) / PULSE_PERIOD_S


def _scale(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, round(c * factor))) for c in color)


def has_animation(keys: list[KeyAppearance]) -> bool:
    """True if any key wants motion (so the daemon can idle otherwise)."""
    return any(k.pulse for k in keys)


def animate_frame(keys: list[KeyAppearance], phase: float) -> list[KeyAppearance]:
    """A copy of ``keys`` with each pulsing key's fill scaled by the breath.

    Non-pulsing keys pass through untouched (same object). The ``pulse`` flag and
    ``label`` are preserved, so the renderer still draws the ring and text.
    """
    factor = pulse_factor(phase)
    return [
        replace(k, color=_scale(k.color, factor)) if k.pulse else k
        for k in keys
    ]
