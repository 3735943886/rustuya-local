"""The rustuya-manager plugin under the manager's real plugin host (entry-point discovery, PluginContext, State),
against a real broker: the service it registers publishes IL, follows the manager's device set, reports
itself in the state namespace, and goes offline when the manager cancels it."""
import asyncio
import json

import pytest
from devices import lamp
from test_daemon import answer_status
from tuya2ildevice.host import MqttTransport

plugins = pytest.importorskip("rustuya_manager.plugins")
from rustuya_manager.models import Device
from rustuya_manager.mqtt import BridgeClient
from rustuya_manager.state import State


async def until(cond, what, n=200):
    for _ in range(n):
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(what)


async def test_the_plugin_runs_the_service_under_the_manager(broker, tmp_path):
    state = State()
    await state.set_cloud({"mp1": Device.from_dict(lamp("mp1"))})
    ctx = plugins.PluginContext(plugins.PluginRegistry(), state=state, data_root=tmp_path,
                                bridge_client=BridgeClient(f"mqtt://127.0.0.1:{broker}", "rmp", state))
    (tmp_path / "rustuya-local").mkdir()
    (tmp_path / "rustuya-local" / "settings.json").write_text(json.dumps({"il": {"prefix": "ilmp"}}))
    plugins.load_plugins(ctx)                                     # finds us through the entry point
    reg = ctx._registry
    assert [p["id"] for p in reg.pages] == ["rustuya-local"] and len(reg.services) == 1

    seen = {}
    w = MqttTransport("127.0.0.1", broker, client_id="mpwatch")
    await w.connect()
    await w.subscribe("ilmp/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    await answer_status(w, "rmp", ["mp1", "mp2"])
    task = asyncio.ensure_future(reg.services[0]())
    try:
        await until(lambda: "ilmp/mp1" in seen, "not published")
        assert seen["ilmp/_producer/tuya"] == "online"
        await until(lambda: (state.get_plugin_data("rustuya-local") or {}).get("devices"), "no status", n=300)
        status = state.get_plugin_data("rustuya-local")
        assert [d["id"] for d in status["devices"]] == ["mp1"]

        await state.set_cloud({"mp1": Device.from_dict(lamp("mp1")), "mp2": Device.from_dict(lamp("mp2"))})
        await until(lambda: "ilmp/mp2" in seen, "mp2 not followed")
        assert (tmp_path / "rustuya-local" / "custom_converters").is_dir()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await until(lambda: seen.get("ilmp/_producer/tuya") == "offline", "not offline after cancel")
    assert state.get_plugin_data("rustuya-local")["running"] is False

    for topic in list(seen):
        await w.publish(topic, "", 1, True)
    await asyncio.sleep(0.2)
    await w.close()


def test_an_old_manager_is_refused(caplog):
    from rustuya_local.manager_plugin import register

    class Old:
        api_version = 3
    register(Old())
    assert "api_version >= 4" in caplog.text


def _ctx(tmp_path):
    state = State()
    return plugins.PluginContext(plugins.PluginRegistry(), state=state, data_root=tmp_path,
                                 bridge_client=BridgeClient("mqtt://127.0.0.1:1", "r", state))


def test_installed_twice_it_registers_once(tmp_path, caplog):
    import rustuya_local
    from rustuya_local.manager_plugin import register
    ctx = _ctx(tmp_path)
    register(ctx)                      # the entry point
    rustuya_local.register(ctx)        # the dropped-in package's top-level register
    assert len(ctx._registry.services) == 1 and len(ctx._registry.pages) == 1
    assert "installed twice" in caplog.text
    other = _ctx(tmp_path / "other")
    register(other)                    # another manager (context) is not a duplicate
    assert len(other._registry.services) == 1


DROPIN_CHECK = r"""
import importlib.machinery, sys, tempfile
from pathlib import Path
plugin_dir = Path(sys.argv[1])
vendor = plugin_dir / "rustuya_local" / "_vendor"

class OnlyVendored:
    # tuya2ildevice may be installed here; a manager's environment has none, so resolve it from the zip only
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] != "tuya2ildevice" or path is not None:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, [str(vendor)])
        if spec is None:
            raise ImportError(f"{name} is not in the drop-in zip")
        return spec

sys.meta_path.insert(0, OnlyVendored())
from rustuya_manager import plugins
from rustuya_manager.mqtt import BridgeClient
from rustuya_manager.state import State
registers = plugins._discover_dir_plugins([str(plugin_dir)])
state = State()
ctx = plugins.PluginContext(plugins.PluginRegistry(), state=state, data_root=Path(tempfile.mkdtemp()),
                            bridge_client=BridgeClient("mqtt://127.0.0.1:1", "r", state))
for r in registers:
    r(ctx)
import rustuya_local, tuya2ildevice
from rustuya_local.service import Service
from tuya2ildevice.host import MqttTransport
assert Path(rustuya_local.__file__).is_relative_to(plugin_dir), rustuya_local.__file__
assert Path(tuya2ildevice.__file__).is_relative_to(vendor), tuya2ildevice.__file__
print(len(registers), [p["id"] for p in ctx._registry.pages], len(ctx._registry.services))
"""


def test_the_drop_in_zip_loads_in_the_manager_with_its_vendored_tuya2ildevice(tmp_path):
    import importlib.util
    import subprocess
    import sys
    import zipfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("build_dropin", root / "scripts" / "build_dropin.py")
    build_dropin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_dropin)
    plugin_dir = tmp_path / "plugins"
    zipfile.ZipFile(build_dropin.build(tmp_path)).extractall(plugin_dir)
    out = subprocess.run([sys.executable, "-c", DROPIN_CHECK, str(plugin_dir)], capture_output=True, text=True,
                         cwd=tmp_path, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["1", "['rustuya-local']", "1"]
