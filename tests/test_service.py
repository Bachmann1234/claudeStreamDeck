"""The launchd LaunchAgent template stays a valid, loadable plist."""

import plistlib
from pathlib import Path

PLIST = (
    Path(__file__).resolve().parent.parent
    / "service"
    / "com.claudestreamdeck.streamdeckd.plist"
)


def _load():
    with open(PLIST, "rb") as f:
        return plistlib.load(f)


def test_plist_parses():
    data = _load()
    assert data["Label"] == "com.claudestreamdeck.streamdeckd"


def test_plist_runs_streamdeckd_at_load_and_keeps_alive():
    data = _load()
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] is True
    assert data["ProgramArguments"][0].endswith("streamdeckd")


def test_plist_still_has_placeholders_to_fill():
    # Guard against accidentally committing a machine-specific path.
    assert "/ABSOLUTE/PATH" in PLIST.read_text()
