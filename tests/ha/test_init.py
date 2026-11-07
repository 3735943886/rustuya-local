"""`async_setup_entry`/`async_unload_entry` for real: a real MQTT broker (mosquitto, skipped if missing) for the bridge's
side, a real `rustuya_local.host.Runner` and the embedded ildevice hub on the in-process transport. No bridge and no
`rustuya_manager` (only the config flow needs it, and only for onboarding)."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
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
        # an id of its own: a second client on the same id takes the session over, and paho reconnecting the
        # first one takes it back, each dropping the other's connection
        self._c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"watcher-{uuid.uuid4().hex[:8]}")
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

    async def clear_and_close(self) -> None:
        """Clear every retained topic seen, then close: always, so a client left reconnecting cannot outlive the
        test it belongs to."""
        try:
            for topic in list(self.last):
                self.publish(topic, "", retain=True)
            await asyncio.sleep(0.1)
        finally:
            self.close()


def il(entry_or_runtime, hass=None) -> dict[str, str]:
    """What the in-process transport retains: the IL topics, as text."""
    runtime = hass.data[DOMAIN][entry_or_runtime.entry_id] if hass is not None else entry_or_runtime
    return {t: m.payload.decode() if isinstance(m.payload, bytes) else m.payload
            for t, m in runtime.transport.retained.items()}


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
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1",))
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        runtime = hass.data[DOMAIN][entry.entry_id]

        await until(lambda: il(runtime).get("il/_producer/tuya") == "online")
        await until(lambda: "il/lamp1/descriptor" in il(runtime))
        assert json.loads(il(runtime)["il/lamp1/descriptor"])["kind"] == "light"

        # the runner asked the bridge for the device's state, exactly as the standalone daemon does
        watcher.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', retain=True)
        await until(lambda: json.loads(watcher.last.get("rustuya/command", "{}")).get("action") == "get")

        watcher.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', retain=True)
        await until(lambda: il(runtime).get("il/lamp1/brightness") == "100")

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert il(runtime)["il/_producer/tuya"] == "offline"
        assert not [t for t in watcher.last if t.startswith("il/")]         # nothing of it reaches the broker
    finally:
        await watcher.clear_and_close()


async def test_setup_fails_cleanly_when_the_broker_is_unreachable(hass, tmp_path, socket_enabled):
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": 1, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_driving_the_first_device_does_no_blocking_io_in_the_event_loop(hass, broker, tmp_path, socket_enabled,
                                                                               monkeypatch):
    """The host reads rule files and the binding loads its engine: the service preloads them in a worker
    thread, so Home Assistant sees no blocking call when the first device's driver is made."""
    from tuya_interpreter import native

    from rustuya_local.host.rules import rules

    for loader in (native.library, rules):
        loader.cache_clear()                                    # as in a fresh Home Assistant process
    import pathlib
    import threading

    loop_thread, in_loop = threading.current_thread(), []
    real_read = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):      # what Home Assistant's blocking-call detector flags, recorded instead
        if threading.current_thread() is loop_thread and "tuya_interpreter" in str(self):
            in_loop.append(self.name)
        return real_read(self, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    devices_path = tmp_path / "tuyadevices.json"
    devices_path.write_text(json.dumps([lamp("lamp1")]))
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1",))
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: "il/lamp1/descriptor" in il(entry, hass))
        assert in_loop == []
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        await watcher.clear_and_close()


async def test_deleting_an_entry_from_before_the_hub_clears_what_it_left_on_the_broker(hass, broker, tmp_path,
                                                                                       socket_enabled):
    """It published IL on the broker, under the prefix and source in its data: deleting it clears every retained topic
    of that source, and nothing of another source on the same prefix. A new entry has no such keys, and leaves IL alone."""
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(tmp_path / "t.json"),
        CONF_IL_PREFIX: "il", CONF_IL_SOURCE: "tuya",
    }, options={CONF_PACK: False}, minor_version=2)
    entry.add_to_hass(hass)
    watcher = Watcher(broker)
    try:
        watcher.publish("il/stale1/descriptor", json.dumps({"il": 1, "id": "stale1", "source": "tuya", "props": {}}), retain=True)
        watcher.publish("il/stale1/power", "true", retain=True)
        watcher.publish("il/zb1/descriptor", json.dumps({"il": 1, "id": "zb1", "source": "zigbee", "props": {}}), retain=True)
        watcher.publish("il/zb1/power", "true", retain=True)
        watcher.publish("il/_producer/tuya", "offline", retain=True)
        with patch("custom_components.rustuya.async_setup_entry", return_value=False):
            await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        for topic in ("il/stale1/descriptor", "il/stale1/power", "il/_producer/tuya"):
            await until(lambda t=topic: watcher.last.get(t) == "")
        assert watcher.last["il/zb1/power"] == "true" and json.loads(watcher.last["il/zb1/descriptor"])["source"] == "zigbee"

        new = MockConfigEntry(domain=DOMAIN, data={
            CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
            "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(tmp_path / "t.json"),
        }, options={CONF_PACK: False}, minor_version=2)
        new.add_to_hass(hass)
        watcher.publish("il/stale2/descriptor", json.dumps({"il": 1, "id": "stale2", "source": "tuya", "props": {}}), retain=True)
        with patch("custom_components.rustuya.async_setup_entry", return_value=False):
            await hass.config_entries.async_remove(new.entry_id)
        await hass.async_block_till_done()
        await asyncio.sleep(0.3)
        assert watcher.last["il/stale2/descriptor"] != ""
    finally:
        await watcher.clear_and_close()


async def _remove_with(hass, broker, tmp_path, mode: str, devices_path: str):
    from custom_components.rustuya.const import CONF_BRIDGE_STATE_FILE

    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: mode, "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: devices_path,
        CONF_BRIDGE_STATE_FILE: hass.config.path(".storage/rustuya/bridge_state.json"),
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
        await watcher.clear_and_close()


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
        await watcher.clear_and_close()


async def test_setup_without_a_device_file_starts_with_no_devices(hass, broker, tmp_path, socket_enabled):
    """Set up without the cloud login (closed, or left for later): no device file yet, and no failed entry."""
    from homeassistant.config_entries import ConfigEntryState

    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(tmp_path / "none" / "t.json"),
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
        try:
            watcher.publish("il/_producer/tuya", "", retain=True)
        finally:
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
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    watcher = Watcher(broker, registered=("lamp1", "lock1"))
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await until(lambda: "il/lamp1/descriptor" in il(entry, hass) and "il/lock1/descriptor" in il(entry, hass))

        await hass.services.async_call(DOMAIN, "send_command", {"device_id": "lamp1", "dp": 20, "value": False},
                                       blocking=True)
        await until(lambda: json.loads(watcher.last.get("rustuya/command", "{}")).get("action") == "set")
        assert json.loads(watcher.last["rustuya/command"]) == {"action": "set", "id": "lamp1", "dps": {"20": False}}

        # the Home Assistant device the embedded hub registered: the IL id (the Tuya id) among its identifiers
        from homeassistant.helpers import device_registry as dr

        registry = dr.async_get(hass)

        def ha_device(device_id):
            return next((d for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
                         if (DOMAIN, device_id) in d.identifiers), None)

        await until(lambda: ha_device("lamp1") is not None)
        ha_lamp = ha_device("lamp1")
        # the test's lock has no entity the hub would register it for
        ha_lock = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "lock1")})
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
        await watcher.clear_and_close()
