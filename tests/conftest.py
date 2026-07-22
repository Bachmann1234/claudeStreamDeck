"""Shared fixtures.

macOS caps ``AF_UNIX`` socket paths at 104 bytes, and pytest's ``tmp_path``
lives under a long ``/private/var/folders/...`` prefix that blows past it. Any
test that binds a real socket must place it under a short directory instead.
"""

import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def short_dir():
    """A short-path temp dir (under /tmp) safe for AF_UNIX sockets."""
    path = Path(tempfile.mkdtemp(prefix="sdk", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
