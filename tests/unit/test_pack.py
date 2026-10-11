"""The override pack: `host.pack.sync` against a pack served from a directory (file://), and the one tuya2ildevice
publishes (its sibling checkout, when there is one)."""

import hashlib
import json
from pathlib import Path

import pytest

from rustuya_local.host import load_overrides, pack
from rustuya_local.host.pack import LEDGER, PackError, read_ledger, sync

PUBLISHED = Path(__file__).resolve().parents[3] / "tuya2ildevice" / "rules" / "pack"       # the sibling checkout


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Pack:
    """A pack directory with its manifest, served as file://."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.entries: dict[str, dict] = {}
        self.url = root.as_uri() + "/"

    def put(self, name: str, text: str, **bounds) -> None:
        (self.root / name).write_text(text)
        self.entries[name] = {"name": name, "sha256": sha(text), **bounds}
        self._manifest()

    def drop(self, name: str) -> None:
        (self.root / name).unlink()
        del self.entries[name]
        self._manifest()

    def _manifest(self) -> None:
        (self.root / "manifest.json").write_text(json.dumps({"version": 1, "files": list(self.entries.values())}))


@pytest.fixture
def served(tmp_path):
    return Pack(tmp_path / "served"), tmp_path / "custom_converters"


FIX = json.dumps({"overrides": {"abc123": {"dp": {"101": {"code": "switch_x", "type": "Boolean"}}}}})


def test_sync_adds_updates_and_removes_only_its_own_files(served):
    src, dest = served
    src.put("00_pack_a.json", FIX)
    r = sync(dest, base_url=src.url)
    assert r.added == ["00_pack_a.json"] and r.changed
    assert (dest / "00_pack_a.json").read_text() == FIX and read_ledger(dest) == {"00_pack_a.json": sha(FIX)}
    assert load_overrides(dest).overrides["abc123"]["dp"]["101"]["code"] == "switch_x"   # the ledger is not loaded

    assert not sync(dest, base_url=src.url).changed                                      # nothing new

    (dest / "99_mine.json").write_text("{}")
    src.put("00_pack_a.json", FIX.replace("switch_x", "switch_y"))
    assert sync(dest, base_url=src.url).updated == ["00_pack_a.json"]
    src.drop("00_pack_a.json")
    assert sync(dest, base_url=src.url).removed == ["00_pack_a.json"]
    assert sorted(p.name for p in dest.iterdir()) == [LEDGER, "99_mine.json"] and read_ledger(dest) == {}


def test_a_users_file_or_edit_is_never_overwritten_or_removed(served):
    src, dest = served
    dest.mkdir()
    (dest / "00_pack_a.json").write_text('{"overrides": {}}')            # the user's, same name as a pack file
    src.put("00_pack_a.json", FIX)
    src.put("00_pack_b.json", FIX)
    r = sync(dest, base_url=src.url)
    assert r.kept == ["00_pack_a.json"] and r.added == ["00_pack_b.json"]
    assert (dest / "00_pack_a.json").read_text() == '{"overrides": {}}'

    (dest / "00_pack_b.json").write_text('{"overrides": {"x": {}}}')     # our copy, edited: theirs now
    src.put("00_pack_b.json", FIX + " ")
    assert sync(dest, base_url=src.url).kept == ["00_pack_a.json", "00_pack_b.json"]
    src.drop("00_pack_b.json")
    sync(dest, base_url=src.url)
    assert (dest / "00_pack_b.json").read_text() == '{"overrides": {"x": {}}}'


def test_a_bad_file_is_skipped_and_the_copy_there_stays(served):
    src, dest = served
    src.put("00_pack_a.json", FIX)
    sync(dest, base_url=src.url)
    src.put("00_pack_a.json", "{not json")
    src.put("00_pack_c.json", "[1]")
    src.put("00_pack_d.py", "print('code')\n")                             # not a rule file: never taken
    r = sync(dest, base_url=src.url)
    assert [f.split(":")[0] for f in r.failed] == ["00_pack_a.json", "00_pack_c.json"]
    assert (dest / "00_pack_a.json").read_text() == FIX and "00_pack_a.json" in read_ledger(dest)
    assert not (dest / "00_pack_c.json").exists() and not (dest / "00_pack_d.py").exists()

    src.entries["00_pack_a.json"]["sha256"] = sha(FIX.replace("x", "z"))    # a file that does not match its hash
    src._manifest()
    assert "hash" in sync(dest, base_url=src.url).failed[0]
    assert (dest / "00_pack_a.json").read_text() == FIX


def test_files_are_bounded_by_level(served):
    src, dest = served
    src.put("00_pack_new.json", FIX, requires=pack.LEVEL + 1)          # needs a newer tuya2ildevice
    src.put("00_pack_old.json", FIX, until=pack.LEVEL)                  # built into this one
    src.put("00_pack_now.json", FIX, requires=pack.LEVEL, until=pack.LEVEL + 1)
    assert sync(dest, base_url=src.url).added == ["00_pack_now.json"]


def test_a_manifest_that_cannot_be_had_raises(tmp_path):
    with pytest.raises(PackError):
        sync(tmp_path / "d", base_url=(tmp_path / "nowhere").as_uri() + "/")
    with pytest.raises(PackError):
        sync(tmp_path / "d", base_url="ftp://example.invalid/")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.json").write_text("[]")
    with pytest.raises(PackError):
        sync(tmp_path / "d", base_url=(tmp_path / "bad").as_uri() + "/")


def test_unsafe_names_in_the_manifest_are_ignored(served):
    src, dest = served
    src.entries["../escape.json"] = {"name": "../escape.json", "sha256": sha(FIX)}
    src.entries[".hidden.json"] = {"name": ".hidden.json", "sha256": sha(FIX)}
    src._manifest()
    r = sync(dest, base_url=src.url)
    assert not r.changed and not (dest.parent / "escape.json").exists()


@pytest.mark.skipif(not PUBLISHED.is_dir(), reason="no tuya2ildevice checkout next to this one")
def test_the_published_pack_loads(tmp_path):
    r = sync(tmp_path / "d", base_url=PUBLISHED.as_uri() + "/")
    assert not r.failed
    assert not load_overrides(tmp_path / "d").warnings
