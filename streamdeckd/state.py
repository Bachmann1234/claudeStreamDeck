"""The in-memory session model and the state -> key-appearance mapping.

This module is pure data + logic: no sockets, no AppleScript, no threads. That
keeps key allocation and the appearance mapping trivially unit-testable. The
:class:`~streamdeckd.daemon.Daemon` is what serializes access to a
:class:`SessionModel` and pushes :meth:`SessionModel.snapshot_keys` to a
renderer.

State machine (README's state -> key table):

===============  ==================================  ==================
Hook event       Meaning                             Key appearance
===============  ==================================  ==================
SessionStart     claim a free key                    cream / labeled
UserPromptSubmit working                             teal + spinner
PreToolUse       working                             teal + spinner
Notification     needs you (question / permission)   coral, blinking "?"
Stop             response finished / done            amber
SessionEnd       release the key                     blank
===============  ==================================  ==================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KeyState(str, Enum):
    """What a session's key is currently showing. ``str`` so it JSON-dumps."""

    EMPTY = "empty"        # no session on this key
    STARTING = "starting"  # session claimed a key, not yet doing anything
    WORKING = "working"    # prompt submitted / running a tool
    ATTENTION = "attention"  # Notification: needs the human
    DONE = "done"          # Stop: response finished
    LAUNCHER = "launcher"  # display-only: a reserved "new session" (+) key


# Sentinel returned by :func:`resolve_state` meaning "release this session's
# key" (SessionEnd). Distinct from ``None`` which means "leave state unchanged".
RELEASE = "__release__"


# Claude Code ``hook_event_name`` -> resulting state (or RELEASE).
EVENT_TO_STATE: dict[str, object] = {
    "SessionStart": KeyState.STARTING,
    "UserPromptSubmit": KeyState.WORKING,
    "PreToolUse": KeyState.WORKING,
    "PostToolUse": KeyState.WORKING,  # a tool finished but the turn is still live
    "Notification": KeyState.ATTENTION,
    "Stop": KeyState.DONE,
    "SubagentStop": KeyState.WORKING,  # a subagent ended; the main turn continues
    "SessionEnd": RELEASE,
}


def resolve_state(*, event: str | None, state: str | None) -> object | None:
    """Decide the target state for a message.

    Precedence: an explicit, recognized ``state`` wins (lets a caller drive the
    deck directly); otherwise map the ``event``. Returns a :class:`KeyState`,
    the :data:`RELEASE` sentinel, or ``None`` meaning "no change" (e.g. an event
    we don't map, like ``PreCompact``).
    """
    if state:
        lowered = state.lower()
        if lowered == RELEASE or lowered == "release":
            return RELEASE
        try:
            return KeyState(lowered)
        except ValueError:
            pass  # unknown explicit state -> fall through to event mapping
    if event:
        return EVENT_TO_STATE.get(event)
    return None


@dataclass(frozen=True)
class KeyAppearance:
    """How one key should look. The renderer's sole input per key.

    ``spin`` is an animation phase (0..1) the ticker stamps on a WORKING key for
    the renderer to draw a rotating spinner; ``None`` on a still key. ``pulse``
    marks the "needs you" (ATTENTION) key, which the renderer draws as a big
    ``?``; ``blink_on`` (toggled by the ticker) is whether that ``?`` is showing
    this frame — it defaults ``True`` so a still preview shows a steady ``?``.
    """

    state: KeyState
    color: tuple[int, int, int]
    label: str = ""
    pulse: bool = False
    spin: float | None = None
    blink_on: bool = True

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "color": list(self.color),
            "label": self.label,
            "pulse": self.pulse,
        }


# Base color per state. The renderer may animate ``pulse`` states; the virtual
# deck just records the flag.
APPEARANCE: dict[KeyState, KeyAppearance] = {
    KeyState.EMPTY: KeyAppearance(KeyState.EMPTY, (0, 0, 0)),
    KeyState.STARTING: KeyAppearance(KeyState.STARTING, (245, 239, 224)),  # cream #F5EFE0
    KeyState.WORKING: KeyAppearance(KeyState.WORKING, (18, 140, 140)),  # teal #128C8C
    KeyState.ATTENTION: KeyAppearance(KeyState.ATTENTION, (255, 111, 89), pulse=True),  # coral #FF6F59
    KeyState.DONE: KeyAppearance(KeyState.DONE, (217, 164, 65)),  # amber #D9A441
    KeyState.LAUNCHER: KeyAppearance(KeyState.LAUNCHER, (16, 74, 74)),  # deep teal "+"
}


def appearance_for(state: KeyState, label: str = "") -> KeyAppearance:
    """Base appearance for ``state``, stamped with ``label``."""
    base = APPEARANCE[state]
    return KeyAppearance(state=base.state, color=base.color, label=label, pulse=base.pulse)


# The rendered key label is the git branch, kept deliberately tiny: a single
# line, no wrapping, no ellipsis — just the distinguishing tail of the branch
# hard-capped at LABEL_MAX_CHARS. (Calibrated on the physical deck: 7 chars at a
# comfortable size reads at a glance without crowding the key. See
# docs/next-steps.md "labels".)
LABEL_MAX_CHARS = 7


def format_branch_label(branch: str | None, *, limit: int = LABEL_MAX_CHARS) -> str:
    """The branch as it should appear on a key: its last ``/``-segment (dropping
    a ``feat/``, ``fix/``, ``user/`` … prefix so the *distinguishing* part
    shows), hard-truncated to ``limit`` chars. No ellipsis by design — a clipped
    label is fine; a cluttered one isn't. ``"HEAD"`` (detached) yields ``""``."""
    if not branch:
        return ""
    tail = branch.rsplit("/", 1)[-1].strip()
    if not tail or tail == "HEAD":
        return ""
    return tail[:limit]


# How much a session "deserves" a key when the deck is full. A session needing
# you (ATTENTION) outranks one merely working, which outranks a finished (DONE)
# one — so an urgent session can evict a finished one for its key. Drives
# overflow admission / eviction in :class:`SessionModel`.
STATE_PRIORITY: dict[KeyState, int] = {
    KeyState.ATTENTION: 3,
    KeyState.WORKING: 2,
    KeyState.STARTING: 1,
    KeyState.DONE: 0,
    KeyState.EMPTY: -1,
    KeyState.LAUNCHER: -1,  # never a session; here only to keep lookups total
}


@dataclass
class Slot:
    """One tracked session and the key it owns.

    ``key_index`` is ``None`` only in the overflow case (more live sessions than
    keys): the session is still tracked, but it gets no key until one frees up,
    and reassignment is not automatic. See :meth:`SessionModel.apply`.
    """

    session_id: str
    key_index: int | None
    state: KeyState = KeyState.STARTING
    label: str = ""
    branch: str | None = None
    uuid: str | None = None
    tty: str | None = None
    cwd: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # Monotonic recency stamp assigned by the model on every touch. Used for
    # LRU tie-breaks in overflow handling — finer-grained than ``updated_at``,
    # whose second resolution can't order events within the same second.
    seq: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "key_index": self.key_index,
            "state": self.state.value,
            "label": self.label,
            "branch": self.branch,
            "uuid": self.uuid,
            "tty": self.tty,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ApplyResult:
    """What :meth:`SessionModel.apply` did, so the daemon can mirror it into the
    gsm registry (bind on a resolved uuid, release on SessionEnd)."""

    slot: Slot | None       # the current slot, or the removed slot on release
    action: str             # allocated | updated | released | overflow | ignored

    @property
    def released(self) -> bool:
        return self.action == "released"

    @property
    def parked(self) -> bool:
        """True when the session is tracked but holds no key (overflow)."""
        return self.slot is not None and self.slot.key_index is None


class SessionModel:
    """``session_id -> Slot`` with stable key allocation and overflow handling.

    Not thread-safe by design — the daemon owns the lock. A session keeps its
    key for its whole life; a key is freed only on release **or** when the
    session is *parked* to make room for a more-deserving one (overflow).

    Overflow policy when all keys are taken and a new/urgent session needs one:
    with ``evict_finished_when_full`` (default), the least-recently-active
    keyed session whose :data:`STATE_PRIORITY` is *below* the newcomer's is
    parked and its key handed over — so a session that finished (DONE) yields
    its key to one that's working, and any keyed session yields to one that
    now needs you (ATTENTION). When a key frees up, the highest-priority parked
    session (oldest first) is promoted back onto it.
    """

    def __init__(
        self,
        key_count: int = 15,
        *,
        evict_finished_when_full: bool = True,
        reserved: frozenset[int] | set[int] = frozenset(),
    ):
        if key_count < 1:
            raise ValueError("key_count must be >= 1")
        self.key_count = key_count
        self.evict_finished_when_full = evict_finished_when_full
        # Keys the model must never allocate to a session (e.g. a launcher key
        # the daemon paints itself). Sessions never land here, so eviction and
        # promotion — which only consider *keyed* sessions — ignore them too.
        self.reserved = frozenset(reserved)
        self._slots: dict[str, Slot] = {}
        self._seq = 0

    # -- queries -----------------------------------------------------------

    def get(self, session_id: str) -> Slot | None:
        return self._slots.get(session_id)

    def slots(self) -> list[Slot]:
        return list(self._slots.values())

    def parked(self) -> list[Slot]:
        """Tracked sessions that currently hold no key (overflow)."""
        return [s for s in self._slots.values() if s.key_index is None]

    def session_for_key(self, key_index: int) -> Slot | None:
        for slot in self._slots.values():
            if slot.key_index == key_index:
                return slot
        return None

    def _used_keys(self) -> set[int]:
        return {s.key_index for s in self._slots.values() if s.key_index is not None}

    def _next_free_key(self) -> int | None:
        used = self._used_keys()
        for i in range(self.key_count):
            if i not in used and i not in self.reserved:
                return i
        return None

    def _bump(self, slot: Slot) -> None:
        self._seq += 1
        slot.seq = self._seq
        slot.updated_at = _now()

    # -- overflow admission ------------------------------------------------

    def _admit(self, slot: Slot) -> None:
        """Give ``slot`` a key: a free one, or (if enabled) one evicted from a
        lower-priority keyed session. Leaves it parked if neither is possible."""
        if slot.key_index is not None:
            return
        free = self._next_free_key()
        if free is not None:
            slot.key_index = free
            return
        if not self.evict_finished_when_full:
            return
        victim = self._evict_target(STATE_PRIORITY[slot.state])
        if victim is not None:
            slot.key_index = victim.key_index
            victim.key_index = None  # park the victim

    def _evict_target(self, incoming_priority: int) -> Slot | None:
        """The least-deserving keyed session below ``incoming_priority``: lowest
        priority first, oldest (smallest ``seq``) as the LRU tie-break."""
        candidates = [
            s
            for s in self._slots.values()
            if s.key_index is not None
            and STATE_PRIORITY[s.state] < incoming_priority
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda s: (STATE_PRIORITY[s.state], s.seq))

    def _promote_waiting(self) -> None:
        """Fill any free keys with the best parked sessions (highest priority,
        oldest first)."""
        waiting = self.parked()
        if not waiting:
            return
        waiting.sort(key=lambda s: (-STATE_PRIORITY[s.state], s.seq))
        for slot in waiting:
            free = self._next_free_key()
            if free is None:
                break
            slot.key_index = free

    # -- mutation ----------------------------------------------------------

    def apply(self, msg) -> ApplyResult:
        """Fold one :class:`~streamdeckd.protocol.Message` into the model.

        - Unknown session + a non-release event  -> track it and try to admit a
          key (allocating, or evicting a finished session when full).
        - Known session                          -> update state / metadata,
          keeping its key; if it grew more urgent while parked, try to admit it.
        - RELEASE (SessionEnd)                    -> drop the slot, then promote
          the best parked session onto the freed key.
        """
        target = resolve_state(event=msg.event, state=msg.state)

        existing = self._slots.get(msg.session_id)

        if target is RELEASE:
            if existing is None:
                return ApplyResult(slot=None, action="ignored")
            del self._slots[msg.session_id]
            if existing.key_index is not None:
                self._promote_waiting()  # freed key goes to a waiting session
            return ApplyResult(slot=existing, action="released")

        if existing is None:
            # First time we've heard of this session -> track and try to admit.
            state = target if isinstance(target, KeyState) else KeyState.STARTING
            slot = Slot(
                session_id=msg.session_id,
                key_index=None,
                state=state,
                label=msg.label or _default_label(msg),
                branch=msg.branch,
                uuid=msg.uuid,
                tty=msg.tty,
                cwd=msg.cwd,
            )
            self._slots[msg.session_id] = slot
            self._bump(slot)
            self._admit(slot)
            return ApplyResult(
                slot=slot,
                action="allocated" if slot.key_index is not None else "overflow",
            )

        # Known session: update in place.
        prev_priority = STATE_PRIORITY[existing.state]
        if isinstance(target, KeyState):
            existing.state = target
        if msg.label:
            existing.label = msg.label
        elif not existing.label:
            existing.label = _default_label(msg)
        if msg.branch:
            existing.branch = msg.branch
        if msg.uuid:
            existing.uuid = msg.uuid
        if msg.tty:
            existing.tty = msg.tty
        if msg.cwd:
            existing.cwd = msg.cwd
        self._bump(existing)
        # A parked session that just grew more urgent gets a fresh admission try.
        if existing.key_index is None and STATE_PRIORITY[existing.state] > prev_priority:
            self._admit(existing)
        action = "updated" if existing.key_index is not None else "overflow"
        return ApplyResult(slot=existing, action=action)

    def remove(self, session_id: str) -> Slot | None:
        """Drop a session (e.g. its surface died on focus). Frees + promotes."""
        slot = self._slots.pop(session_id, None)
        if slot is not None and slot.key_index is not None:
            self._promote_waiting()
        return slot

    # -- rendering ---------------------------------------------------------

    def snapshot_keys(self) -> list[KeyAppearance]:
        """Current appearance of every physical key (length ``key_count``).

        Idempotent: always rebuilt from current state, never diffed.
        """
        keys = [appearance_for(KeyState.EMPTY) for _ in range(self.key_count)]
        for slot in self._slots.values():
            if slot.key_index is not None and 0 <= slot.key_index < self.key_count:
                keys[slot.key_index] = appearance_for(slot.state, _display_label(slot))
        return keys


def _display_label(slot: Slot) -> str:
    """The 7-char string a key actually shows: the branch if we have one, else
    the repo/hook label as a fallback — both run through the same clip so no key
    ever shows more than :data:`LABEL_MAX_CHARS`."""
    branch_label = format_branch_label(slot.branch)
    if branch_label:
        return branch_label
    return format_branch_label(slot.label)  # fallback: clip repo basename the same way


def _default_label(msg) -> str:
    """A human-ish label when the hook didn't send one: basename of cwd."""
    if msg.cwd:
        base = msg.cwd.rstrip("/").rsplit("/", 1)[-1]
        if base:
            return base
    return msg.session_id[:8]
