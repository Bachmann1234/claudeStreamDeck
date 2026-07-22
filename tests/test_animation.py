"""The breathing-key animation: pure pulse math + frame modulation."""

import pytest

from streamdeckd.animation import (
    PULSE_MAX,
    PULSE_MIN,
    PULSE_PERIOD_S,
    animate_frame,
    has_animation,
    phase_at,
    pulse_factor,
)
from streamdeckd.state import KeyState, appearance_for


def test_pulse_factor_endpoints_are_dim():
    assert pulse_factor(0.0) == pytest.approx(PULSE_MIN)
    assert pulse_factor(1.0) == pytest.approx(PULSE_MIN)


def test_pulse_factor_peak_is_full_at_half():
    assert pulse_factor(0.5) == pytest.approx(PULSE_MAX)


def test_pulse_factor_stays_in_range():
    for i in range(101):
        f = pulse_factor(i / 100)
        assert PULSE_MIN - 1e-9 <= f <= PULSE_MAX + 1e-9


def test_phase_at_wraps_on_period():
    assert phase_at(0.0) == pytest.approx(0.0)
    assert phase_at(PULSE_PERIOD_S) == pytest.approx(0.0)
    assert phase_at(PULSE_PERIOD_S / 2) == pytest.approx(0.5)


def test_has_animation_only_when_a_key_pulses():
    calm = [appearance_for(KeyState.WORKING), appearance_for(KeyState.DONE)]
    assert not has_animation(calm)
    assert has_animation(calm + [appearance_for(KeyState.ATTENTION)])


def test_animate_frame_dims_only_pulsing_keys():
    working = appearance_for(KeyState.WORKING, "w")   # not pulsing
    attention = appearance_for(KeyState.ATTENTION, "a")  # pulsing
    out = animate_frame([working, attention], phase=0.0)  # dimmest
    # Non-pulsing key is the very same object, untouched.
    assert out[0] is working
    # Pulsing key is dimmed toward black but keeps its flags/label.
    assert out[1].color != attention.color
    assert all(o <= b for o, b in zip(out[1].color, attention.color))
    assert out[1].pulse is True and out[1].label == "a" and out[1].state is attention.state


def test_animate_frame_full_brightness_at_peak():
    attention = appearance_for(KeyState.ATTENTION)
    out = animate_frame([attention], phase=0.5)  # PULSE_MAX == 1.0
    assert out[0].color == attention.color
