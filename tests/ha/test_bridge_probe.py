"""The external step's bridge check (`_probe_bridge`) against a real mosquitto: a running bridge is known by its
retained `{root}/bridge/config`."""

from __future__ import annotations

import json

import pytest

from custom_components.rustuya import config_flow


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(config_flow, "PROBE_TIMEOUT", 1.0)


def _data(port: int, root: str = "rustuya") -> dict:
    return {"broker_host": "127.0.0.1", "broker_port": port, "broker_username": "", "broker_password": "",
            "bridge_root": root}


async def _retain(port: int, topic: str, payload: str) -> None:
    from tuya2ildevice.host import MqttTransport

    t = MqttTransport("127.0.0.1", port, client_id="fake-bridge")
    await t.connect()
    t._client.publish(topic, payload, 1, True).wait_for_publish(5)
    await t.close()


async def test_a_running_bridge_passes(hass, broker, socket_enabled, fast):
    await _retain(broker, "rb-up/bridge/config", json.dumps({"mqtt_root_topic": "rb-up"}))
    assert await config_flow._probe_bridge(hass, _data(broker, "rb-up")) is None


async def test_no_bridge_on_the_root_is_bridge_not_found(hass, broker, socket_enabled, fast):
    assert await config_flow._probe_bridge(hass, _data(broker, "nobody-here")) == "bridge_not_found"


async def test_a_cleared_config_is_a_bridge_that_went_away(hass, broker, socket_enabled, fast):
    await _retain(broker, "rb-gone/bridge/config", json.dumps({}))
    await _retain(broker, "rb-gone/bridge/config", "")
    assert await config_flow._probe_bridge(hass, _data(broker, "rb-gone")) == "bridge_not_found"


async def test_an_unreachable_broker_is_cannot_connect(hass, socket_enabled, fast):
    from conftest import _free_port

    assert await config_flow._probe_bridge(hass, _data(_free_port())) == "cannot_connect"
