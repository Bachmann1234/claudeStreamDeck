"""The gsm CLI, end-to-end against a fake Ghostty and a temp registry."""

import json

import pytest

from gsm import cli

from fakes import FakeGhostty


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Route the CLI's Ghostty + registry at test doubles."""
    ghostty = FakeGhostty()
    monkeypatch.setenv("GSM_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "Ghostty", lambda target: ghostty)
    return ghostty


# -- spawn ------------------------------------------------------------------


def test_spawn_registers_and_prints(fake, capsys):
    assert cli.main(["spawn", "t1", "--cwd", "/w/repo"]) == 0
    out = capsys.readouterr().out
    assert "spawned t1 -> spawned-0" in out
    assert fake.spawns[0]["working_directory"] == "/w/repo"


def test_spawn_json(fake, capsys):
    assert cli.main(["--json", "spawn", "t1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tag"] == "t1" and payload["uuid"] == "spawned-0"


def test_spawn_bad_env_is_exit_2(fake, capsys):
    assert cli.main(["spawn", "t1", "--env", "NOEQUALS"]) == 2
    assert "expected K=V" in capsys.readouterr().err


def test_spawn_env_ok(fake):
    assert cli.main(["spawn", "t1", "--env", "A=1", "--env", "B=2"]) == 0


# -- focus ------------------------------------------------------------------


def test_focus_spawned_session(fake, capsys):
    cli.main(["spawn", "t1"])
    assert cli.main(["focus", "t1"]) == 0
    assert fake.focused == ["spawned-0"]


def test_focus_unknown_tag_is_exit_4(fake, capsys):
    assert cli.main(["focus", "nope"]) == 4
    assert "unknown tag" in capsys.readouterr().err


def test_focus_dead_surface_is_exit_5_and_prunes(fake, capsys):
    cli.main(["spawn", "t1"])
    fake.kill("spawned-0")
    assert cli.main(["focus", "t1"]) == 5
    # Pruned: a second focus now reports the tag as unknown.
    assert cli.main(["focus", "t1"]) == 4


# -- adopt ------------------------------------------------------------------


def test_adopt_by_uuid(fake, capsys):
    assert cli.main(["adopt", "t1", "--uuid", "U1"]) == 0
    assert "adopted t1 -> U1" in capsys.readouterr().out


def test_adopt_unknown_uuid_is_exit_6(fake, capsys):
    assert cli.main(["adopt", "t1", "--uuid", "nope"]) == 6


def test_adopt_by_cwd(fake, capsys):
    assert cli.main(["adopt", "t1", "--cwd", "/wd/U2"]) == 0
    assert "adopted t1 -> U2" in capsys.readouterr().out


def test_adopt_by_title(fake, capsys):
    assert cli.main(["adopt", "t1", "--title-contains", "title-U3"]) == 0
    assert "U3" in capsys.readouterr().out


def test_adopt_by_tty_unsupported_is_exit_6(fake, capsys):
    assert cli.main(["adopt", "t1", "--tty", "/dev/ttys004"]) == 6
    assert "does not expose" in capsys.readouterr().err


# -- status -----------------------------------------------------------------


def test_status_lists_sessions_with_marks(fake, capsys):
    cli.main(["spawn", "t1"])
    cli.main(["adopt", "t2", "--uuid", "U1"])
    fake.kill("U1")
    fake.front_uuid = "spawned-0"
    capsys.readouterr()  # drop spawn/adopt output
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "[*] t1" in out  # focused
    assert "[x] t2" in out  # dead
    assert "legend" in out


def test_status_json(fake, capsys):
    cli.main(["spawn", "t1"])
    capsys.readouterr()
    assert cli.main(["--json", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ghostty_running"] is True
    assert payload["sessions"][0]["session"]["tag"] == "t1"


def test_status_not_running(fake, capsys):
    fake.running = False
    assert cli.main(["status"]) == 0
    assert "not running" in capsys.readouterr().out
