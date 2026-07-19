"""Key allocation + the event/state -> appearance mapping."""

import pytest

from streamdeckd.protocol import Message
from streamdeckd.state import (
    APPEARANCE,
    RELEASE,
    KeyState,
    SessionModel,
    appearance_for,
    resolve_state,
)


def _msg(session_id, event=None, **kw):
    return Message(session_id=session_id, event=event, **kw)


# -- resolve_state ---------------------------------------------------------


@pytest.mark.parametrize(
    "event,expected",
    [
        ("SessionStart", KeyState.STARTING),
        ("UserPromptSubmit", KeyState.WORKING),
        ("PreToolUse", KeyState.WORKING),
        ("Notification", KeyState.ATTENTION),
        ("Stop", KeyState.DONE),
        ("SessionEnd", RELEASE),
    ],
)
def test_event_to_state(event, expected):
    assert resolve_state(event=event, state=None) is expected


def test_explicit_state_overrides_event():
    assert resolve_state(event="SessionStart", state="working") is KeyState.WORKING


def test_unknown_event_is_no_change():
    assert resolve_state(event="PreCompact", state=None) is None


def test_unknown_explicit_state_falls_back_to_event():
    assert resolve_state(event="Stop", state="bogus") is KeyState.DONE


def test_release_via_explicit_state():
    assert resolve_state(event=None, state="release") is RELEASE


# -- allocation ------------------------------------------------------------


def test_sessionstart_allocates_lowest_free_key():
    m = SessionModel(key_count=15)
    a = m.apply(_msg("a", "SessionStart"))
    b = m.apply(_msg("b", "SessionStart"))
    assert a.slot.key_index == 0
    assert b.slot.key_index == 1
    assert a.action == "allocated"


def test_key_is_stable_across_updates():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart"))
    m.apply(_msg("b", "SessionStart"))
    for event in ("UserPromptSubmit", "Notification", "Stop"):
        r = m.apply(_msg("a", event))
        assert r.slot.key_index == 0  # never jumps mid-life
        assert r.action == "updated"


def test_release_frees_key_for_reuse():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart"))  # key 0
    m.apply(_msg("b", "SessionStart"))  # key 1
    rel = m.apply(_msg("a", "SessionEnd"))
    assert rel.released and rel.slot.session_id == "a"
    assert m.get("a") is None
    # The freed key 0 is now the lowest free slot again.
    c = m.apply(_msg("c", "SessionStart"))
    assert c.slot.key_index == 0


def test_release_unknown_session_is_ignored():
    m = SessionModel()
    r = m.apply(_msg("ghost", "SessionEnd"))
    assert r.action == "ignored" and r.slot is None


def test_overflow_tracks_without_key():
    m = SessionModel(key_count=2)
    m.apply(_msg("a", "SessionStart"))
    m.apply(_msg("b", "SessionStart"))
    over = m.apply(_msg("c", "SessionStart"))
    assert over.action == "overflow"
    assert over.slot.key_index is None
    assert m.get("c") is not None  # still tracked


def test_unknown_session_non_start_event_still_allocates():
    # A daemon (re)started mid-session first hears about "a" via a Stop.
    m = SessionModel()
    r = m.apply(_msg("a", "Stop"))
    assert r.action == "allocated"
    assert r.slot.state is KeyState.DONE


def test_uuid_and_metadata_updated_in_place():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart", cwd="/w/repo"))
    m.apply(_msg("a", "UserPromptSubmit", uuid="U-9", tty="/dev/ttys004"))
    slot = m.get("a")
    assert slot.uuid == "U-9" and slot.tty == "/dev/ttys004"


def test_default_label_from_cwd_basename():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart", cwd="/Users/me/code/repo-x"))
    assert m.get("a").label == "repo-x"


def test_explicit_label_wins():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart", cwd="/w/repo", label="my-label"))
    assert m.get("a").label == "my-label"


# -- appearance snapshot ---------------------------------------------------


def test_snapshot_maps_states_to_colors():
    m = SessionModel(key_count=15)
    m.apply(_msg("a", "SessionStart"))       # key 0 -> starting
    m.apply(_msg("b", "SessionStart"))       # key 1
    m.apply(_msg("b", "UserPromptSubmit"))   # key 1 -> working
    m.apply(_msg("c", "SessionStart"))       # key 2
    m.apply(_msg("c", "Notification"))       # key 2 -> attention (pulse)

    keys = m.snapshot_keys()
    assert len(keys) == 15
    assert keys[0].state is KeyState.STARTING
    assert keys[1].state is KeyState.WORKING
    assert keys[1].color == APPEARANCE[KeyState.WORKING].color
    assert keys[2].state is KeyState.ATTENTION and keys[2].pulse is True
    # Unused keys are blank.
    assert all(k.state is KeyState.EMPTY for k in keys[3:])


def test_snapshot_carries_label():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart", label="repo-x"))
    assert m.snapshot_keys()[0].label == "repo-x"


def test_appearance_for_stamps_label():
    ap = appearance_for(KeyState.DONE, "hello")
    assert ap.label == "hello" and ap.state is KeyState.DONE


def test_key_count_must_be_positive():
    with pytest.raises(ValueError):
        SessionModel(key_count=0)
