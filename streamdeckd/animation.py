"""Frame animation — the blinking "needs you" key and the spinning "working" key.

Pure functions here; the daemon runs a tiny background ticker that calls
:func:`animate_frame` a few times a second and re-renders. Keeping the math
clock-free makes it unit-testable with an explicit elapsed time.

Two animations, one per active state, distinguished by :func:`animation_kind`:

- **blink** (ATTENTION): the key shows a big ``?`` that blinks on and off — a
  loud "answer me". Carried as ``blink_on`` on :class:`KeyAppearance`; the
  renderer draws the ``?`` only while it's on. A still preview (VirtualDeck)
  leaves ``blink_on`` at its ``True`` default, so it shows a steady ``?``.
- **spin** (WORKING): a short arc rotates around the key edge — a loading
  spinner. Carried as a ``spin`` phase and drawn by
  :func:`streamdeckd.renderer.draw_spinner`. The phase is quantized to
  :data:`SPIN_STEPS` positions so the renderer's per-appearance image cache
  stays small across many revolutions.

Only the hardware renderer animates; the VirtualDeck's frames are files.
"""

from __future__ import annotations

from dataclasses import replace

from .state import KeyAppearance, KeyState

# Blink (ATTENTION): full on/off period. Half on, half off -> a clear blink.
BLINK_PERIOD_S = 0.9

# Spin (WORKING): one revolution period, and how many discrete arc positions to
# render per revolution (fewer = a smaller image cache, coarser motion).
SPIN_PERIOD_S = 1.1
SPIN_STEPS = 24


def _phase(elapsed_s: float, period_s: float) -> float:
    """Normalized phase in [0, 1) for ``elapsed_s`` against a period."""
    return (elapsed_s % period_s) / period_s


def blink_on(elapsed_s: float) -> bool:
    """Whether the ``?`` is showing this instant (on for the first half-period)."""
    return _phase(elapsed_s, BLINK_PERIOD_S) < 0.5


def spin_phase(elapsed_s: float) -> float:
    """Rotation phase in [0, 1), quantized to :data:`SPIN_STEPS` positions."""
    raw = _phase(elapsed_s, SPIN_PERIOD_S)
    return (round(raw * SPIN_STEPS) % SPIN_STEPS) / SPIN_STEPS


def animation_kind(appearance: KeyAppearance) -> str | None:
    """Which animation a key wants: ``"blink"``, ``"spin"``, or ``None``."""
    if appearance.pulse:  # the "needs you" marker
        return "blink"
    if appearance.state is KeyState.WORKING:
        return "spin"
    return None


def has_animation(keys: list[KeyAppearance]) -> bool:
    """True if any key wants motion (so the daemon can idle otherwise)."""
    return any(animation_kind(k) is not None for k in keys)


def animate_frame(keys: list[KeyAppearance], elapsed_s: float) -> list[KeyAppearance]:
    """A copy of ``keys`` with each animated key advanced to ``elapsed_s``.

    Attention keys get a ``blink_on`` toggle; working keys get a ``spin`` phase.
    Calm keys pass through untouched (same object). Flags and labels preserved.
    """
    out: list[KeyAppearance] = []
    for k in keys:
        kind = animation_kind(k)
        if kind == "blink":
            out.append(replace(k, blink_on=blink_on(elapsed_s)))
        elif kind == "spin":
            out.append(replace(k, spin=spin_phase(elapsed_s)))
        else:
            out.append(k)
    return out
