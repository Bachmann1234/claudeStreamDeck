"""The Claude Code hook reporter — one script, wired to every lifecycle event.

Claude Code runs this on each hook event with the event JSON on stdin. It maps
``hook_event_name`` to a deck state and writes a single JSON line to the
daemon's unix socket. It is **fire-and-forget**: a short timeout, all errors
swallowed, so a dead (or missing) daemon can never slow down or break Claude.

The interesting work happens once, on ``SessionStart``: resolving *which Ghostty
surface this session lives in*. Stock Ghostty 1.3.1 exposes no ``tty``/``pid``
(see ``docs/tier0-validation-findings.md``), so we can't look the surface up by
tty. Instead the hook writes a **unique title sentinel** to its own controlling
terminal via an OSC escape, then asks Ghostty for the surface whose title
contains that sentinel — resolving the session's UUID *from inside the session*.
Because the sentinel embeds the session id, it is globally unique, so even
several sessions starting at once each match only their own surface. The full
rationale and the alternatives considered are in
``docs/correlation-rationale.md``.

Install: expose as the ``claudestreamdeck-hook`` console script and point every
hook event at it (see ``docs/setup.md`` / ``hooks/settings.snippet.json``).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

from .state import RELEASE, KeyState, resolve_state

SOCKET_ENV = "STREAMDECKD_SOCKET"
TARGET_ENV = "STREAMDECKD_GHOSTTY"  # override the Ghostty app name/path
SENTINEL_PREFIX = "⟦gsm:"      # ⟦gsm:<session_id>⟧ — won't occur naturally
SENTINEL_SUFFIX = "⟧"


# -- socket path (kept in sync with gsm.registry.default_home, no gsm import) --


def default_socket_path() -> str:
    home = os.environ.get("GSM_HOME")
    base = os.path.expanduser(home) if home else os.path.join(
        os.path.expanduser("~"), ".claudeStreamDeck"
    )
    return os.path.join(base, "streamdeckd.sock")


def socket_path() -> str:
    return os.environ.get(SOCKET_ENV) or default_socket_path()


# -- correlation: resolve this session's Ghostty surface UUID -----------------


def _write_title(title: str) -> bool:
    """Set the terminal (window) title via OSC 2 on the controlling tty."""
    seq = f"\033]2;{title}\a"
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(seq)
            tty.flush()
        return True
    except OSError:
        return False


def _run_osascript(script: str, *, timeout: float = 2.0) -> str:
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def _as_applescript_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def resolve_uuid_via_sentinel(
    session_id: str,
    *,
    target: str = "Ghostty",
    write_title=_write_title,
    run_osascript=_run_osascript,
    sleep=time.sleep,
    attempts: int = 6,
    delay: float = 0.05,
) -> str | None:
    """Return this session's Ghostty surface UUID, or ``None`` if unresolvable.

    Sets a unique title sentinel, then polls Ghostty for the surface whose
    ``name`` contains it. The set-then-read pair lives entirely in this one
    process, so the window between "title set" and "title read" is tiny and the
    sentinel is unique — that is what contains the race. Retries a few times to
    absorb the terminal's title-update latency; gives up quietly (returns
    ``None``) if Ghostty isn't scriptable or the title never lands.
    """
    sentinel = f"{SENTINEL_PREFIX}{session_id}{SENTINEL_SUFFIX}"
    if not write_title(sentinel):
        return None  # no controlling tty -> can't set a title to look up
    script = (
        f'tell application "{_as_applescript_str(target)}" to get id of '
        f'(first terminal whose name contains "{_as_applescript_str(sentinel)}")'
    )
    for attempt in range(attempts):
        try:
            uuid = run_osascript(script).strip()
        except Exception:
            uuid = ""  # no match yet, Ghostty not up, or TCC not granted
        if uuid:
            return uuid
        if attempt < attempts - 1:
            sleep(delay)
    return None


# -- message assembly ---------------------------------------------------------


def _current_tty() -> str | None:
    """Best-effort controlling-tty path (diagnostics + future tty-capable Ghostty)."""
    try:
        with open("/dev/tty") as tty:
            return os.ttyname(tty.fileno())
    except OSError:
        return None


def _label_from(event: dict) -> str | None:
    title = event.get("session_title")
    if title:
        return str(title)
    cwd = event.get("cwd")
    if cwd:
        base = str(cwd).rstrip("/").rsplit("/", 1)[-1]
        if base:
            return base
    return None


def _state_value(hook_event: str | None) -> str | None:
    target = resolve_state(event=hook_event, state=None)
    if target is RELEASE:
        return "release"
    if isinstance(target, KeyState):
        return target.value
    return None


def build_line(event: dict, *, target: str = "Ghostty", resolve=True) -> str | None:
    """Turn an event payload into the JSON line to send, or ``None`` to skip.

    Pure except for the optional UUID resolution (``resolve=False`` in tests).
    """
    session_id = str(event.get("session_id") or "").strip()
    if not session_id:
        return None  # unattributable -> nothing to report

    hook_event = event.get("hook_event_name")
    cwd = event.get("cwd")
    label = _label_from(event)
    uuid: str | None = None

    if resolve and hook_event == "SessionStart":
        try:
            uuid = resolve_uuid_via_sentinel(session_id, target=target)
        finally:
            # Always clear the sentinel back to a friendly title, even if the
            # lookup failed, so the user never sees the raw sentinel linger.
            if label:
                _write_title(label)

    payload = {
        "session_id": session_id,
        "event": hook_event,
        "state": _state_value(hook_event),
        "label": label,
        "tty": _current_tty(),
        "uuid": uuid,
        "cwd": cwd,
    }
    return json.dumps({k: v for k, v in payload.items() if v is not None})


def send_line(line: str, *, path: str | None = None, timeout: float = 0.25) -> bool:
    """Write one line to the daemon socket. Never raises; returns success."""
    target = path or socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(target)
        s.sendall((line + "\n").encode("utf-8"))
        return True
    except OSError:
        return False  # daemon down / socket missing -> silently drop
    finally:
        s.close()


def main(argv: list[str] | None = None) -> int:
    """Hook entry point. Always exits 0 so it never blocks Claude Code."""
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(event, dict):
        return 0

    target = os.environ.get(TARGET_ENV, "Ghostty")
    try:
        line = build_line(event, target=target)
        if line is not None:
            send_line(line)
    except Exception:
        # A reporter must never surface an error to Claude Code.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
