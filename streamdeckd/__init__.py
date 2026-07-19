"""streamdeckd — the headless Claude Code session daemon.

A long-running process that listens on a unix socket for JSON-line state
reports from Claude Code hooks (see :mod:`streamdeckd.hook`), maintains a
stable ``session_id -> key`` model (:mod:`streamdeckd.state`), and paints keys
through a :class:`~streamdeckd.renderer.Renderer`. On a key press it focuses
the session's exact Ghostty surface by reusing :class:`gsm.Manager`.

The USB/HID half (M1) is deliberately behind the :class:`Renderer` interface:
this package ships a fully inspectable :class:`~streamdeckd.renderer.VirtualDeck`
so the whole daemon is testable and runnable headless, with no physical deck.
"""

from .protocol import Message, ProtocolError, parse_message
from .state import (
    APPEARANCE,
    EVENT_TO_STATE,
    KeyAppearance,
    KeyState,
    SessionModel,
    Slot,
    resolve_state,
)

__all__ = [
    "Message",
    "ProtocolError",
    "parse_message",
    "KeyState",
    "KeyAppearance",
    "Slot",
    "SessionModel",
    "APPEARANCE",
    "EVENT_TO_STATE",
    "resolve_state",
]
