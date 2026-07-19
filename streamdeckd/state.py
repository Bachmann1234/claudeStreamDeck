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
SessionStart     claim a free key                    dim / labeled
UserPromptSubmit working                             blue
PreToolUse       working                             blue
Notification     needs you (question / permission)   pulsing yellow
Stop             response finished / done            green
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
    """How one key should look. The renderer's sole input per key."""

    state: KeyState
    color: tuple[int, int, int]
    label: str = ""
    pulse: bool = False

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
    KeyState.STARTING: KeyAppearance(KeyState.STARTING, (60, 60, 72)),
    KeyState.WORKING: KeyAppearance(KeyState.WORKING, (0, 90, 200)),
    KeyState.ATTENTION: KeyAppearance(KeyState.ATTENTION, (235, 185, 0), pulse=True),
    KeyState.DONE: KeyAppearance(KeyState.DONE, (0, 160, 70)),
}


def appearance_for(state: KeyState, label: str = "") -> KeyAppearance:
    """Base appearance for ``state``, stamped with ``label``."""
    base = APPEARANCE[state]
    return KeyAppearance(state=base.state, color=base.color, label=label, pulse=base.pulse)


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
    uuid: str | None = None
    tty: str | None = None
    cwd: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "key_index": self.key_index,
            "state": self.state.value,
            "label": self.label,
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


class SessionModel:
    """``session_id -> Slot`` with stable, lowest-free key allocation.

    Not thread-safe by design — the daemon owns the lock. A session keeps its
    key for its whole life (stable mapping); the key is freed only on release.
    """

    def __init__(self, key_count: int = 15):
        if key_count < 1:
            raise ValueError("key_count must be >= 1")
        self.key_count = key_count
        self._slots: dict[str, Slot] = {}

    # -- queries -----------------------------------------------------------

    def get(self, session_id: str) -> Slot | None:
        return self._slots.get(session_id)

    def slots(self) -> list[Slot]:
        return list(self._slots.values())

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
            if i not in used:
                return i
        return None

    # -- mutation ----------------------------------------------------------

    def apply(self, msg) -> ApplyResult:
        """Fold one :class:`~streamdeckd.protocol.Message` into the model.

        - Unknown session + a non-release event  -> allocate a key (STARTING or
          the mapped state).
        - Known session                          -> update state / metadata,
          keeping its key.
        - RELEASE (SessionEnd)                    -> free the key, drop the slot.
        """
        target = resolve_state(event=msg.event, state=msg.state)

        existing = self._slots.get(msg.session_id)

        if target is RELEASE:
            if existing is None:
                return ApplyResult(slot=None, action="ignored")
            del self._slots[msg.session_id]
            return ApplyResult(slot=existing, action="released")

        if existing is None:
            # First time we've heard of this session -> claim a key.
            key_index = self._next_free_key()
            state = target if isinstance(target, KeyState) else KeyState.STARTING
            slot = Slot(
                session_id=msg.session_id,
                key_index=key_index,
                state=state,
                label=msg.label or _default_label(msg),
                uuid=msg.uuid,
                tty=msg.tty,
                cwd=msg.cwd,
            )
            self._slots[msg.session_id] = slot
            return ApplyResult(
                slot=slot,
                action="allocated" if key_index is not None else "overflow",
            )

        # Known session: update in place, keep the key.
        if isinstance(target, KeyState):
            existing.state = target
        if msg.label:
            existing.label = msg.label
        elif not existing.label:
            existing.label = _default_label(msg)
        if msg.uuid:
            existing.uuid = msg.uuid
        if msg.tty:
            existing.tty = msg.tty
        if msg.cwd:
            existing.cwd = msg.cwd
        existing.updated_at = _now()
        return ApplyResult(slot=existing, action="updated")

    def remove(self, session_id: str) -> Slot | None:
        """Drop a session (e.g. its surface died on focus). Frees its key."""
        return self._slots.pop(session_id, None)

    # -- rendering ---------------------------------------------------------

    def snapshot_keys(self) -> list[KeyAppearance]:
        """Current appearance of every physical key (length ``key_count``).

        Idempotent: always rebuilt from current state, never diffed.
        """
        keys = [appearance_for(KeyState.EMPTY) for _ in range(self.key_count)]
        for slot in self._slots.values():
            if slot.key_index is not None and 0 <= slot.key_index < self.key_count:
                keys[slot.key_index] = appearance_for(slot.state, slot.label)
        return keys


def _default_label(msg) -> str:
    """A human-ish label when the hook didn't send one: basename of cwd."""
    if msg.cwd:
        base = msg.cwd.rstrip("/").rsplit("/", 1)[-1]
        if base:
            return base
    return msg.session_id[:8]
