"""gsm — Tier 0 Ghostty Claude-session manager core.

Focus an exact Ghostty surface by stable identity, using only stock Ghostty
>= 1.3.0's AppleScript dictionary (no fork). See ``ghostty-focus-plan.md`` §3.
"""

from .applescript import (
    DeadSurface,
    Ghostty,
    GhosttyNotRunning,
    GhosttyScriptError,
    Terminal,
    TtyUnsupported,
)
from .manager import Manager, SessionStatus, StatusReport
from .registry import Registry, Session

__all__ = [
    "Ghostty",
    "Terminal",
    "GhosttyScriptError",
    "GhosttyNotRunning",
    "DeadSurface",
    "TtyUnsupported",
    "Registry",
    "Session",
    "Manager",
    "SessionStatus",
    "StatusReport",
]
