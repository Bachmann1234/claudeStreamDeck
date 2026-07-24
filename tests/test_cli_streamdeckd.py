"""The streamdeckd CLI: parser, renderer selection, and daemon wiring.

``main`` is exercised with a fake Daemon (recording its kwargs) and a fake
renderer, so no socket is bound and no hardware is touched — hardware probing
is forced to fail via ``StreamDeckRenderer.open_first``.
"""

import logging

import pytest

from streamdeckd import cli
from streamdeckd.renderer import VirtualDeck
from streamdeckd.streamdeck_renderer import StreamDeckRenderer

log = logging.getLogger("test")


def _args(*argv):
    return cli._build_parser().parse_args(list(argv))


@pytest.fixture
def no_hardware(monkeypatch):
    """Make hardware probing fail, as on a deck-less machine."""

    def boom(cls, **kw):
        raise RuntimeError("no deck attached")

    monkeypatch.setattr(StreamDeckRenderer, "open_first", classmethod(boom))


# -- parser & renderer selection -------------------------------------------


def test_parser_defaults():
    args = _args()
    assert args.keys == 15
    assert args.brightness == 60
    assert not args.deck and not args.virtual
    assert args.working_timeout == 60.0
    assert args.launcher_key is None and not args.no_launcher


def test_make_renderer_virtual(tmp_path):
    args = _args("--virtual", "--keys", "6", "--out-dir", str(tmp_path / "vd"), "--no-png")
    r = cli._make_renderer(args, log)
    assert isinstance(r, VirtualDeck)
    assert r.key_count == 6
    assert r.write_png is False
    assert r.out_dir == tmp_path / "vd"


def test_make_renderer_auto_falls_back_to_virtual(no_hardware, tmp_path):
    args = _args("--out-dir", str(tmp_path))
    assert isinstance(cli._make_renderer(args, log), VirtualDeck)


def test_make_renderer_deck_flag_surfaces_the_error(no_hardware, tmp_path):
    args = _args("--deck")
    with pytest.raises(RuntimeError, match="no deck"):
        cli._make_renderer(args, log)


# -- main wiring ------------------------------------------------------------


class FakeRenderer:
    def __init__(self, key_count=15):
        self.key_count = key_count
        self.on_press = None

    def render(self, keys):
        pass

    def close(self):
        pass


class FakeDaemon:
    last = None

    def __init__(self, **kw):
        self.kw = kw
        FakeDaemon.last = self

    def press(self, key):
        pass

    def serve_forever(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    renderer = FakeRenderer()
    monkeypatch.setattr(cli, "Daemon", FakeDaemon)
    monkeypatch.setattr(cli, "_make_renderer", lambda args, log: renderer)
    # These tests exercise wiring, not logging — stub logging setup so main()
    # never writes to (or rotates) the developer's real ~/.claudeStreamDeck log.
    monkeypatch.setattr(cli, "_configure_logging", lambda verbose: None)
    return renderer


def test_main_defaults_launcher_to_last_key(wired):
    assert cli.main([]) == 0
    assert FakeDaemon.last.kw["launcher_key"] == 14
    # A physical press must take the same path as {"press": N}.
    assert wired.on_press == FakeDaemon.last.press


def test_main_no_launcher(wired):
    assert cli.main(["--no-launcher"]) == 0
    assert FakeDaemon.last.kw["launcher_key"] is None


def test_main_explicit_launcher_key(wired):
    assert cli.main(["--launcher-key", "3"]) == 0
    assert FakeDaemon.last.kw["launcher_key"] == 3


def test_main_passes_launch_and_timeout_flags(wired):
    assert cli.main(
        ["--launch-command", "claude", "--launch-cwd", "/w", "--working-timeout", "0"]
    ) == 0
    kw = FakeDaemon.last.kw
    assert kw["launch_command"] == "claude"
    assert kw["launch_cwd"] == "/w"
    assert kw["working_timeout"] == 0


def test_main_renderer_failure_is_exit_1(monkeypatch):
    monkeypatch.setattr(cli, "_configure_logging", lambda verbose: None)

    def boom(args, log):
        raise RuntimeError("could not open")

    monkeypatch.setattr(cli, "_make_renderer", boom)
    assert cli.main(["--deck"]) == 1


# -- logging: size-capped file + quiet stderr under launchd -----------------


def _reset_root_logging():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


def test_configure_logging_adds_rotating_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GSM_HOME", str(tmp_path))
    _reset_root_logging()
    try:
        cli._configure_logging(verbose=False)
        handlers = logging.getLogger().handlers
        rotating = [h for h in handlers if isinstance(h, cli.RotatingFileHandler)]
        stream = [
            h for h in handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, cli.RotatingFileHandler)
        ]
        assert rotating and rotating[0].maxBytes == cli._LOG_MAX_BYTES
        assert (tmp_path / "streamdeckd.log").exists()
        # Non-verbose: stderr stays quiet (WARNING+) so launchd's boot log
        # doesn't grow with routine INFO.
        assert stream and stream[0].level == logging.WARNING
    finally:
        _reset_root_logging()


def test_configure_logging_verbose_stream_is_debug(tmp_path, monkeypatch):
    monkeypatch.setenv("GSM_HOME", str(tmp_path))
    _reset_root_logging()
    try:
        cli._configure_logging(verbose=True)
        stream = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, cli.RotatingFileHandler)
        ]
        assert stream and stream[0].level == logging.DEBUG
    finally:
        _reset_root_logging()
