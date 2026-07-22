import pytest

from gsm.applescript import (
    _RS,
    _US,
    DeadSurface,
    Ghostty,
    GhosttyScriptError,
    TtyUnsupported,
    _as_str,
    _classify,
    _extract_code,
    _process_name,
)


def test_as_str_escapes_quotes_and_backslashes():
    assert _as_str('a"b') == '"a\\"b"'
    assert _as_str("a\\b") == '"a\\\\b"'
    assert _as_str("plain") == '"plain"'


def test_as_str_escapes_newlines():
    # A raw newline inside an AppleScript literal is a syntax error; dirs may
    # legally contain them.
    assert _as_str("a\nb") == '"a\\nb"'
    assert _as_str("a\rb") == '"a\\rb"'


def test_process_name():
    assert _process_name("Ghostty") == "Ghostty"
    assert _process_name("/Applications/Ghostty.app") == "Ghostty"
    assert _process_name("/tmp/build/MyGhostty.app/") == "MyGhostty"


def test_extract_code():
    assert _extract_code("execution error: ... (-1728)") == -1728
    assert _extract_code("execution error: ... (-1719)") == -1719
    assert _extract_code("no code here") is None


def test_classify_dead_surface_object_specifier():
    err = _classify('Ghostty got an error: Can’t get terminal id "X". (-1728)', "focus terminal id \"X\"")
    assert isinstance(err, DeadSurface)
    assert err.code == -1728


def test_classify_dead_surface_whose_form():
    err = _classify("Can’t get terminal 1 whose id = \"X\". Invalid index. (-1719)", "focus ... whose id")
    assert isinstance(err, DeadSurface)
    assert err.code == -1719


def test_classify_tty_unsupported():
    err = _classify(
        "Can’t make tty of every terminal into type specifier. (-1700)",
        "get id of (first terminal whose tty is \"/dev/ttys004\")",
    )
    assert isinstance(err, TtyUnsupported)


def test_classify_tty_unsupported_whose_form_minus_2753():
    # The `whose tty is` form errors as an undefined variable, not -1700.
    err = _classify(
        "The variable tty is not defined. (-2753)",
        'get id of (first terminal whose tty is "/dev/ttys004")',
    )
    assert isinstance(err, TtyUnsupported)


def test_classify_unknown_property_without_tty_is_generic():
    err = _classify(
        "Can’t make foo into type specifier. (-1700)",
        "get foo of terminal 1",
    )
    assert isinstance(err, GhosttyScriptError)
    assert not isinstance(err, TtyUnsupported)


def test_classify_no_longer_available_message():
    err = _classify("Terminal surface is no longer available.", "focus terminal id \"X\"")
    assert isinstance(err, DeadSurface)


# -- script assembly & parsing (a scripted _run, no osascript) --------------


class ScriptedGhostty(Ghostty):
    """Records every script `_run` would execute and returns canned output."""

    def __init__(self, output: str = "", error: Exception | None = None, **kw):
        super().__init__(**kw)
        self.scripts: list[str] = []
        self.output = output
        self.error = error

    def _run(self, script: str) -> str:
        self.scripts.append(script)
        if self.error is not None:
            raise self.error
        return self.output


def test_spawn_window_assembles_configuration():
    g = ScriptedGhostty(output="ABCD-1234")
    uuid = g.spawn_window(
        command="claude",
        working_directory="/w/repo",
        env={"CC_SESSION": "t1"},
        keep_open=True,
    )
    assert uuid == "ABCD-1234"
    script = g.scripts[-1]
    assert 'command:"claude"' in script
    assert 'initial working directory:"/w/repo"' in script
    assert "wait after command:true" in script
    assert 'environment variables:{"CC_SESSION=t1"}' in script
    assert "return id of terminal 1" in script


def test_spawn_window_plain_shell_has_no_configuration():
    g = ScriptedGhostty(output="U")
    g.spawn_window()
    assert "with configuration" not in g.scripts[-1]


def test_list_terminals_parses_punctuation_safely():
    title = 'repo — "quoted", with, commas'
    out = (
        f"u1{_US}{title}{_US}/wd/one{_RS}"
        f"u2{_US}t2{_US}/wd/two{_RS}"
    )
    g = ScriptedGhostty(output=out)
    terms = g.list_terminals()
    assert [t.uuid for t in terms] == ["u1", "u2"]
    assert terms[0].title == title
    assert terms[1].working_directory == "/wd/two"


def test_list_terminals_tolerates_short_records():
    g = ScriptedGhostty(output=f"u1{_RS}{_RS}")
    terms = g.list_terminals()
    assert len(terms) == 1
    assert terms[0].uuid == "u1" and terms[0].title == ""


def test_terminal_exists():
    assert ScriptedGhostty(output="true").terminal_exists("u1") is True
    assert ScriptedGhostty(output="false").terminal_exists("u1") is False


def test_focus_reclassifies_no_longer_available():
    g = ScriptedGhostty(error=GhosttyScriptError("surface is No Longer Available"))
    with pytest.raises(DeadSurface):
        g.focus("u1")


def test_resolvers_return_none_when_nothing_matches():
    g = ScriptedGhostty(error=DeadSurface("no match", code=-1719))
    assert g.resolve_by_working_directory("/nope") is None
    assert g.resolve_by_title_contains("nope") is None


def test_resolve_by_tty_propagates_unsupported():
    g = ScriptedGhostty(error=TtyUnsupported("no tty on 1.3.1"))
    with pytest.raises(TtyUnsupported):
        g.resolve_by_tty("/dev/ttys004")


def test_has_open_window_guards_on_is_running():
    # is_running() sees "" -> False, so no `count of terminals` query is sent
    # (that one would launch Ghostty).
    g = ScriptedGhostty(output="")
    assert g.has_open_window() is False
    assert len(g.scripts) == 1
    assert "System Events" in g.scripts[0]


def test_is_running_uses_process_name_derived_from_target():
    g = ScriptedGhostty(output="true", target="/tmp/build/MyGhostty.app")
    assert g.is_running() is True
    assert 'contains "MyGhostty"' in g.scripts[-1]


def test_focused_terminal_id_none_when_no_front_window():
    g = ScriptedGhostty(error=GhosttyScriptError("no front window"))
    assert g.focused_terminal_id() is None


# -- the real _run against a fake osascript binary --------------------------


def _fake_osascript(tmp_path, body: str) -> str:
    path = tmp_path / "osascript"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def test_run_returns_stdout(tmp_path):
    g = Ghostty(osascript=_fake_osascript(tmp_path, "echo hello\n"))
    assert g.version() == "hello"


def test_run_classifies_stderr(tmp_path):
    g = Ghostty(
        osascript=_fake_osascript(
            tmp_path,
            "echo 'execution error: Ghostty got an error: no terminal. (-1728)' >&2\n"
            "exit 1\n",
        )
    )
    with pytest.raises(DeadSurface):
        g.focus("gone")


def test_run_missing_osascript_binary():
    g = Ghostty(osascript="definitely-not-a-real-binary-a8f3")
    with pytest.raises(GhosttyScriptError, match="not found"):
        g.version()
