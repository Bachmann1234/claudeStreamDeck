"""Manager logic tested against a fake Ghostty — no osascript, no real app."""

import pytest

from gsm.applescript import DeadSurface, Terminal, TtyUnsupported
from gsm.manager import AdoptFailed, Manager, UnknownTag
from gsm.registry import Registry


class FakeGhostty:
    def __init__(self, terminals=None, focused=None, frontmost=True, running=True, tty_supported=False):
        self.terminals = list(terminals or [])
        self._focused = focused
        self._frontmost = frontmost
        self._running = running
        self._tty_supported = tty_supported
        self.spawned = []
        self.focused_calls = []
        self._next_uuid = 100

    def is_running(self):
        return self._running

    def frontmost(self):
        return self._frontmost

    def focused_terminal_id(self):
        return self._focused

    def list_terminals(self):
        return list(self.terminals)

    def terminal_exists(self, uuid):
        return any(t.uuid == uuid for t in self.terminals)

    def spawn_window(self, *, command=None, working_directory=None, env=None, keep_open=False):
        self._next_uuid += 1
        uuid = f"U{self._next_uuid}"
        self.spawned.append({"uuid": uuid, "command": command, "cwd": working_directory, "env": env})
        self.terminals.append(Terminal(uuid=uuid, title=command or "shell", working_directory=working_directory or "/"))
        return uuid

    def focus(self, uuid):
        self.focused_calls.append(uuid)
        if not self.terminal_exists(uuid):
            raise DeadSurface(f"gone: {uuid}", code=-1728)
        self._focused = uuid

    def resolve_by_tty(self, tty):
        if not self._tty_supported:
            raise TtyUnsupported("no tty property", code=-1700)
        for t in self.terminals:
            if getattr(t, "tty", None) == tty:
                return t.uuid
        return None

    def resolve_by_working_directory(self, path):
        for t in self.terminals:
            if t.working_directory == path:
                return t.uuid
        return None

    def resolve_by_title_contains(self, needle):
        for t in self.terminals:
            if needle in t.title:
                return t.uuid
        return None


def _mgr(tmp_path, ghostty):
    return Manager(ghostty=ghostty, registry=Registry(path=tmp_path / "r.json"))


def test_spawn_persists_mapping_and_injects_cc_session(tmp_path):
    g = FakeGhostty()
    m = _mgr(tmp_path, g)
    s = m.spawn("proj", command="claude", working_directory="/tmp")
    assert s.uuid.startswith("U")
    assert m.registry.get("proj").uuid == s.uuid
    assert g.spawned[0]["env"]["CC_SESSION"] == "proj"


def test_focus_resolves_and_touches(tmp_path):
    g = FakeGhostty()
    m = _mgr(tmp_path, g)
    s = m.spawn("proj", command="claude")
    m.focus("proj")
    assert g.focused_calls == [s.uuid]
    assert m.registry.get("proj").last_focused_at is not None


def test_focus_unknown_tag(tmp_path):
    m = _mgr(tmp_path, FakeGhostty())
    with pytest.raises(UnknownTag):
        m.focus("nope")


def test_focus_dead_surface_prunes(tmp_path):
    g = FakeGhostty()
    m = _mgr(tmp_path, g)
    m.spawn("proj", command="claude")
    g.terminals.clear()  # surface died
    with pytest.raises(DeadSurface):
        m.focus("proj")
    assert m.registry.get("proj") is None  # pruned


def test_adopt_by_uuid(tmp_path):
    g = FakeGhostty(terminals=[Terminal("UX", "t", "/w")])
    m = _mgr(tmp_path, g)
    s = m.adopt("existing", uuid="UX")
    assert s.uuid == "UX"
    assert s.source == "adopted"


def test_adopt_by_uuid_missing(tmp_path):
    m = _mgr(tmp_path, FakeGhostty())
    with pytest.raises(AdoptFailed):
        m.adopt("existing", uuid="nope")


def test_adopt_by_cwd(tmp_path):
    g = FakeGhostty(terminals=[Terminal("UX", "t", "/work/proj")])
    m = _mgr(tmp_path, g)
    s = m.adopt("existing", cwd="/work/proj")
    assert s.uuid == "UX"


def test_adopt_by_title_contains(tmp_path):
    g = FakeGhostty(terminals=[Terminal("UX", "claude: repo-x", "/w")])
    m = _mgr(tmp_path, g)
    s = m.adopt("existing", title_contains="repo-x")
    assert s.uuid == "UX"


def test_adopt_by_tty_unsupported_gives_clear_error(tmp_path):
    g = FakeGhostty(tty_supported=False)
    m = _mgr(tmp_path, g)
    with pytest.raises(AdoptFailed) as e:
        m.adopt("existing", tty="/dev/ttys004")
    assert "does not expose" in str(e.value)


def test_adopt_requires_exactly_one_selector(tmp_path):
    m = _mgr(tmp_path, FakeGhostty())
    with pytest.raises(AdoptFailed):
        m.adopt("x", uuid="U", cwd="/p")
    with pytest.raises(AdoptFailed):
        m.adopt("x")


def test_status_marks_alive_focused_and_dead(tmp_path):
    from gsm.registry import Session

    g = FakeGhostty(frontmost=True)
    m = _mgr(tmp_path, g)
    # Spawn one live session; make it the focused surface.
    alive = m.spawn("alive", command="a")
    g._focused = alive.uuid
    # A second tag whose surface is gone.
    m.registry.upsert(Session(tag="dead", uuid="GONE"))

    report = m.status()
    assert report.ghostty_running is True
    assert report.app_frontmost is True
    by_tag = {s.session.tag: s for s in report.sessions}
    assert by_tag["alive"].alive and by_tag["alive"].focused
    assert not by_tag["dead"].alive and not by_tag["dead"].focused


def test_status_prune_removes_dead(tmp_path):
    from gsm.registry import Session

    g = FakeGhostty(terminals=[], focused=None)
    m = _mgr(tmp_path, g)
    m.registry.upsert(Session(tag="dead", uuid="GONE"))
    report = m.status(prune=True)
    assert report.sessions == []
    assert m.registry.get("dead") is None


def test_status_ghostty_not_running(tmp_path):
    from gsm.registry import Session

    g = FakeGhostty(running=False)
    m = _mgr(tmp_path, g)
    m.registry.upsert(Session(tag="x", uuid="U"))
    report = m.status()
    assert report.ghostty_running is False
    assert report.sessions[0].alive is False
