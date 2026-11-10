"""The built-in broker for a user without one, for real: pymqttd as a child process, the embedded bridge (pyrustuyabridge)
and the service on it. Skipped without pymqttd or pyrustuyabridge."""

from __future__ import annotations

import asyncio
import json
import uuid

import paho.mqtt.client as mqtt
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rustuya import async_restart_service, builtin_broker
from custom_components.rustuya.const import (
    CONF_ALLOW_HAZARDOUS,
    CONF_BRIDGE_LOG_LEVEL,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_BRIDGE_STATE_FILE,
    CONF_BROKER_BUILTIN,
    CONF_DEVICES_PATH,
    CONF_EXPOSE_UNUSED,
    CONF_PACK,
    DOMAIN,
)

pytest.importorskip("pymqttd")
pytest.importorskip("pyrustuyabridge")


@pytest.fixture
async def entry(hass, tmp_path, socket_enabled, monkeypatch):
    monkeypatch.setattr(builtin_broker, "RESTART_DELAYS", (0,))
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=2, data={
        CONF_BRIDGE_MODE: "embedded", CONF_BROKER_BUILTIN: True, CONF_BRIDGE_ROOT: "rustuya",
        CONF_DEVICES_PATH: str(tmp_path / "tuyadevices.json"), CONF_BRIDGE_STATE_FILE: str(tmp_path / "bridge.json"),
        CONF_BRIDGE_LOG_LEVEL: "warn",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        assert await hass.config_entries.async_unload(entry.entry_id)
    await builtin_broker.async_stop(hass)


def _connects(port: int, username: str | None = None, password: str | None = None) -> bool:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"probe-{uuid.uuid4().hex[:8]}")
    if username is not None:
        c.username_pw_set(username, password)
    c.connect("127.0.0.1", port)
    for _ in range(20):
        c.loop(0.1)
        if c.is_connected():
            break
    ok = c.is_connected()
    c.disconnect()
    return ok


async def _bridge_config(hass, timeout: float = 15.0) -> dict:
    """The bridge's retained `rustuya/bridge/config` on the built-in broker."""
    endpoint = await builtin_broker.async_ensure(hass)
    seen: list[bytes] = []
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"reader-{uuid.uuid4().hex[:8]}")
    c.username_pw_set(endpoint.username, endpoint.password)
    c.on_message = lambda _c, _u, m: seen.append(m.payload)
    c.connect(endpoint.host, endpoint.port)
    c.loop_start()
    c.subscribe("rustuya/bridge/config", 1)
    try:
        end = asyncio.get_running_loop().time() + timeout
        while not any(seen):
            assert asyncio.get_running_loop().time() < end, "no bridge config on the built-in broker"
            await asyncio.sleep(0.1)
    finally:
        c.loop_stop()
        c.disconnect()
    return json.loads(next(p for p in seen if p))


async def test_the_embedded_bridge_runs_on_the_built_in_broker(hass, entry):
    endpoint = await builtin_broker.async_ensure(hass)
    assert endpoint.host == "127.0.0.1"
    assert (await _bridge_config(hass)).get("mqtt_root_topic") == "rustuya"
    assert await hass.async_add_executor_job(_connects, endpoint.port, endpoint.username, endpoint.password)
    # other processes on the host reach 127.0.0.1 too
    assert not await hass.async_add_executor_job(_connects, endpoint.port)


async def test_restarts_keep_the_broker(hass, entry):
    endpoint = await builtin_broker.async_ensure(hass)
    pid = hass.data[f"{DOMAIN}_builtin_broker"].broker._proc.pid

    await async_restart_service(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert await builtin_broker.async_ensure(hass) == endpoint
    assert hass.data[f"{DOMAIN}_builtin_broker"].broker._proc.pid == pid
    assert (await _bridge_config(hass)).get("mqtt_root_topic") == "rustuya"


async def test_a_broker_that_dies_comes_back_on_the_same_port_and_the_bridge_with_it(hass, entry):
    state = hass.data[f"{DOMAIN}_builtin_broker"]
    endpoint = await builtin_broker.async_ensure(hass)
    dead = state.broker
    dead._proc.kill()
    end = asyncio.get_running_loop().time() + 10
    while state.broker is dead or not state.broker.running:
        assert asyncio.get_running_loop().time() < end, "the broker was not started again"
        await asyncio.sleep(0.1)
    assert await builtin_broker.async_ensure(hass) == endpoint
    # what it retained went with it: the bridge, reconnected, publishes its config again
    assert (await _bridge_config(hass, timeout=30)).get("mqtt_root_topic") == "rustuya"


async def test_deleting_the_entry_stops_the_broker(hass, entry):
    proc = hass.data[f"{DOMAIN}_builtin_broker"].broker._proc
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}_builtin_broker" not in hass.data
    assert proc.returncode is not None
