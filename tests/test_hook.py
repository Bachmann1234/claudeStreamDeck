"""The reporter hook: event->message mapping, UUID self-resolution, sending."""

import json
import socket
import threading

from streamdeckd import hook
from streamdeckd.protocol import parse_message


# -- correlation: resolve_uuid_via_sentinel --------------------------------


def test_resolve_returns_uuid_on_first_try():
    titles = []

    def write_title(t):
        titles.append(t)
        return True

    def run_osascript(script):
        # The sentinel that was written is embedded in the query script.
        assert "sess-123" in script
        return "U-RESOLVED"

    uuid = hook.resolve_uuid_via_sentinel(
        "sess-123",
        write_title=write_title,
        run_osascript=run_osascript,
        sleep=lambda _s: None,
    )
    assert uuid == "U-RESOLVED"
    assert titles == ["⟦gsm:sess-123⟧"]


def test_resolve_retries_until_title_lands():
    calls = {"n": 0}

    def run_osascript(script):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Ghostty got an error: Can't get terminal (-1719)")
        return "U-LATE"

    uuid = hook.resolve_uuid_via_sentinel(
        "s",
        write_title=lambda _t: True,
        run_osascript=run_osascript,
        sleep=lambda _s: None,
    )
    assert uuid == "U-LATE"
    assert calls["n"] == 3


def test_resolve_gives_up_quietly():
    def run_osascript(script):
        raise RuntimeError("not authorized to send Apple events")

    uuid = hook.resolve_uuid_via_sentinel(
        "s",
        write_title=lambda _t: True,
        run_osascript=run_osascript,
        sleep=lambda _s: None,
        attempts=3,
    )
    assert uuid is None


def test_resolve_no_tty_returns_none():
    # No controlling terminal -> can't set a title to look up -> bail.
    called = {"osascript": False}

    def run_osascript(script):
        called["osascript"] = True
        return "U"

    uuid = hook.resolve_uuid_via_sentinel(
        "s",
        write_title=lambda _t: False,  # /dev/tty open failed
        run_osascript=run_osascript,
        sleep=lambda _s: None,
    )
    assert uuid is None
    assert called["osascript"] is False


def test_resolve_uses_unique_sentinel_per_session():
    seen = {}

    def make_writer(store_key):
        def write_title(t):
            store_key.append(t)
            return True

        return write_title

    a, b = [], []
    hook.resolve_uuid_via_sentinel(
        "aaa", write_title=make_writer(a), run_osascript=lambda s: "UA",
        sleep=lambda _s: None,
    )
    hook.resolve_uuid_via_sentinel(
        "bbb", write_title=make_writer(b), run_osascript=lambda s: "UB",
        sleep=lambda _s: None,
    )
    assert a == ["⟦gsm:aaa⟧"] and b == ["⟦gsm:bbb⟧"]
    assert a[0] != b[0]  # no cross-session collision


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
