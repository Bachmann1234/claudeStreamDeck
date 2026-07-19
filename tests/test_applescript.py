from gsm.applescript import (
    DeadSurface,
    GhosttyScriptError,
    TtyUnsupported,
    _as_str,
    _classify,
    _extract_code,
)


def test_as_str_escapes_quotes_and_backslashes():
    assert _as_str('a"b') == '"a\\"b"'
    assert _as_str("a\\b") == '"a\\\\b"'
    assert _as_str("plain") == '"plain"'


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
