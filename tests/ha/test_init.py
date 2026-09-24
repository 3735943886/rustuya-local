"""`async_setup_entry`/`async_unload_entry` for real: a real MQTT broker (mosquitto, skipped if missing), a real
`tuya2ildevice.host.Runner`. No il-ha, no bridge, no `rustuya_manager` — this integration produces IL and does not
depend on any of them at runtime (only its config flow does, and only for onboarding)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
from devices import lamp, status_reply  # noqa: E402

from custom_components.rustuya.const import (  # noqa: E402
    CONF_ALLOW_HAZARDOUS,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_DEVICES_PATH,
    CONF_EXPOSE_UNUSED,
    CONF_IL_PREFIX,
    CONF_IL_SOURCE,
    DOMAIN,
)


class Watcher:
    """A plain paho client, standing in for whatever actually reads this integration's output (a real rustuya-bridge
    on one side, an IL consumer on the other) — the test only cares what lands on the wire."""

    def __init__(self, port: int, registered: tuple[str, ...] = ()) -> None:
        self.last: dict[str, str] = {}
        self.registered = list(registered)               # the devices the stand-in bridge "holds"
        self._c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="watcher")
        self._c.on_message = self._on_message
        self._c.connect("127.0.0.1", port, 30)
        self._c.loop_start()
        self._c.subscribe("il/#", 1)
        self._c.subscribe("rustuya/#", 1)

    def _on_message(self, _c, _u, m) -> None:
        payload = m.payload.decode()
        self.last[m.topic] = payload
        if m.topic == "rustuya/command" and payload and json.loads(payload).get("action") == "status":
            self._c.publish("rustuya/response/bridge", status_reply(self.registered), qos=1)

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self._c.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)

    def close(self) -> None:
        self._c.loop_stop()
        self._c.disconnect()


async def until(condition, timeout: float = 10.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < end, "condition not met in time"
        await asyncio.sleep(0.05)


async def test_setup_publishes_and_unload_goes_offline(hass, broker, tmp_path, socket_enabled):
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1",))
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await until(lambda: "il/_producer/tuya" in watcher.last)
        assert watcher.last["il/_producer/tuya"] == "online"
        await until(lambda: "il/lamp1" in watcher.last)
        assert json.loads(watcher.last["il/lamp1"])["kind"] == "light"

        # the runner asked the bridge for the device's state, exactly as the standalone daemon does
        watcher.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', retain=True)
        await until(lambda: json.loads(watcher.last.get("rustuya/command", "{}")).get("action") == "get")

        watcher.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', retain=True)
        await until(lambda: watcher.last.get("il/lamp1/brightness") == "100")

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: watcher.last.get("il/_producer/tuya") == "offline")
    finally:
        for topic in list(watcher.last):
            watcher.publish(topic, "", retain=True)
        await asyncio.sleep(0.1)
        watcher.close()


async def test_setup_fails_cleanly_when_the_broker_is_unreachable(hass, tmp_path, socket_enabled):
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": 1, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
