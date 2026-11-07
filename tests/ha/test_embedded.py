"""The ildevice hub embedded in the integration, on a real service: a real broker on the bridge's side (mosquitto, skipped
if missing), the in-process transport on the IL side, entities registered under this integration."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from devices import lamp
from test_init import Watcher, il, until

from custom_components.rustuya import (
    async_remove_config_entry_device,
    async_restart_service,
)
from custom_components.rustuya.const import (
    CONF_ALLOW_HAZARDOUS,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_DEVICES_PATH,
    CONF_EXPOSE_UNUSED,
    CONF_PACK,
    DOMAIN,
    PLATFORMS,
)

CONNECTED = '{"errorCode":0,"errorMsg":"Connection Successful"}'
LAMP_ON = '{"20":true,"22":1000,"23":0}'


@pytest.fixture
async def setup(hass, broker, tmp_path, socket_enabled):
    """A loaded entry driving lamp1 (and whatever `ids` says), and the stand-in for the bridge."""
    watchers = []
    devices_path = tmp_path / "tuyadevices.json"

    async def start(ids=("lamp1",)):
        devices_path.write_text(json.dumps([lamp(i) for i in ids]))
        entry = MockConfigEntry(domain=DOMAIN, data={
            CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": broker, "broker_username": "",
            "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices_path),
        }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
        entry.add_to_hass(hass)
        watcher = Watcher(broker, registered=ids)
        watchers.append(watcher)
        for i in ids:
            watcher.publish(f"rustuya/error/{i}", CONNECTED, retain=True)
            watcher.publish(f"rustuya/event/state/{i}", LAMP_ON, retain=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for i in ids:
            await until(lambda d=i: il(entry, hass).get(f"il/{d}/brightness") == "100")
        await hass.async_block_till_done()
        return entry, watcher, devices_path

    yield start
    for watcher in watchers:
        await watcher.clear_and_close()


def _device(hass, device_id: str):
    """The Home Assistant device of a Tuya id."""
    registry = dr.async_get(hass)
    return next((d for entry in hass.config_entries.async_entries(DOMAIN)
                 for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
                 if (DOMAIN, device_id) in d.identifiers), None)


def _light(hass, device_id: str) -> str | None:
    """The entity id of the device's light, registered under this integration."""
    registry = er.async_get(hass)
    device = _device(hass, device_id)
    if device is None:
        return None
    return next((e.entity_id for e in er.async_entries_for_device(registry, device.id) if e.domain == "light"), None)


def test_the_platforms_are_ildevices():
    from ildevice_ha.const import PLATFORMS as ILDEVICE_PLATFORMS

    assert PLATFORMS == ILDEVICE_PLATFORMS
    root = Path(__file__).resolve().parents[2] / "custom_components/rustuya"
    assert all((root / f"{platform}.py").is_file() for platform in PLATFORMS)


async def test_devices_become_entities_of_this_integration(hass, setup):
    entry, watcher, _ = await setup()
    entity_id = _light(hass, "lamp1")
    assert entity_id is not None
    assert er.async_get(hass).async_get(entity_id).platform == DOMAIN
    await until(lambda: hass.states.get(entity_id).state == "on")
    assert hass.states.get(entity_id).attributes["brightness"] == 255

    # a command goes through the whole chain to the bridge
    await hass.services.async_call("light", "turn_off", {"entity_id": entity_id}, blocking=True)
    await until(lambda: json.loads(watcher.last.get("rustuya/command", "{}")).get("dps", {}).get("20") is False)
    assert not [t for t in watcher.last if t.startswith("il/")]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert er.async_get(hass).async_get(entity_id) is not None      # a stop forgets nothing


async def test_the_last_values_survive_a_restart_until_the_device_says_otherwise(hass, setup):
    """Without a broker holding them, the store does: the values come back at once, marked unavailable until the service
    hears of the device, which the bridge has nothing to say about here (its retained topics are gone)."""
    entry, watcher, _ = await setup()
    entity_id = _light(hass, "lamp1")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    watcher.publish("rustuya/event/state/lamp1", "", retain=True)
    watcher.publish("rustuya/error/lamp1", "", retain=True)
    watcher.registered.clear()                                      # and the bridge does not answer with the device

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    retained = il(entry, hass)
    assert retained["il/lamp1/brightness"] == "100" and retained["il/lamp1/available"] == "false"
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_device_gone_while_home_assistant_was_down_leaves_the_registry(hass, setup):
    entry, _, devices_path = await setup(("lamp1", "lamp2"))
    assert _light(hass, "lamp2") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    devices_path.write_text(json.dumps([lamp("lamp1")]))

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await until(lambda: "il/lamp2/descriptor" not in il(entry, hass))
    await until(lambda: _device(hass, "lamp2") is None)
    assert _light(hass, "lamp1") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_an_options_change_restarts_the_service_and_keeps_the_entities(hass, setup):
    """No device goes `unavailable` and back (an event entity coming back looks pressed to an automation that follows
    its state), and the hub stays."""
    entry, watcher, _ = await setup()
    entity_id = _light(hass, "lamp1")
    await until(lambda: hass.states.get(entity_id).state == "on")
    runtime = hass.data[DOMAIN][entry.entry_id]
    hub, service = runtime.hub, runtime.service
    seen = []
    await runtime.transport.subscribe("il/#", lambda m: seen.append((m.topic, m.payload)))
    seen.clear()
    states = []
    hass.bus.async_listen("state_changed", lambda e: states.append(e.data["new_state"].state)
                          if e.data["entity_id"] == entity_id else None)
    watcher.log.clear()

    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_EXPOSE_UNUSED: True})
    await hass.async_block_till_done()
    await until(lambda: runtime.service is not service)
    await until(lambda: any(t == "rustuya/command" for t, _ in watcher.log))     # the new one asked the bridge
    await asyncio.sleep(1)
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id] is runtime and runtime.hub is hub
    assert ("il/_producer/tuya", "offline") not in seen
    assert "unavailable" not in states
    assert il(runtime)["il/_producer/tuya"] == "online"
    assert runtime.options[CONF_EXPOSE_UNUSED] is True

    await async_restart_service(hass, entry)                        # by hand too, as the panel does
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert il(runtime)["il/_producer/tuya"] == "offline"


async def test_a_device_still_driven_cannot_be_deleted_but_a_leftover_can(hass, setup):
    entry, _, _ = await setup()
    registry = dr.async_get(hass)
    device = _device(hass, "lamp1")
    with pytest.raises(HomeAssistantError) as e:
        await async_remove_config_entry_device(hass, entry, device)
    assert e.value.translation_key == "device_on_bridge"
    leftover = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "gone")})
    assert await async_remove_config_entry_device(hass, entry, leftover)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not await async_remove_config_entry_device(hass, entry, leftover)      # not running: nothing to tell by


async def test_deleting_the_entry_removes_its_store_and_registry_entries(hass, setup, hass_storage):
    entry, _, _ = await setup()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    key = f"{DOMAIN}.{entry.entry_id}.retained"
    assert "il/lamp1/descriptor" in hass_storage[key]["data"]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
    assert _device(hass, "lamp1") is None


async def test_a_failed_service_start_leaves_nothing_behind(hass, tmp_path, socket_enabled):
    from homeassistant.config_entries import ConfigEntryState

    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "127.0.0.1", "broker_port": 1, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(tmp_path / "t.json"),
    }, options={CONF_PACK: False})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
