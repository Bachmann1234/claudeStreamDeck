"""The daemon: socket ingest -> model/registry -> repaint, and press -> focus."""

import json
import socket
import threading
import time

import pytest

from gsm.manager import Manager
from gsm.registry import Registry
from streamdeckd.daemon import Daemon
from streamdeckd.state import KeyState

from fakes import FakeGhostty, RecordingRenderer


def _daemon(tmp_path, key_count=15, ghostty=None, socket_path=None):
    ghostty = ghostty or FakeGhostty()
    manager = Manager(ghostty=ghostty, registry=Registry(path=tmp_path / "reg.json"))
    renderer = RecordingRenderer(key_count=key_count)
    d = Daemon(
        manager=manager,
        renderer=renderer,
        socket_path=socket_path or (tmp_path / "d.sock"),
    )
    return d, manager, renderer, ghostty


def _line(**kw):
    return json.dumps(kw)


# -- handle_line -----------------------------------------------------------


def test_sessionstart_allocates_and_repaints(tmp_path):
    d, _, renderer, _ = _daemon(tmp_path)
    r = d.handle_line(_line(session_id="a", event="SessionStart", cwd="/w/repo-a"))
    assert r.action == "allocated"
    assert renderer.last[0].state is KeyState.STARTING
    assert renderer.last[0].label == "repo-a"


def test_state_transitions_repaint(tmp_path):
    d, _, renderer, _ = _daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart"))
    d.handle_line(_line(session_id="a", event="UserPromptSubmit"))
    assert renderer.last[0].state is KeyState.WORKING
    d.handle_line(_line(session_id="a", event="Notification"))
    assert renderer.last[0].state is KeyState.ATTENTION
    d.handle_line(_line(session_id="a", event="Stop"))
    assert renderer.last[0].state is KeyState.DONE


def test_uuid_mirrored_into_registry(tmp_path):
    d, manager, _, _ = _daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1", cwd="/w/a"))
    bound = manager.registry.get("a")
    assert bound is not None and bound.uuid == "U1"


def test_sessionend_releases_key_and_registry(tmp_path):
    d, manager, renderer, _ = _daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1"))
    d.handle_line(_line(session_id="a", event="SessionEnd"))
    assert renderer.last[0].state is KeyState.EMPTY
    assert manager.registry.get("a") is None


def test_bad_lines_are_swallowed(tmp_path):
    d, _, renderer, _ = _daemon(tmp_path)
    assert d.handle_line("{not json") is None
    assert d.handle_line(_line(event="Stop")) is None  # no session_id
    assert d.handle_line("   ") is None
    # A dead daemon-breaker never raised; model untouched.
    assert renderer.frames == []


# -- press -> focus --------------------------------------------------------


def test_press_focuses_bound_surface(tmp_path):
    d, manager, _, ghostty = _daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1"))
    slot = d.press(0)
    assert slot.session_id == "a"
    assert ghostty.focused == ["U1"]


def test_press_blank_key_is_noop(tmp_path):
    d, _, _, ghostty = _daemon(tmp_path)
    assert d.press(3) is None
    assert ghostty.focused == []


def test_press_without_uuid_cannot_focus(tmp_path):
    d, _, _, ghostty = _daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart"))  # no uuid resolved
    slot = d.press(0)
    assert slot is not None and slot.uuid is None
    assert ghostty.focused == []  # nothing to focus


def test_press_dead_surface_prunes_key(tmp_path):
    d, manager, renderer, ghostty = _daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1"))
    ghostty.kill("U1")  # surface closed under us
    assert d.press(0) is None
    assert renderer.last[0].state is KeyState.EMPTY  # key released
    assert manager.registry.get("a") is None  # pruned
    assert d.model.get("a") is None


def test_press_command_over_socket_path(tmp_path):
    d, _, _, ghostty = _daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1"))
    # A {"press": N} control line focuses key N (the HID callback's socket form).
    d.handle_line(json.dumps({"press": 0}))
    assert ghostty.focused == ["U1"]


def test_bad_press_command_swallowed(tmp_path):
    d, _, _, ghostty = _daemon(tmp_path)
    d.handle_line(json.dumps({"press": "notint"}))
    assert ghostty.focused == []


# -- real socket integration ----------------------------------------------


def test_socket_end_to_end(tmp_path, short_dir):
    sock_path = short_dir / "live.sock"
    d, manager, renderer, ghostty = _daemon(tmp_path, socket_path=sock_path)
    t = threading.Thread(
        target=lambda: d.serve_forever(install_signal_handlers=False),
        daemon=True,
    )
    t.start()
    try:
        for _ in range(100):
            if sock_path.exists():
                break
            time.sleep(0.01)
        assert sock_path.exists()

        def send(obj):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(sock_path))
            s.sendall((json.dumps(obj) + "\n").encode())
            s.close()

        send({"session_id": "a", "event": "SessionStart", "uuid": "U1", "cwd": "/w/a"})
        send({"session_id": "a", "event": "UserPromptSubmit"})
        # Wait for the daemon thread to apply both lines.
        for _ in range(100):
            if renderer.frames and renderer.last[0].state is KeyState.WORKING:
                break
            time.sleep(0.01)
        assert renderer.last[0].state is KeyState.WORKING
        assert manager.registry.get("a").uuid == "U1"

        # A press command over the socket focuses the surface.
        send({"press": 0})
        for _ in range(100):
            if ghostty.focused:
                break
            time.sleep(0.01)
        assert ghostty.focused == ["U1"]
    finally:
        d.shutdown()
        t.join(timeout=2)
    assert not sock_path.exists()  # cleaned up on shutdown


def test_refuses_second_daemon_on_live_socket(tmp_path, short_dir):
    sock_path = short_dir / "busy.sock"
    d, _, _, _ = _daemon(tmp_path, socket_path=sock_path)
    t = threading.Thread(
        target=lambda: d.serve_forever(install_signal_handlers=False),
        daemon=True,
    )
    t.start()
    try:
        for _ in range(100):
            if sock_path.exists():
                break
            time.sleep(0.01)
        d2, _, _, _ = _daemon(tmp_path, socket_path=sock_path)
        with pytest.raises(RuntimeError):
            d2.serve_forever(install_signal_handlers=False)
    finally:
        d.shutdown()
        t.join(timeout=2)


def test_stale_socket_file_is_reclaimed(tmp_path, short_dir):
    sock_path = short_dir / "stale.sock"
    sock_path.write_text("")  # a leftover file, nothing listening
    d, _, _, _ = _daemon(tmp_path, socket_path=sock_path)
    t = threading.Thread(
        target=lambda: d.serve_forever(install_signal_handlers=False),
        daemon=True,
    )
    t.start()
    try:
        # Poll until it's actually a bound, connectable socket (not the leftover
        # regular file the daemon has to reclaim first).
        connected = False
        for _ in range(200):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(str(sock_path))
                connected = True
                break
            except OSError:
                time.sleep(0.01)
            finally:
                s.close()
        assert connected  # bound successfully despite the pre-existing file
    finally:
        d.shutdown()
        t.join(timeout=2)


def test_model_renderer_key_mismatch_rejected(tmp_path):
    from streamdeckd.state import SessionModel

    manager = Manager(ghostty=FakeGhostty(), registry=Registry(path=tmp_path / "r.json"))
    with pytest.raises(ValueError):
        Daemon(
            manager=manager,
            renderer=RecordingRenderer(key_count=15),
            model=SessionModel(key_count=6),
            socket_path=tmp_path / "x.sock",
        )


# -- animation ticker ------------------------------------------------------


def _animated_daemon(tmp_path, *, animate=True):
    manager = Manager(ghostty=FakeGhostty(), registry=Registry(path=tmp_path / "r.json"))
    renderer = RecordingRenderer(animated=True)
    d = Daemon(
        manager=manager,
        renderer=renderer,
        socket_path=tmp_path / "d.sock",
        animate=animate,
    )
    return d, renderer


def test_tick_animation_noop_when_all_keys_calm(tmp_path):
    d, renderer = _animated_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="Stop", cwd="/w/r"))  # DONE: no motion
    before = len(renderer.frames)
    assert d._tick_animation(elapsed=0.0) is False
    assert len(renderer.frames) == before  # nothing rendered


def test_tick_animation_blinks_attention_key(tmp_path):
    from streamdeckd.animation import BLINK_PERIOD_S

    d, renderer = _animated_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="Notification", cwd="/w/r"))  # ATTENTION
    assert d._tick_animation(elapsed=BLINK_PERIOD_S * 0.75) is True  # off half
    assert renderer.last[0].pulse is True
    assert renderer.last[0].blink_on is False  # the "?" is hidden this frame


def test_tick_animation_spins_working_key(tmp_path):
    from streamdeckd.animation import SPIN_PERIOD_S

    d, renderer = _animated_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="UserPromptSubmit", cwd="/w/r"))  # WORKING
    assert d._tick_animation(elapsed=SPIN_PERIOD_S / 4) is True
    assert renderer.last[0].spin is not None  # rotation phase stamped for the renderer


def test_animate_flag_off_disables_ticker(tmp_path):
    d, _ = _animated_daemon(tmp_path, animate=False)
    assert d._animate is False


def test_virtual_renderer_never_animates(tmp_path):
    # A renderer that doesn't opt in (animated=False) is never ticked.
    manager = Manager(ghostty=FakeGhostty(), registry=Registry(path=tmp_path / "r.json"))
    d = Daemon(
        manager=manager,
        renderer=RecordingRenderer(animated=False),
        socket_path=tmp_path / "d.sock",
        animate=True,
    )
    assert d._animate is False


# -- launcher key ----------------------------------------------------------


def _launcher_daemon(tmp_path, *, launcher_key=14, launch_command=None):
    ghostty = FakeGhostty()
    manager = Manager(ghostty=ghostty, registry=Registry(path=tmp_path / "r.json"))
    renderer = RecordingRenderer(key_count=15)
    d = Daemon(
        manager=manager,
        renderer=renderer,
        socket_path=tmp_path / "d.sock",
        launcher_key=launcher_key,
        launch_command=launch_command,
        launch_cwd="/w",
    )
    return d, ghostty, renderer


def test_launcher_key_is_painted(tmp_path):
    from streamdeckd.state import KeyState

    d, _, renderer = _launcher_daemon(tmp_path)
    d._repaint()
    assert renderer.last[14].state is KeyState.LAUNCHER


def test_launcher_opens_a_tab_when_a_window_is_open(tmp_path):
    d, ghostty, _ = _launcher_daemon(tmp_path)  # plain shell
    ghostty.windows_open = True
    assert d.press(14) is None
    assert ghostty.tabs == 1 and ghostty.spawns == []  # tab, not a new window


def test_launcher_opens_a_window_when_none_is_open(tmp_path):
    d, ghostty, _ = _launcher_daemon(tmp_path)  # plain shell, no window open
    d.press(14)
    assert ghostty.tabs == 0
    assert ghostty.spawns == [{"command": None, "working_directory": "/w"}]


def test_launcher_falls_back_to_window_if_tab_fails(tmp_path):
    d, ghostty, _ = _launcher_daemon(tmp_path)
    ghostty.windows_open = True
    ghostty.tab_error = RuntimeError("Accessibility not granted")
    d.press(14)
    assert ghostty.spawns == [{"command": None, "working_directory": "/w"}]


def test_launcher_command_always_uses_a_window(tmp_path):
    # A fixed command can't ride a Cmd-T tab, so it always spawns a window.
    d, ghostty, _ = _launcher_daemon(tmp_path, launch_command="claude")
    ghostty.windows_open = True
    d.press(14)
    assert ghostty.tabs == 0
    assert ghostty.spawns == [{"command": "claude", "working_directory": "/w"}]


def test_launcher_key_reserved_from_sessions(tmp_path):
    d, _, _ = _launcher_daemon(tmp_path, launcher_key=0)  # reserve the first key
    d.handle_line(_line(session_id="a", event="SessionStart", cwd="/w/r"))
    assert d.model.get("a").key_index != 0  # session skipped the launcher key


def test_out_of_range_launcher_is_disabled(tmp_path):
    d, ghostty, _ = _launcher_daemon(tmp_path, launcher_key=99)
    assert d.launcher_key is None
    assert d.press(99) is None and ghostty.spawns == []  # no spawn, no crash


# -- reaper (auto-heal) ----------------------------------------------------


def _reaper_daemon(tmp_path):
    ghostty = FakeGhostty()  # U1, U2, U3 live
    manager = Manager(ghostty=ghostty, registry=Registry(path=tmp_path / "r.json"))
    renderer = RecordingRenderer()
    d = Daemon(manager=manager, renderer=renderer, socket_path=tmp_path / "d.sock",
               launcher_key=None)
    return d, ghostty, renderer


def test_reaper_blanks_key_when_surface_dies(tmp_path):
    from streamdeckd.state import KeyState

    d, ghostty, renderer = _reaper_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1", cwd="/w"))
    key = d.model.get("a").key_index
    assert key is not None
    ghostty.kill("U1")  # the surface goes away with no SessionEnd
    assert d._reap_dead_surfaces() == 1
    assert d.model.get("a") is None            # session dropped
    assert renderer.last[key].state is KeyState.EMPTY  # key blanked


def test_reaper_keeps_live_and_unresolved_sessions(tmp_path):
    d, ghostty, _ = _reaper_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1", cwd="/w"))
    d.handle_line(_line(session_id="b", event="SessionStart", cwd="/w2"))  # no uuid
    assert d._reap_dead_surfaces() == 0
    assert d.model.get("a") is not None and d.model.get("b") is not None


def test_reaper_skips_when_ghostty_not_running(tmp_path):
    # Can't confirm liveness -> reap nothing (never blank live sessions on a blip).
    d, ghostty, _ = _reaper_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="SessionStart", uuid="U1", cwd="/w"))
    ghostty.kill("U1")
    ghostty.running = False
    assert d._reap_dead_surfaces() == 0
    assert d.model.get("a") is not None


# -- watchdog: clear a spinner stuck by an interrupt -----------------------


def _watchdog_daemon(tmp_path, *, working_timeout=30.0):
    manager = Manager(ghostty=FakeGhostty(), registry=Registry(path=tmp_path / "r.json"))
    d = Daemon(manager=manager, renderer=RecordingRenderer(), socket_path=tmp_path / "d.sock",
               launcher_key=None, working_timeout=working_timeout)
    return d


def test_watchdog_downgrades_stale_working(tmp_path):
    from streamdeckd.state import KeyState

    d = _watchdog_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="UserPromptSubmit", cwd="/w"))  # WORKING
    key = d.model.get("a").key_index
    d._activity["a"] -= 40  # pretend the last activity was 40s ago (> 30s timeout)
    assert d._downgrade_stale_working() == 1
    assert d.model.get("a").state is KeyState.DONE
    assert d.renderer.last[key].state is KeyState.DONE


def test_watchdog_leaves_fresh_working_alone(tmp_path):
    from streamdeckd.state import KeyState

    d = _watchdog_daemon(tmp_path)
    d.handle_line(_line(session_id="a", event="UserPromptSubmit", cwd="/w"))
    assert d._downgrade_stale_working() == 0  # just active
    assert d.model.get("a").state is KeyState.WORKING


def test_watchdog_disabled_with_zero_timeout(tmp_path):
    from streamdeckd.state import KeyState

    d = _watchdog_daemon(tmp_path, working_timeout=0)
    d.handle_line(_line(session_id="a", event="UserPromptSubmit", cwd="/w"))
    d._activity["a"] -= 9999
    assert d._downgrade_stale_working() == 0
    assert d.model.get("a").state is KeyState.WORKING
