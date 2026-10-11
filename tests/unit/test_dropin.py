"""The rustuya-manager drop-in zip: one top-level package with tuya2ildevice vendored, byte-identical rebuilds."""
import hashlib
import importlib.util
import os
import subprocess
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
                   "rustuya_local/_vendor/tuya2ildevice/__init__.py", "rustuya_local/_vendor/tuya2ildevice/native.py"):
        assert needed in names
    assert "rustuya_local/_vendor/tuya2ildevice/rules/00-default.json" in names
    assert any(n.startswith("rustuya_local/_vendor/tuya2ildevice/") and n.endswith((".so", ".dll", ".dylib"))
               for n in names)
    assert not any("__pycache__" in n or n.startswith("rustuya_local/_vendor/paho") for n in names)


def test_rebuilds_are_byte_identical(tmp_path):
    a = build_dropin.build(tmp_path / "a").read_bytes()
    b = build_dropin.build(tmp_path / "b").read_bytes()
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


def test_archive_runs_driver_without_installed_tuya2ildevice(tmp_path):
    with zipfile.ZipFile(build_dropin.build(tmp_path)) as archive:
        archive.extractall(tmp_path)
    # -I -S excludes editable installs and site-packages: both native code and rules
    # must be usable from the extracted archive alone.
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from rustuya_local.host import Hub, BridgeCommand
from tuya2ildevice import Connected, Message
import tuya2ildevice
assert Path(tuya2ildevice.__file__).is_relative_to(Path(sys.argv[1]))
device = {'id': 'd', 'category': 'kg',
          'function': {'switch_1': {'type': 'Boolean', 'values': {}}},
          'local_strategy': {'1': {'value_convert': 'default', 'status_code': 'switch_1',
                                  'config_item': {'statusFormat': {'switch_1': '$'},
                                                  'valueType': 'Boolean', 'valueDesc': {},
                                                  'enumMappingMap': {}}}}}
hub = Hub([device])
hub.start(now=0)
hub.on_bridge_message(1, 'd', Connected())
hub.on_bridge_message(2, 'd', Message('active', {'1': True}))
actions = hub.on_il(3, 'il/d/switch_1/set', 'off')
assert BridgeCommand('d', 'set', {'1': False}) in actions, actions
"""
    env = {k: v for k, v in os.environ.items() if k not in {'TUYA_ENGINE_LIBRARY', 'TUYA_ENGINE_RULES'}}
    subprocess.run([sys.executable, '-I', '-S', '-c', script, str(tmp_path)],
                   env=env, check=True, capture_output=True, text=True, timeout=30)


def test_the_package_exposes_register():
    import rustuya_local
    assert callable(rustuya_local.register)
