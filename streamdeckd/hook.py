"""The Claude Code hook reporter — one script, wired to every lifecycle event.

Claude Code runs this on each hook event with the event JSON on stdin. It maps
``hook_event_name`` to a deck state and writes a single JSON line to the
daemon's unix socket. It is **fire-and-forget**: a short timeout, all errors
swallowed, so a dead (or missing) daemon can never slow down or break Claude.

The interesting work happens once, on ``SessionStart``: resolving *which Ghostty
surface this session lives in*. Stock Ghostty 1.3.1 exposes no ``tty``/``pid``
(see ``docs/tier0-validation-findings.md``), so we can't look the surface up by
tty. And — discovered by live testing — Claude Code runs hooks with **no
controlling terminal** (``/dev/tty`` is "Device not configured"), so the hook
can't write an OSC title sentinel either. What *does* work from a hook is
read-only ``osascript``. So on ``SessionStart`` (wired **synchronously**, so it
runs while the new window is still frontmost) the hook asks Ghostty for the
**focused surface** and cross-checks it against the session's ``cwd`` — that is
the window the user just typed ``claude`` into. The full rationale and the
alternatives considered are in ``docs/correlation-rationale.md``.

Install: expose as the ``claudestreamdeck-hook`` console script and point every
hook event at it (see ``docs/setup.md`` / ``hooks/settings.snippet.json``).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

from .state import RELEASE, KeyState, resolve_state

SOCKET_ENV = "STREAMDECKD_SOCKET"
TARGET_ENV = "STREAMDECKD_GHOSTTY"  # override the Ghostty app name/path


def _log(message: str) -> None:
    """Append one diagnostic line next to the socket. Never raises."""
    try:
        home = os.environ.get("GSM_HOME")
        base = os.path.expanduser(home) if home else os.path.join(
            os.path.expanduser("~"), ".claudeStreamDeck"
        )
        with open(os.path.join(base, "hook.log"), "a") as f:
            f.write(message + "\n")
    except OSError:
        pass


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


def _focused_id(run_osascript, target: str) -> str | None:
    """UUID of the frontmost focused surface, or None if there's no front window."""
    script = (
        f'tell application "{_as_applescript_str(target)}" to get id of '
        f"focused terminal of selected tab of front window"
    )
    try:
        return run_osascript(script).strip() or None
    except Exception:
        return None


def _ids_for_cwd(run_osascript, target: str, cwd: str) -> list[str]:
    """UUIDs of every surface whose working directory equals ``cwd``."""
    script = (
        f'tell application "{_as_applescript_str(target)}" to get id of '
        f'every terminal whose working directory is "{_as_applescript_str(cwd)}"'
    )
    try:
        out = run_osascript(script).strip()
    except Exception:
        return []
    # osascript renders an AppleScript list as "id1, id2"; UUIDs contain no comma.
    return [part.strip() for part in out.split(",") if part.strip()]


def resolve_uuid(
    cwd: str | None,
    *,
    target: str = "Ghostty",
    run_osascript=_run_osascript,
) -> str | None:
    """Return this session's Ghostty surface UUID, or ``None`` if unsure.

    Uses only read-only ``osascript`` (the one Ghostty channel that works from a
    hook — see the module docstring). The frontmost focused surface at
    ``SessionStart`` is the window the user just started ``claude`` in; we return
    it when it also matches the session ``cwd`` (or when Ghostty reports no cwd
    matches at all). Failing that, a *unique* cwd match is used. Anything
    ambiguous returns ``None`` rather than guess wrong — a missing binding just
    means "focus unavailable until re-resolved", never "focus the wrong window".
    """
    focused = _focused_id(run_osascript, target)
    cwd_ids = _ids_for_cwd(run_osascript, target, cwd) if cwd else []
    if focused and (focused in cwd_ids or not cwd_ids):
        return focused
    if len(cwd_ids) == 1:
        return cwd_ids[0]
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
        uuid = resolve_uuid(cwd, target=target)
        _log(f"SessionStart {session_id[:8]} cwd={cwd!r} -> uuid={uuid!r}")

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
