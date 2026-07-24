"""The reporter hook: event->message mapping, UUID self-resolution, sending."""

import json
import socket
import threading

from streamdeckd import hook
from streamdeckd.protocol import parse_message


# -- correlation: resolve_uuid (focused + cwd, read-only osascript) ---------


def _fake_osascript(*, focused=None, cwd_ids=None, focused_error=False):
    """A stand-in osascript that answers the two queries resolve_uuid makes.

    ``focused`` -> the focused-front-surface id (or None / raise). ``cwd_ids``
    -> the surfaces the ``whose working directory is`` query returns.
    """
    cwd_ids = cwd_ids or []

    def run(script):
        if "focused terminal" in script:
            if focused_error:
                raise RuntimeError("no front window (-1728)")
            return focused or ""
        if "every terminal whose working directory" in script:
            return ", ".join(cwd_ids)
        return ""

    return run


def test_resolve_returns_focused_when_it_matches_cwd():
    # The common case: the new session's window is focused and matches cwd.
    run = _fake_osascript(focused="U-NEW", cwd_ids=["U-NEW", "U-OTHER"])
    assert hook.resolve_uuid("/w/repo", run_osascript=run) == "U-NEW"


def test_resolve_trusts_focused_when_no_cwd_matches():
    # Ghostty reports the cwd differently than Claude -> still trust focused.
    run = _fake_osascript(focused="U-NEW", cwd_ids=[])
    assert hook.resolve_uuid("/w/repo", run_osascript=run) == "U-NEW"


def test_resolve_falls_back_to_unique_cwd_match():
    # A different window is focused, but exactly one surface matches our cwd.
    run = _fake_osascript(focused="U-SOMETHING-ELSE", cwd_ids=["U-ONLY"])
    assert hook.resolve_uuid("/w/repo", run_osascript=run) == "U-ONLY"


def test_resolve_ambiguous_cwd_without_focus_match_returns_none():
    # Two surfaces share the cwd and neither is the focused one -> don't guess.
    run = _fake_osascript(focused="U-ELSEWHERE", cwd_ids=["U-A", "U-B"])
    assert hook.resolve_uuid("/w/repo", run_osascript=run) is None


def test_resolve_no_front_window_uses_unique_cwd():
    run = _fake_osascript(focused_error=True, cwd_ids=["U-ONLY"])
    assert hook.resolve_uuid("/w/repo", run_osascript=run) == "U-ONLY"


def test_resolve_no_front_window_ambiguous_cwd_returns_none():
    run = _fake_osascript(focused_error=True, cwd_ids=["U-A", "U-B"])
    assert hook.resolve_uuid("/w/repo", run_osascript=run) is None


def test_resolve_nothing_resolvable_returns_none():
    run = _fake_osascript(focused_error=True, cwd_ids=[])
    assert hook.resolve_uuid("/w/repo", run_osascript=run) is None


def test_resolve_disambiguates_same_cwd_by_focus():
    # The exact live scenario: two Claude sessions in the same repo; the one the
    # user just started is focused -> we pick it, not its sibling.
    run = _fake_osascript(focused="U-JUST-STARTED", cwd_ids=["U-SIBLING", "U-JUST-STARTED"])
    assert hook.resolve_uuid("/code/app", run_osascript=run) == "U-JUST-STARTED"


# -- build_line ------------------------------------------------------------


def test_build_line_sessionstart_maps_state():
    event = {
        "session_id": "abc",
        "hook_event_name": "SessionStart",
        "cwd": "/Users/me/code/repo-x",
    }
    line = hook.build_line(event, resolve=False)
    msg = parse_message(line)
    assert msg.session_id == "abc"
    assert msg.event == "SessionStart"
    assert msg.state == "starting"
    assert msg.label == "repo-x"


def test_build_line_stop_is_done():
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "Stop"}, resolve=False
    )
    assert parse_message(line).state == "done"


def test_build_line_sessionend_is_release():
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "SessionEnd"}, resolve=False
    )
    assert parse_message(line).state == "release"


def test_build_line_prefers_session_title_label():
    line = hook.build_line(
        {
            "session_id": "abc",
            "hook_event_name": "SessionStart",
            "cwd": "/w/repo",
            "session_title": "My Session",
        },
        resolve=False,
    )
    assert parse_message(line).label == "My Session"


def test_build_line_no_session_id_returns_none():
    assert hook.build_line({"hook_event_name": "Stop"}, resolve=False) is None


# -- Notification: distinguish a real prompt from an idle one --------------


def _notif(ntype=None):
    e = {"session_id": "abc", "hook_event_name": "Notification"}
    if ntype is not None:
        e["notification_type"] = ntype
    return parse_message(hook.build_line(e, resolve=False))


def test_notification_permission_is_attention():
    assert _notif("permission_prompt").state == "attention"


def test_notification_agent_needs_input_is_attention():
    assert _notif("agent_needs_input").state == "attention"


def test_notification_idle_prompt_is_done_not_attention():
    # The bug: an idle "waiting for your prompt" notification was flashing "?".
    assert _notif("idle_prompt").state == "done"


def test_notification_missing_type_defaults_to_attention():
    assert _notif(None).state == "attention"  # unknown/old CC -> stay conservative


def test_notification_transient_type_leaves_state_unchanged():
    # auth_success etc.: no state, and the event is dropped so the daemon's
    # event->ATTENTION fallback can't kick in.
    msg = _notif("auth_success")
    assert msg.state is None and msg.event is None


# -- branch resolution -----------------------------------------------------


class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_git_branch_returns_current_branch(monkeypatch):
    monkeypatch.setattr(hook.subprocess, "run",
                        lambda *a, **k: _FakeProc("feat/1234-auth\n"))
    assert hook._git_branch("/w/repo") == "feat/1234-auth"


def test_git_branch_none_on_detached_head(monkeypatch):
    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: _FakeProc("HEAD\n"))
    assert hook._git_branch("/w/repo") is None


def test_git_branch_none_outside_repo(monkeypatch):
    monkeypatch.setattr(hook.subprocess, "run",
                        lambda *a, **k: _FakeProc("", returncode=128))
    assert hook._git_branch("/w/notrepo") is None


def test_git_branch_none_without_cwd():
    assert hook._git_branch(None) is None  # no subprocess call at all


def test_git_branch_swallows_git_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git not found")
    monkeypatch.setattr(hook.subprocess, "run", boom)
    assert hook._git_branch("/w/repo") is None


def test_build_line_includes_branch_on_sessionstart(monkeypatch):
    monkeypatch.setattr(hook, "resolve_uuid", lambda *a, **k: "U-1")
    monkeypatch.setattr(hook, "_git_branch", lambda *a, **k: "feat/1234-auth")
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "SessionStart", "cwd": "/w/repo"},
        resolve=True,
    )
    msg = parse_message(line)
    assert msg.branch == "feat/1234-auth"
    assert msg.uuid == "U-1"


def test_build_line_reresolves_uuid_on_userpromptsubmit(monkeypatch):
    # Heal-on-activity: UserPromptSubmit re-resolves the surface UUID (the
    # session is focused when you submit), but does NOT re-run the git lookup.
    monkeypatch.setattr(hook, "resolve_uuid", lambda *a, **k: "U-RE")
    monkeypatch.setattr(hook, "_git_branch", lambda *a, **k: "should-not-run")
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "UserPromptSubmit", "cwd": "/w"},
        resolve=True,
    )
    msg = parse_message(line)
    assert msg.uuid == "U-RE"
    assert msg.branch is None  # branch is resolved only on SessionStart


def test_userpromptsubmit_skips_resolve_once_bound(tmp_path, monkeypatch):
    # The per-prompt cost fix: once the daemon has bound this session (its uuid
    # is in registry.json), UserPromptSubmit must NOT re-run osascript.
    monkeypatch.setenv("GSM_HOME", str(tmp_path))
    (tmp_path / "registry.json").write_text(
        json.dumps({"sessions": {"abc": {"uuid": "U-BOUND"}}})
    )

    def fail(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("resolve_uuid ran despite an existing binding")

    monkeypatch.setattr(hook, "resolve_uuid", fail)
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "UserPromptSubmit", "cwd": "/w"},
        resolve=True,
    )
    assert parse_message(line).uuid is None  # no uuid sent; daemon keeps its own


def test_userpromptsubmit_still_resolves_when_unbound(tmp_path, monkeypatch):
    # A session the daemon rejected (mis-resolved sibling surface) has no uuid in
    # the registry, so UserPromptSubmit keeps re-resolving until it heals.
    monkeypatch.setenv("GSM_HOME", str(tmp_path))  # no registry.json at all
    monkeypatch.setattr(hook, "resolve_uuid", lambda *a, **k: "U-HEALED")
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "UserPromptSubmit", "cwd": "/w"},
        resolve=True,
    )
    assert parse_message(line).uuid == "U-HEALED"


def test_sessionstart_always_resolves_even_when_bound(tmp_path, monkeypatch):
    # SessionStart is a fresh session (a reused id would be a new surface), so it
    # always resolves regardless of a stale registry entry.
    monkeypatch.setenv("GSM_HOME", str(tmp_path))
    (tmp_path / "registry.json").write_text(
        json.dumps({"sessions": {"abc": {"uuid": "U-OLD"}}})
    )
    monkeypatch.setattr(hook, "resolve_uuid", lambda *a, **k: "U-FRESH")
    monkeypatch.setattr(hook, "_git_branch", lambda *a, **k: None)
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "SessionStart", "cwd": "/w"},
        resolve=True,
    )
    assert parse_message(line).uuid == "U-FRESH"


def test_build_line_omits_branch_off_sessionstart(monkeypatch):
    # branch is resolved only on SessionStart; other events carry no branch.
    def fail(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("_git_branch called off SessionStart")
    monkeypatch.setattr(hook, "_git_branch", fail)
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "Stop", "cwd": "/w/repo"},
        resolve=True,
    )
    assert parse_message(line).branch is None


def test_build_line_unknown_event_has_no_state():
    line = hook.build_line(
        {"session_id": "abc", "hook_event_name": "PreCompact"}, resolve=False
    )
    msg = parse_message(line)
    assert msg.state is None and msg.event == "PreCompact"


# -- send_line -------------------------------------------------------------


def test_send_line_delivers_to_socket(short_dir):
    sock_path = short_dir / "recv.sock"
    received = []
    ready = threading.Event()

    def server():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        ready.set()
        conn, _ = srv.accept()
        with conn:
            received.append(conn.recv(4096).decode())
        srv.close()

    t = threading.Thread(target=server, daemon=True)
    t.start()
    ready.wait(2)

    ok = hook.send_line('{"session_id": "abc"}', path=str(sock_path))
    t.join(timeout=2)
    assert ok is True
    assert received and json.loads(received[0].strip())["session_id"] == "abc"


def test_send_line_missing_daemon_is_silent(tmp_path):
    # No server listening -> returns False, never raises (Claude stays unblocked).
    assert hook.send_line('{"session_id": "abc"}', path=str(tmp_path / "nope.sock")) is False


def test_socket_path_env_override(monkeypatch):
    monkeypatch.setenv(hook.SOCKET_ENV, "/tmp/custom.sock")
    assert hook.socket_path() == "/tmp/custom.sock"


def test_default_socket_path_honors_gsm_home(monkeypatch):
    monkeypatch.delenv(hook.SOCKET_ENV, raising=False)
    monkeypatch.setenv("GSM_HOME", "/tmp/gsmhome")
    assert hook.default_socket_path() == "/tmp/gsmhome/streamdeckd.sock"


def test_main_swallows_everything(monkeypatch):
    # A malformed stdin payload must still exit 0.
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert hook.main() == 0


# -- hook.log rotation ------------------------------------------------------


def test_log_rotates_past_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("GSM_HOME", str(tmp_path))
    log_path = tmp_path / "hook.log"
    log_path.write_text("x" * (hook._LOG_MAX_BYTES + 1))
    hook._log("fresh line")
    assert (tmp_path / "hook.log.1").exists()
    assert log_path.read_text() == "fresh line\n"


def test_log_appends_below_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("GSM_HOME", str(tmp_path))
    hook._log("one")
    hook._log("two")
    assert (tmp_path / "hook.log").read_text() == "one\ntwo\n"
    assert not (tmp_path / "hook.log.1").exists()
