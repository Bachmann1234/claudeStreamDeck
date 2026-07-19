import json

from gsm.registry import Registry, Session


def _reg(tmp_path):
    return Registry(path=tmp_path / "registry.json")


def test_upsert_get_roundtrip(tmp_path):
    reg = _reg(tmp_path)
    reg.upsert(Session(tag="proj", uuid="U1", working_directory="/tmp"))
    got = reg.get("proj")
    assert got is not None
    assert got.uuid == "U1"
    assert got.working_directory == "/tmp"
    assert got.source == "spawned"


def test_upsert_preserves_created_at(tmp_path):
    reg = _reg(tmp_path)
    reg.upsert(Session(tag="proj", uuid="U1"))
    created = reg.get("proj").created_at
    reg.upsert(Session(tag="proj", uuid="U2"))  # last-writer-wins on uuid
    after = reg.get("proj")
    assert after.uuid == "U2"
    assert after.created_at == created


def test_remove(tmp_path):
    reg = _reg(tmp_path)
    reg.upsert(Session(tag="proj", uuid="U1"))
    assert reg.remove("proj") is True
    assert reg.get("proj") is None
    assert reg.remove("proj") is False


def test_touch_focused(tmp_path):
    reg = _reg(tmp_path)
    reg.upsert(Session(tag="proj", uuid="U1"))
    assert reg.get("proj").last_focused_at is None
    reg.touch_focused("proj")
    assert reg.get("proj").last_focused_at is not None


def test_survives_corrupt_file(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{ this is not json")
    reg = Registry(path=path)
    assert reg.all() == {}
    reg.upsert(Session(tag="x", uuid="U"))
    assert reg.get("x").uuid == "U"


def test_tolerates_partial_records(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"sessions": {"p": {"tag": "p", "uuid": "U"}}}))
    reg = Registry(path=path)
    got = reg.get("p")
    assert got.uuid == "U"
    assert got.created_at  # backfilled
