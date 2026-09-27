"""The plugin tab's backend: settings and custom_converters files, as functions and through FastAPI."""
import json

import pytest

from rustuya_local.manager_plugin import api


def test_settings_default_validate_and_save(tmp_path):
    assert api.read_settings(tmp_path) == api.DEFAULTS
    saved = api.save_settings(tmp_path, {"il": {"prefix": "il/tuya"}, "options": {"expose_unused": True}})
    assert saved["il"] == {"prefix": "il/tuya", "source": "tuya"} and saved["options"]["expose_unused"] is True
    assert api.read_settings(tmp_path) == saved
    assert json.loads((tmp_path / "settings.json").read_text()) == saved
    for bad in ({"il": {"prefix": "il/+"}}, {"il": {"prefix": ""}}, {"il": {"source": "a/b"}}, {"nope": 1},
                {"options": {"expose_unused": "yes"}}, {"options": {"unknown": True}}, [1]):
        with pytest.raises(api.Invalid):
            api.save_settings(tmp_path, bad)
    assert api.read_settings(tmp_path) == saved                       # a refused save changes nothing


def test_converter_files(tmp_path):
    d = tmp_path / "custom_converters"
    assert api.list_converters(d) == {"files": [], "warnings": []}
    r = api.save_converter(d, "10_mine.json", json.dumps({"pid1": {"device": {"label": "Mine"}}}))
    assert r["warnings"] == []
    assert api.read_converter(d, "10_mine.json")["content"].startswith("{")
    assert [f["name"] for f in api.list_converters(d)["files"]] == ["10_mine.json"]
    r = api.save_converter(d, "empty.py", "X = 1\n")                     # no CONVERTERS: saved, and reported
    assert any("defines no CONVERTERS" in w for w in r["warnings"])
    for name in ("../x.json", ".hidden.json", "a.txt", "sub/a.json"):
        with pytest.raises(api.Invalid):
            api.save_converter(d, name, "{}")
    with pytest.raises(api.Invalid):
        api.save_converter(d, "bad.json", "{not json")
    assert not (d / "bad.json").exists()
    api.delete_converter(d, "empty.py")
    with pytest.raises(FileNotFoundError):
        api.delete_converter(d, "empty.py")


def test_where_a_converter_file_comes_from(tmp_path):
    import hashlib

    d = tmp_path / "custom_converters"
    api.save_converter(d, "00_pack_a.json", "{}")
    api.save_converter(d, "00_pack_b.json", "{}")
    api.save_converter(d, "10_mine.json", "{}")
    sha = hashlib.sha256(b"{}").hexdigest()
    (d / ".tuya2ildevice_pack.json").write_text(json.dumps({"version": 1, "files": {"00_pack_a.json": sha,
                                                                                     "00_pack_b.json": sha}}))
    api.save_converter(d, "00_pack_b.json", '{"x": {}}')              # the user edits a pack copy
    assert {f["name"]: f["origin"] for f in api.list_converters(d)["files"]} == {
        "00_pack_a.json": "pack", "00_pack_b.json": "pack_edited", "10_mine.json": "user"}


def test_the_router(tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    restarts = []

    async def on_settings():
        restarts.append(1)

    app = fastapi.FastAPI()
    app.include_router(api.router(tmp_path, on_settings))
    c = TestClient(app)
    assert c.get("/api/rustuya-local/settings").json() == api.DEFAULTS
    assert c.put("/api/rustuya-local/settings", json={"il": {"prefix": "il/x"}}).status_code == 200 and restarts == [1]
    assert c.put("/api/rustuya-local/settings", json={"il": {"prefix": "#"}}).status_code == 400 and restarts == [1]
    assert c.put("/api/rustuya-local/converters/a.json", json={"content": "{}"}).json() == {"name": "a.json", "warnings": []}
    assert c.get("/api/rustuya-local/converters").json()["files"] == [{"name": "a.json", "size": 2, "origin": "user"}]
    assert c.get("/api/rustuya-local/converters/a.json").json()["content"] == "{}"
    assert c.get("/api/rustuya-local/converters/zz.json").status_code == 404
    assert c.put("/api/rustuya-local/converters/a.txt", json={"content": ""}).status_code == 400
    assert c.delete("/api/rustuya-local/converters/a.json").status_code == 200
