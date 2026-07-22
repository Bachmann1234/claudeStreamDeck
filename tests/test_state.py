"""Key allocation + the event/state -> appearance mapping."""

import pytest

from streamdeckd.protocol import Message
from streamdeckd.state import (
    APPEARANCE,
    LABEL_MAX_CHARS,
    RELEASE,
    KeyState,
    SessionModel,
    appearance_for,
    format_branch_label,
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
    # Deck full of same-priority sessions -> newcomer can't evict, is parked.
    m = SessionModel(key_count=2)
    m.apply(_msg("a", "SessionStart"))
    m.apply(_msg("b", "SessionStart"))
    over = m.apply(_msg("c", "SessionStart"))
    assert over.action == "overflow" and over.parked
    assert over.slot.key_index is None
    assert m.get("c") is not None  # still tracked
    assert [s.session_id for s in m.parked()] == ["c"]


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


# -- overflow: eviction & promotion ----------------------------------------


def _fill_done(m, n):
    """Fill n keys with finished (DONE) sessions, oldest first."""
    for i in range(n):
        m.apply(_msg(f"done{i}", "SessionStart"))
        m.apply(_msg(f"done{i}", "Stop"))


def test_new_session_evicts_a_finished_one_when_full():
    m = SessionModel(key_count=2)
    _fill_done(m, 2)  # keys 0,1 held by finished sessions
    r = m.apply(_msg("fresh", "SessionStart"))
    assert r.action == "allocated"  # it got a key by eviction
    assert r.slot.key_index is not None
    # Exactly one finished session was parked to make room.
    assert len(m.parked()) == 1
    assert m.parked()[0].session_id.startswith("done")


def test_eviction_is_lru_among_finished():
    m = SessionModel(key_count=2)
    _fill_done(m, 2)  # done0 finished before done1 -> done0 is older (LRU)
    m.apply(_msg("fresh", "SessionStart"))
    # The older finished session (done0) is the one evicted.
    assert m.get("done0").key_index is None
    assert m.get("done1").key_index is not None


def test_no_eviction_when_disabled():
    m = SessionModel(key_count=2, evict_finished_when_full=False)
    _fill_done(m, 2)
    r = m.apply(_msg("fresh", "SessionStart"))
    assert r.action == "overflow" and r.slot.key_index is None
    assert m.get("done0").key_index is not None  # nobody evicted


def test_equal_priority_does_not_evict():
    # A working session can't push out another working session.
    m = SessionModel(key_count=1)
    m.apply(_msg("a", "SessionStart"))
    m.apply(_msg("a", "UserPromptSubmit"))  # a: WORKING, key 0
    m.apply(_msg("b", "SessionStart"))
    r = m.apply(_msg("b", "UserPromptSubmit"))  # b wants a key, same priority
    assert r.parked and m.get("a").key_index == 0


def test_parked_session_that_needs_you_evicts_a_working_one():
    m = SessionModel(key_count=1)
    m.apply(_msg("a", "SessionStart"))
    m.apply(_msg("a", "UserPromptSubmit"))  # a: WORKING, holds key 0
    m.apply(_msg("b", "SessionStart"))       # b parked (equal/again lower)
    r = m.apply(_msg("b", "Notification"))    # b now ATTENTION -> outranks a
    assert r.slot.key_index == 0             # b took the key
    assert m.get("a").key_index is None       # a parked


def test_release_promotes_highest_priority_parked():
    m = SessionModel(key_count=1)
    m.apply(_msg("a", "SessionStart"))
    m.apply(_msg("a", "Notification"))       # a: ATTENTION, key 0 (unevictable)
    m.apply(_msg("low", "SessionStart"))     # parked
    m.apply(_msg("low", "Stop"))             # low: DONE, parked
    m.apply(_msg("hi", "SessionStart"))      # parked
    m.apply(_msg("hi", "UserPromptSubmit"))  # hi: WORKING, still parked (a unevictable)
    assert m.get("low").key_index is None and m.get("hi").key_index is None
    m.apply(_msg("a", "SessionEnd"))         # frees key 0
    # The freed key goes to the higher-priority parked session (hi), not low.
    assert m.get("hi").key_index == 0
    assert m.get("low").key_index is None


def test_remove_frees_key_and_promotes():
    m = SessionModel(key_count=1)
    m.apply(_msg("a", "SessionStart"))       # key 0
    m.apply(_msg("b", "SessionStart"))       # parked
    m.remove("a")                             # e.g. surface died on focus
    assert m.get("b").key_index == 0          # promoted onto the freed key


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


# -- branch labels ---------------------------------------------------------


@pytest.mark.parametrize(
    "branch,expected",
    [
        ("main", "main"),
        ("develop", "develop"),
        ("m2-headless-m3-hooks", "m2-head"),      # clipped to 7, no ellipsis
        ("feat/1234-auth-refactor", "1234-au"),   # last segment, then clip
        ("matt/spike", "spike"),                  # user prefix dropped
        ("renovate/deps", "deps"),
        ("HEAD", ""),                             # detached -> nothing
        ("", ""),
        (None, ""),
    ],
)
def test_format_branch_label(branch, expected):
    assert format_branch_label(branch) == expected


def test_format_branch_label_never_exceeds_max():
    assert len(format_branch_label("a-very-long-branch-name-indeed")) == LABEL_MAX_CHARS


def test_snapshot_label_prefers_branch_over_repo():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart", label="claudeStreamDeck",
                 branch="feat/1234-auth", cwd="/w/claudeStreamDeck"))
    # The key shows the branch tail (clipped), not the repo basename.
    assert m.snapshot_keys()[0].label == "1234-au"


def test_snapshot_label_falls_back_to_repo_when_no_branch():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart", label="api-server"))
    assert m.snapshot_keys()[0].label == "api-ser"  # repo clipped the same way


def test_branch_updates_on_later_message():
    m = SessionModel()
    m.apply(_msg("a", "SessionStart", branch="main"))
    assert m.snapshot_keys()[0].label == "main"
    m.apply(_msg("a", "UserPromptSubmit", branch="feature-x"))
    assert m.get("a").branch == "feature-x"
    assert m.snapshot_keys()[0].label == "feature"
