"""The key animations: blink (attention "?") + spin (working) rotation."""

import pytest

from streamdeckd.animation import (
    BLINK_PERIOD_S,
    SPIN_PERIOD_S,
    SPIN_STEPS,
    animate_frame,
    animation_kind,
    blink_on,
    has_animation,
    spin_phase,
)
from streamdeckd.state import KeyState, appearance_for


# -- blink math ------------------------------------------------------------


def test_blink_on_first_half_off_second_half():
    assert blink_on(0.0) is True
    assert blink_on(BLINK_PERIOD_S * 0.25) is True
    assert blink_on(BLINK_PERIOD_S * 0.75) is False
    assert blink_on(BLINK_PERIOD_S) is True  # wraps to the next cycle


# -- spin math -------------------------------------------------------------


def test_spin_phase_wraps_and_quantizes():
    assert spin_phase(0.0) == pytest.approx(0.0)
    assert spin_phase(SPIN_PERIOD_S) == pytest.approx(0.0)  # full revolution
    for i in range(200):
        p = spin_phase(i * 0.017)
        assert p == pytest.approx(round(p * SPIN_STEPS) / SPIN_STEPS)


# -- classification --------------------------------------------------------


def test_animation_kind_per_state():
    assert animation_kind(appearance_for(KeyState.ATTENTION)) == "blink"
    assert animation_kind(appearance_for(KeyState.WORKING)) == "spin"
    assert animation_kind(appearance_for(KeyState.DONE)) is None
    assert animation_kind(appearance_for(KeyState.STARTING)) is None


def test_has_animation_true_for_working_or_attention():
    assert not has_animation([appearance_for(KeyState.DONE)])
    assert has_animation([appearance_for(KeyState.WORKING)])
    assert has_animation([appearance_for(KeyState.ATTENTION)])


# -- frame modulation ------------------------------------------------------


def test_animate_frame_toggles_blink_on_attention():
    attention = appearance_for(KeyState.ATTENTION, "a")
    on = animate_frame([attention], elapsed_s=0.0)[0]
    off = animate_frame([attention], elapsed_s=BLINK_PERIOD_S * 0.75)[0]
    assert on.blink_on is True and off.blink_on is False
    assert on.pulse is True and off.pulse is True  # still the attention key
    assert on.color == attention.color  # blink doesn't touch the fill


def test_animate_frame_stamps_spin_on_working_key():
    working = appearance_for(KeyState.WORKING, "w")
    assert working.spin is None
    out = animate_frame([working], elapsed_s=SPIN_PERIOD_S / 4)[0]
    assert out.spin is not None and 0.0 <= out.spin < 1.0
    assert out.color == working.color and out.label == "w"


def test_animate_frame_leaves_calm_keys_untouched():
    done = appearance_for(KeyState.DONE, "d")
    out = animate_frame([done], elapsed_s=0.3)
    assert out[0] is done  # same object, no copy
