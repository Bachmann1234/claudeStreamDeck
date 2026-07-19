"""The socket wire protocol: parsing is the daemon's trust boundary."""

import json

import pytest

from streamdeckd.protocol import Message, ProtocolError, parse_message


def test_parse_full_message():
    line = json.dumps(
        {
            "session_id": "abc",
            "event": "SessionStart",
            "state": "starting",
            "label": "repo-x",
            "tty": "/dev/ttys004",
            "uuid": "U-1",
            "cwd": "/w/repo-x",
        }
    )
    msg = parse_message(line)
    assert msg.session_id == "abc"
    assert msg.event == "SessionStart"
    assert msg.uuid == "U-1"
    assert msg.cwd == "/w/repo-x"


def test_parse_minimal_message():
    msg = parse_message('{"session_id": "abc"}')
    assert msg.session_id == "abc"
    assert msg.event is None and msg.uuid is None


def test_missing_session_id_rejected():
    with pytest.raises(ProtocolError):
        parse_message('{"event": "Stop"}')


def test_blank_session_id_rejected():
    with pytest.raises(ProtocolError):
        parse_message('{"session_id": "   "}')


def test_bad_json_rejected():
    with pytest.raises(ProtocolError):
        parse_message("{not json")


def test_non_object_rejected():
    with pytest.raises(ProtocolError):
        parse_message('["a", "b"]')


def test_empty_line_rejected():
    with pytest.raises(ProtocolError):
        parse_message("   ")


def test_unknown_keys_ignored():
    # Forward compatibility: extra keys must not break parsing.
    msg = parse_message('{"session_id": "abc", "future_field": 42}')
    assert msg.session_id == "abc"


def test_whitespace_and_empty_fields_become_none():
    msg = parse_message('{"session_id": "abc", "label": "  ", "uuid": ""}')
    assert msg.label is None and msg.uuid is None


def test_roundtrip_to_json():
    original = Message(session_id="abc", event="Stop", state="done")
    reparsed = parse_message(original.to_json())
    assert reparsed == original
