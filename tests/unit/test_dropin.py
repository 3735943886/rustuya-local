"""The rustuya-manager drop-in zip: one top-level package with tuya2ildevice vendored, byte-identical rebuilds."""
import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_dropin", ROOT / "scripts" / "build_dropin.py")
build_dropin = importlib.util.module_from_spec(spec)
sys.modules["build_dropin"] = build_dropin
spec.loader.exec_module(build_dropin)


def test_one_package_with_tuya2ildevice_inside(tmp_path):
    zf = zipfile.ZipFile(build_dropin.build(tmp_path))
    names = zf.namelist()
    assert {n.split("/", 1)[0] for n in names} == {"rustuya_local"}          # the manager imports each top-level entry
    for needed in ("rustuya_local/__init__.py", "rustuya_local/manager_plugin/static/index.js",
                   "rustuya_local/_vendor/tuya2ildevice/__init__.py", "rustuya_local/_vendor/tuya2ildevice/overrides.json"):
        assert needed in names
    assert not any("__pycache__" in n or n.startswith("rustuya_local/_vendor/paho") for n in names)


def test_rebuilds_are_byte_identical(tmp_path):
    a = build_dropin.build(tmp_path / "a").read_bytes()
    b = build_dropin.build(tmp_path / "b").read_bytes()
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


def test_the_package_exposes_register():
    import rustuya_local
    assert callable(rustuya_local.register)
