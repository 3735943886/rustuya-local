"""`async_setup_entry`/`async_unload_entry` for real: a real MQTT broker (mosquitto, skipped if missing), a real
`tuya2ildevice.host.Runner`. No il-ha, no bridge, no `rustuya_manager` — this integration produces IL and does not
depend on any of them at runtime (only its config flow does, and only for onboarding)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import paho.mqtt.client as mqtt
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
from devices import lamp, status_reply

from custom_components.rustuya.const import (
    CONF_ALLOW_HAZARDOUS,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_DEVICES_PATH,
    CONF_EXPOSE_UNUSED,
    CONF_IL_PREFIX,
    CONF_IL_SOURCE,
    CONF_PACK,
    DOMAIN,
)


class Watcher:
    """A plain paho client, standing in for whatever actually reads this integration's output (a real rustuya-bridge
    on one side, an IL consumer on the other) — the test only cares what lands on the wire."""

    def __init__(self, port: int, registered: tuple[str, ...] = ()) -> None:
        self.last: dict[str, str] = {}
        self.log: list[tuple[str, str]] = []             # every message, in order
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
        self.log.append((m.topic, payload))
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
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
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


async def test_setup_is_retried_while_another_producer_runs(hass, broker, tmp_path, socket_enabled):
    from homeassistant.config_entries import ConfigEntryState
    from tuya2ildevice import Hub, IlTopics
    from tuya2ildevice.host import MqttTransport, Runner

    other = Hub([lamp("lamp1")], il=IlTopics("il", "tuya"))            # a daemon, say, already serving il / tuya
    will = other.presence(False)
    t = MqttTransport("127.0.0.1", broker, client_id="other-producer", will=(will.topic, will.payload, will.qos,
                                                                            will.retain))
    await t.connect()
    runner = Runner(other, t)
    await runner.start()
    await runner.drain()
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    try:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert "another producer" in (entry.reason or "")
    finally:
        await runner.stop()
        await t.publish("il/_producer/tuya", "", 1, True)
        await t.publish("il/lamp1", "", 1, True)
        await t.close()


async def test_setup_fails_cleanly_when_the_broker_is_unreachable(hass, tmp_path, socket_enabled):
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": 1, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_driving_the_first_device_does_no_blocking_io_in_the_event_loop(hass, broker, tmp_path, socket_enabled,
                                                                               monkeypatch):
    """tuya2ildevice reads its data files on first use (quirks, platform tables): the service preloads them in a
    worker thread, so Home Assistant sees no blocking call when the first device's driver is made."""
    from tuya2ildevice.tuya import quirks, runtime

    monkeypatch.setattr(quirks, "_QUIRKS", None)               # as in a fresh Home Assistant process
    monkeypatch.setattr(runtime, "_TABLE_CACHE", {})
    import pathlib
    import threading

    loop_thread, in_loop = threading.current_thread(), []
    real_read = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):      # what Home Assistant's blocking-call detector flags, recorded instead
        if threading.current_thread() is loop_thread and "tuya2ildevice" in str(self):
            in_loop.append(self.name)
        return real_read(self, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1",))
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: "il/lamp1" in watcher.last)
        assert in_loop == []
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for topic in list(watcher.last):
            watcher.publish(topic, "", retain=True)
        await asyncio.sleep(0.1)
        watcher.close()


async def test_deleting_the_entry_takes_its_devices_out_of_il(hass, broker, tmp_path, socket_enabled):
    """Unloading keeps the descriptors (the producer comes back); deleting clears every retained topic this source
    left, including a device an earlier run published that the current one no longer drives, and nothing of another
    source on the same prefix."""
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1",))
    try:
        watcher.publish("il/stale1", json.dumps({"il": 1, "id": "stale1", "source": "tuya", "props": {}}), retain=True)
        watcher.publish("il/stale1/power", "true", retain=True)
        watcher.publish("il/zb1", json.dumps({"il": 1, "id": "zb1", "source": "zigbee", "props": {}}), retain=True)
        watcher.publish("il/zb1/power", "true", retain=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: "il/lamp1" in watcher.last)
        watcher.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', retain=True)
        await until(lambda: watcher.last.get("il/lamp1/brightness") == "100")

        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        for topic in ("il/lamp1", "il/lamp1/brightness", "il/stale1", "il/stale1/power", "il/_producer/tuya"):
            try:
                await until(lambda t=topic: watcher.last.get(t) == "")
            except AssertionError:
                raise AssertionError((topic, dict(watcher.last)))
        assert watcher.last["il/zb1/power"] == "true" and json.loads(watcher.last["il/zb1"])["source"] == "zigbee"
        # nothing is left retained for it: a fresh subscriber sees none of those topics
        late = Watcher(broker)
        await asyncio.sleep(0.5)
        late.close()
        assert not [t for t, p in late.last.items() if p and t.startswith(("il/lamp1", "il/stale1", "il/_producer"))]
    finally:
        for topic in list(watcher.last):
            watcher.publish(topic, "", retain=True)
        await asyncio.sleep(0.1)
        watcher.close()


async def _remove_with(hass, broker, tmp_path, mode: str, devices_path: str):
    from custom_components.rustuya.const import CONF_BRIDGE_STATE_FILE

    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: mode, "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: devices_path,
        CONF_BRIDGE_STATE_FILE: hass.config.path(".storage/rustuya/bridge_state.json"),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_PACK: False}, minor_version=2)
    entry.add_to_hass(hass)
    with patch("custom_components.rustuya.async_setup_entry", return_value=False):     # never loaded: remove only
        await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()


async def test_deleting_an_embedded_entry_clears_its_bridge_and_its_files(hass, broker, tmp_path, socket_enabled):
    storage = Path(hass.config.path(".storage/rustuya"))
    storage.mkdir(parents=True, exist_ok=True)
    for name in ("tuyadevices.json", "tuyacreds.json", "bridge_state.json"):
        (storage / name).write_text("{}")
    watcher = Watcher(broker)
    try:
        watcher.publish("rustuya/error/dev1", '{"errorCode":905}', retain=True)
        watcher.publish("rustuya/event/state/dev1", '{"20":true}', retain=True)
        await _remove_with(hass, broker, tmp_path, "embedded", str(storage / "tuyadevices.json"))
        await until(lambda: watcher.last.get("rustuya/error/dev1") == "" and
                    watcher.last.get("rustuya/event/state/dev1") == "")
        assert not storage.exists()
    finally:
        for topic in list(watcher.last):
            watcher.publish(topic, "", retain=True)
        watcher.close()


async def test_deleting_an_external_entry_leaves_the_bridge_and_a_users_own_device_file(hass, broker, tmp_path,
                                                                                       socket_enabled):
    own = tmp_path / "tuyadevices.json"                  # the user pointed at rustuya-manager's own file
    own.write_text("[]")
    (tmp_path / "tuyacreds.json").write_text("{}")
    watcher = Watcher(broker)
    try:
        watcher.publish("rustuya/error/dev1", '{"errorCode":905}', retain=True)
        await _remove_with(hass, broker, tmp_path, "external", str(own))
        await asyncio.sleep(0.3)
        assert watcher.last["rustuya/error/dev1"] == '{"errorCode":905}'
        assert own.exists() and (tmp_path / "tuyacreds.json").exists()
    finally:
        for topic in list(watcher.last):
            watcher.publish(topic, "", retain=True)
        watcher.close()


async def test_setup_without_a_device_file_starts_with_no_devices(hass, broker, tmp_path, socket_enabled):
    """Set up without the cloud login (closed, or left for later): no device file yet, and no failed entry."""
    from homeassistant.config_entries import ConfigEntryState

    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(tmp_path / "none" / "t.json"),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        watcher = Watcher(broker)
        watcher.publish("il/_producer/tuya", "", retain=True)
        watcher.close()


async def test_a_device_file_for_an_entry_that_is_not_running_restarts_it(hass, tmp_path, monkeypatch):
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.rustuya import async_refresh_devices

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICES_PATH: str(tmp_path / "t.json")})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    reloaded = []
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reloaded.append)
    await async_refresh_devices(hass, entry)
    assert reloaded == [entry.entry_id]


async def test_send_command_writes_a_raw_dp_and_refuses_a_lock(hass, broker, tmp_path, socket_enabled):
    """`rustuya.send_command`: one DP straight to the bridge, by Tuya id or Home Assistant device; a lock (by its Tuya category) is refused while remote
    control of locks, alarms and garage doors is off, as is a device the bridge does not hold."""
    from homeassistant.exceptions import ServiceValidationError

    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1"), {**lamp("lock1"), "category": "ms"}]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1", "lock1"))
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: "il/lamp1" in watcher.last and "il/lock1" in watcher.last)

        await hass.services.async_call(DOMAIN, "send_command", {"device_id": "lamp1", "dp": 20, "value": False},
                                       blocking=True)
        await until(lambda: json.loads(watcher.last.get("rustuya/command", "{}")).get("action") == "set")
        assert json.loads(watcher.last["rustuya/command"]) == {"action": "set", "id": "lamp1", "dps": {"20": False}}

        # a Home Assistant device, as il-ha registers one: the IL id (the Tuya id) among its identifiers
        from homeassistant.helpers import device_registry as dr

        il_ha = MockConfigEntry(domain="ildevice")
        il_ha.add_to_hass(hass)
        registry = dr.async_get(hass)
        ha_lamp = registry.async_get_or_create(config_entry_id=il_ha.entry_id, identifiers={("ildevice", "lamp1")})
        ha_lock = registry.async_get_or_create(config_entry_id=il_ha.entry_id, identifiers={("ildevice", "lock1")})
        await hass.services.async_call(DOMAIN, "send_command", {"device_id": ha_lamp.id, "dp": "22", "value": 500},
                                       blocking=True)
        await until(lambda: json.loads(watcher.last["rustuya/command"]).get("dps") == {"22": 500})

        for device_id, reason in (("lock1", "hazardous"), (ha_lock.id, "hazardous"), ("nope", "unknown_device")):
            try:
                await hass.services.async_call(DOMAIN, "send_command", {"device_id": device_id, "dp": "1",
                                                                        "value": True}, blocking=True)
            except ServiceValidationError as e:
                assert e.translation_key == reason
            else:
                raise AssertionError(f"{device_id} was not refused")

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for topic in list(watcher.last):
            watcher.publish(topic, "", retain=True)
        await asyncio.sleep(0.1)
        watcher.close()


async def test_an_options_change_restarts_without_the_devices_going_away(hass, broker, tmp_path, socket_enabled):
    """Changing an option restarts the service, but IL consumers see no gap: the presence stays `online` and no device
    says `available: false` (an event entity coming back from `unavailable` looks pressed to an automation that
    follows its state). Unloading for real still goes offline."""
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1",))
    try:
        watcher.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', retain=True)
        watcher.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', retain=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: watcher.last.get("il/lamp1/brightness") == "100")
        await until(lambda: watcher.last.get("il/lamp1/available") == "true")
        runtime = hass.data[DOMAIN][entry.entry_id]
        watcher.log.clear()

        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_EXPOSE_UNUSED: True})
        await hass.async_block_till_done()
        await until(lambda: hass.data[DOMAIN].get(entry.entry_id) not in (None, runtime))
        await until(lambda: any(t == "rustuya/command" for t, _ in watcher.log))          # the new one asked the bridge
        await asyncio.sleep(1)
        assert ("il/_producer/tuya", "offline") not in watcher.log
        assert ("il/lamp1/available", "false") not in watcher.log
        assert watcher.last["il/_producer/tuya"] == "online"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: watcher.last.get("il/_producer/tuya") == "offline")
    finally:
        for topic in list(watcher.last):
            watcher.publish(topic, "", retain=True)
        await asyncio.sleep(0.1)
        watcher.close()
