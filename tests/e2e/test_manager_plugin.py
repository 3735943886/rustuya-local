"""The rustuya-manager plugin under the manager's real plugin host (entry-point discovery, PluginContext, State),
against a real broker: the service it registers publishes IL and discovery, follows the manager's device set, reports
itself in the state namespace, and goes offline when the manager cancels it."""
import asyncio
import json

import pytest
from devices import lamp
from test_daemon import answer_status

from tuya2ildevice.host import MqttTransport

plugins = pytest.importorskip("rustuya_manager.plugins")
from rustuya_manager.models import Device  # noqa: E402
from rustuya_manager.mqtt import BridgeClient  # noqa: E402
from rustuya_manager.state import State  # noqa: E402


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
    (tmp_path / "rustuya-local" / "settings.json").write_text(json.dumps({
        "il": {"prefix": "ilmp"}, "discovery": {"prefix": "hamp", "node_id": "mp", "relay_prefix": "rmp-ha"}}))
    plugins.load_plugins(ctx)                                     # finds us through the entry point
    reg = ctx._registry
    assert [p["id"] for p in reg.pages] == ["rustuya-local"] and len(reg.services) == 1

    seen = {}
    w = MqttTransport("127.0.0.1", broker, client_id="mpwatch")
    await w.connect()
    for f in ("ilmp/#", "hamp/#"):
        await w.subscribe(f, lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    await answer_status(w, "rmp", ["mp1", "mp2"])
    task = asyncio.ensure_future(reg.services[0]())
    try:
        await until(lambda: "ilmp/mp1" in seen and "hamp/light/mp/mp1_light/config" in seen, "not published")
        assert seen["ilmp/_producer/tuya"] == "online"
        await until(lambda: (state.get_plugin_data("rustuya-local") or {}).get("devices"), "no status", n=300)
        status = state.get_plugin_data("rustuya-local")
        assert [d["id"] for d in status["devices"]] == ["mp1"] and status["discovery"]["configs"] == 1

        await state.set_cloud({"mp1": Device.from_dict(lamp("mp1")), "mp2": Device.from_dict(lamp("mp2"))})
        await until(lambda: "ilmp/mp2" in seen and "hamp/light/mp/mp2_light/config" in seen, "mp2 not followed")
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
