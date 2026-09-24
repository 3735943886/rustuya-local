"""The discovery publisher on in-memory brokers: configs follow the descriptors, relays and mirrors carry what Home
Assistant's MQTT platforms need, and stale configs are swept. What the configs mean to Home Assistant itself is
tests/ha/test_discovery_parity.py."""
import json

import pytest
from devices import curtain, fan_levels, lamp
from rustuya_local.discovery.publisher import DiscoveryPublisher, expected_configs
from rustuya_local.discovery.render import Options
from tuya2ildevice import descriptor_of
from tuya2ildevice.host import InProcessTransport


async def announce(bus, record):
    desc = dict(descriptor_of(record), source="tuya")
    await bus.publish(f"il/{desc['id']}", json.dumps(desc), 1, True)
    return desc


async def settle(pub, bus):
    for _ in range(3):
        await bus.settle()
        await pub.drain()


def configs(bus, node="ildevice"):
    return {t: json.loads(m.payload) for t, m in bus.retained.items() if f"/{node}/" in t and t.endswith("/config")}


@pytest.fixture
async def running():
    bus = InProcessTransport()
    pub = DiscoveryPublisher(bus)
    await pub.start()
    yield bus, pub
    await pub.stop()


async def test_configs_follow_descriptors(running):
    bus, pub = running
    await announce(bus, lamp("lamp1"))
    await settle(pub, bus)
    got = configs(bus)
    assert set(got) == {"homeassistant/light/ildevice/lamp1_light/config"}
    assert got["homeassistant/light/ildevice/lamp1_light/config"]["schema"] == "json"
    assert set(got) == set(expected_configs({t: m.payload for t, m in bus.retained.items()}))

    await bus.publish("il/lamp1", "", 1, True)                         # the device is removed
    await settle(pub, bus)
    assert configs(bus) == {} and "lamp1" not in pub.devices
    assert "ildevice-ha/lamp1/light/state" not in bus.retained         # its mirror goes too


async def test_a_changed_descriptor_clears_what_it_no_longer_has(running):
    bus, pub = running
    desc = await announce(bus, lamp("lamp1"))
    desc["props"]["extra"] = {"type": "number", "label": "Extra"}
    await bus.publish("il/lamp1", json.dumps(desc), 1, True)
    await settle(pub, bus)
    assert "homeassistant/sensor/ildevice/lamp1_extra/config" in configs(bus)
    del desc["props"]["extra"]
    await bus.publish("il/lamp1", json.dumps(desc), 1, True)
    await settle(pub, bus)
    assert set(configs(bus)) == {"homeassistant/light/ildevice/lamp1_light/config"}


async def test_the_light_mirror_and_relay(running):
    bus, pub = running
    await announce(bus, lamp("lamp1"))
    for prop, payload in (("switch_led", "true"), ("brightness", "50"), ("color_temperature", "3000")):
        await bus.publish(f"il/lamp1/{prop}", payload, 1, True)
    await settle(pub, bus)
    assert json.loads(bus.retained["ildevice-ha/lamp1/light/state"].payload) == {
        "state": "ON", "brightness": 50, "color_mode": "color_temp", "color_temp": 3000}

    seen = []
    await bus.subscribe("il/lamp1/+/set", lambda m: seen.append((m.topic, m.payload)))
    await bus.publish("ildevice-ha/lamp1/light/set", json.dumps({"state": "ON", "brightness": 20}), 1, False)
    await settle(pub, bus)
    assert seen == [("il/lamp1/switch_led/set", "true"), ("il/lamp1/brightness/set", "20")]


async def test_a_retained_relay_command_replayed_on_subscribe_is_ignored():
    bus = InProcessTransport()
    await announce(bus, lamp("lamp1"))
    await bus.publish("ildevice-ha/lamp1/light/set", json.dumps({"state": "OFF"}), 1, True)      # an old command
    seen = []
    await bus.subscribe("il/lamp1/+/set", lambda m: seen.append(m.topic))
    pub = DiscoveryPublisher(bus)
    await pub.start()
    await settle(pub, bus)
    assert seen == []
    await pub.stop()


async def test_cover_and_fan_relays(running):
    bus, pub = running
    await announce(bus, curtain("cur1"))
    await announce(bus, fan_levels("fan1"))
    await settle(pub, bus)
    seen = []
    await bus.subscribe("il/+/+/set", lambda m: seen.append((m.topic, m.payload)))
    await bus.publish("ildevice-ha/cur1/cover/command", "STOP", 1, False)
    await bus.publish("ildevice-ha/fan1/fan/percentage", "100", 1, False)
    await bus.publish("ildevice-ha/fan1/fan/percentage", "0", 1, False)
    await settle(pub, bus)
    assert seen[0] == ("il/cur1/stop/set", "")
    assert seen[1:] == [("il/fan1/speed/set", "100"), ("il/fan1/prop/set", "false")]      # 0 turns it off


async def test_sweep_clears_owned_configs_no_device_produces():
    bus = InProcessTransport()
    await bus.publish("homeassistant/switch/ildevice/gone_switch/config", json.dumps({"name": "x"}), 1, True)
    await bus.publish("homeassistant/switch/other/keep/config", json.dumps({"name": "y"}), 1, True)
    pub = DiscoveryPublisher(bus)
    await pub.start()
    await announce(bus, lamp("lamp1"))
    await settle(pub, bus)
    assert pub.sweep() == ["homeassistant/switch/ildevice/gone_switch/config"]
    await settle(pub, bus)
    assert "homeassistant/switch/ildevice/gone_switch/config" not in bus.retained
    assert "homeassistant/switch/other/keep/config" in bus.retained           # not ours
    assert "homeassistant/light/ildevice/lamp1_light/config" in bus.retained
    assert sorted(pub.clear_all()) == ["homeassistant/light/ildevice/lamp1_light/config"]
    await settle(pub, bus)
    assert configs(bus) == {}
    await pub.stop()


async def test_a_separate_home_assistant_broker_and_node_id():
    il, ha = InProcessTransport(), InProcessTransport()
    pub = DiscoveryPublisher(il, ha, Options(node_id="house", discovery_prefix="hass"))
    await pub.start()
    await announce(il, lamp("lamp1"))
    await settle(pub, il)
    await ha.settle()
    assert "hass/light/house/lamp1_light/config" in ha.retained and not configs(il, "house")
    await pub.stop()
