"""The VirtualDeck: in-memory frame + inspectable JSON/PNG output."""

import json

from streamdeckd.renderer import VirtualDeck, _readable_text_color
from streamdeckd.state import KeyState, appearance_for


def _frame(states):
    return [appearance_for(s) for s in states]


def test_starts_blank():
    deck = VirtualDeck(key_count=4)
    assert all(k.state is KeyState.EMPTY for k in deck.keys)
    assert deck.render_count == 0


def test_render_updates_in_memory_frame():
    deck = VirtualDeck(key_count=3)
    deck.render(_frame([KeyState.WORKING, KeyState.DONE, KeyState.EMPTY]))
    assert [k.state for k in deck.keys] == [
        KeyState.WORKING,
        KeyState.DONE,
        KeyState.EMPTY,
    ]
    assert deck.render_count == 1


def test_render_wrong_length_rejected():
    deck = VirtualDeck(key_count=3)
    try:
        deck.render(_frame([KeyState.WORKING]))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on wrong key count")


def test_snapshot_shape():
    deck = VirtualDeck(key_count=2)
    deck.render(_frame([KeyState.ATTENTION, KeyState.EMPTY]))
    snap = deck.snapshot()
    assert snap["key_count"] == 2
    assert len(snap["keys"]) == 2
    assert snap["keys"][0]["state"] == "attention"
    assert snap["keys"][0]["pulse"] is True
    assert snap["keys"][0]["color"] == list(
        appearance_for(KeyState.ATTENTION).color
    )


def test_writes_snapshot_json_and_pngs(tmp_path):
    deck = VirtualDeck(key_count=2, out_dir=tmp_path, write_png=True)
    deck.render(
        [
            appearance_for(KeyState.WORKING, "repo-x"),
            appearance_for(KeyState.EMPTY),
        ]
    )
    snap_file = tmp_path / "snapshot.json"
    assert snap_file.exists()
    data = json.loads(snap_file.read_text())
    assert data["keys"][0]["label"] == "repo-x"
    assert (tmp_path / "key_00.png").exists()
    assert (tmp_path / "key_01.png").exists()


def test_no_png_when_disabled(tmp_path):
    deck = VirtualDeck(key_count=1, out_dir=tmp_path, write_png=False)
    deck.render([appearance_for(KeyState.DONE)])
    assert (tmp_path / "snapshot.json").exists()
    assert not (tmp_path / "key_00.png").exists()


def test_no_files_without_out_dir():
    # In-memory only (the default in tests) touches no disk.
    deck = VirtualDeck(key_count=1)
    deck.render([appearance_for(KeyState.DONE)])
    assert deck.out_dir is None


def test_close_blanks(tmp_path):
    deck = VirtualDeck(key_count=2, out_dir=tmp_path)
    deck.render(_frame([KeyState.WORKING, KeyState.DONE]))
    deck.close()
    assert all(k.state is KeyState.EMPTY for k in deck.keys)


def test_readable_text_color_contrast():
    assert _readable_text_color((0, 0, 0)) == (255, 255, 255)      # white on black
    assert _readable_text_color((235, 185, 0)) == (0, 0, 0)        # black on yellow


def test_conforms_to_renderer_protocol():
    from streamdeckd.renderer import Renderer

    assert isinstance(VirtualDeck(key_count=1), Renderer)
