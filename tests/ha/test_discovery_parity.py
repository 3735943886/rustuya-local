"""Discovery parity: every Home Assistant core tuya fixture goes through tuya2ildevice to an IL device, and that one
device is then shown twice in the same (test) Home Assistant — once by the il-ha integration from its descriptor, once
by Home Assistant's own MQTT integration from the discovery configs `rustuya_local.discovery` renders — with the same
IL values on the same topics. Each il-ha entity's MQTT twin must have the same state and attributes; a service call on
each must end in the same IL writes (the MQTT twin's through the relay where it has one)."""

from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "e2e"))
IL_HA = HERE.parents[3] / "il-ha"                  # the sibling checkout, for custom_components/ildevice
sys.path.insert(0, str(IL_HA))

from devices import GOLDEN, fixture_record  # noqa: E402
from ildevice.core import parse_descriptor  # noqa: E402
from rustuya_local.discovery.render import Options, render  # noqa: E402
from tuya2ildevice import Connected, Message, TuyaDriver, Value  # noqa: E402
from tuya2ildevice.mqtt import encode_value  # noqa: E402

pytestmark = pytest.mark.skipif(not (IL_HA / "custom_components/ildevice").is_dir() or not GOLDEN.is_dir(),
                                reason="needs the sibling il-ha and tuya2ildevice checkouts")

SOURCE = "tuya"


@pytest.fixture
def expected_lingering_timers() -> bool:
    return True


@pytest.fixture(autouse=True)
def _il_ha_integration():
    """Make custom_components/ildevice loadable next to this repository's own integration, whichever
    `custom_components` package the test harness imported first."""
    import custom_components
    path = str(IL_HA / "custom_components")
    if path not in list(custom_components.__path__):
        custom_components.__path__.append(path)


def _codes():
    sys.path.insert(0, str(GOLDEN))
    import fixtures
    return fixtures.all_codes()


CODES: dict[str, str] = {}      # device id -> fixture code


def _device(code: str, n: int):
    dev_id = f"par{n:03d}"
    CODES[dev_id] = code
    rec, dps = fixture_record(code, dev_id)
    drv = TuyaDriver(rec, allow_hazardous=True)
    drv.handle(0, Connected())
    outs = drv.handle(1, Message("state", dps))
    values = {o.prop: encode_value(o.value) for o in outs if isinstance(o, Value)}
    desc = dict(drv.descriptor, source=SOURCE)
    return dev_id, desc, values


def _norm(v):
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


# attributes that say how each integration is built, not what the device is
IGNORED = {"friendly_name"}


def _known(domain, key, a, b) -> bool:
    """Differences Home Assistant's MQTT platforms make whatever the config says."""
    if key == "preset_modes" and a is None and b == []:
        return True                                  # an MQTT fan always lists its preset modes, if only none
    if domain == "climate" and key == "supported_features" and b == a | 384:
        return True                                  # an MQTT climate always offers turn on / turn off
    return False


# Known upstream findings, not discovery differences: tuya2ildevice turns bzyd_45idzfufidgee7ir's out-of-range
# colour_data into IL values outside the descriptor (brightness 393 with max 100, colour "#-2ec-2e6ff"); il-ha shows
# brightness 1002, Home Assistant's MQTT light clamps it to 255.
UPSTREAM = {("bzyd_45idzfufidgee7ir", "light", "brightness")}


def _same_state(domain, a, b) -> bool:
    if a == b:
        return True
    if domain == "select" and a == "none" and b == "unknown":
        return True                                  # an MQTT select reads an option named `none` as no option
    try:
        return float(a) == float(b)                  # an MQTT number shows -30 as -30.0
    except ValueError:
        return False


async def _setup(hass, devices):
    entry = MockConfigEntry(domain="ildevice", options={"il_prefix": "il", "auto_add": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    routes, mirrors = {}, []
    for dev_id, desc, values in devices:
        async_fire_mqtt_message(hass, f"il/{dev_id}", json.dumps(desc), retain=True)
        rendered = render(parse_descriptor(desc), Options())
        routes.update(rendered.routes)
        mirrors += rendered.mirrors
        for topic, cfg in rendered.configs.items():
            async_fire_mqtt_message(hass, topic, json.dumps(cfg), retain=True)
    await hass.async_block_till_done()
    # the mocked broker does not replay retained messages to a late subscriber: everything read goes after the configs
    async_fire_mqtt_message(hass, f"il/_producer/{SOURCE}", "online", retain=True)
    state: dict[str, str] = {}
    for dev_id, desc, values in devices:
        for prop, payload in values.items():
            state[f"il/{dev_id}/{prop}"] = payload
            async_fire_mqtt_message(hass, f"il/{dev_id}/{prop}", payload, retain=True)
    for m in mirrors:                                   # what the publisher would republish from those values
        async_fire_mqtt_message(hass, m.topic, m.payload(state), retain=True)
    await hass.async_block_till_done()
    return routes, state


def _twins(hass):
    reg = er.async_get(hass)
    ours = {e.unique_id: e for e in reg.entities.values() if e.platform == "ildevice"}
    mqtt = {e.unique_id: e for e in reg.entities.values() if e.platform == "mqtt"}
    return ours, mqtt


async def test_every_fixture_looks_the_same_through_discovery(hass, mqtt_mock):
    devices = [_device(code, n) for n, code in enumerate(_codes())]
    await _setup(hass, devices)
    ours, mqtt = _twins(hass)
    assert len(ours) > 1000, "too few entities for the comparison to mean anything"
    missing = sorted(u for u in ours if f"ildevice_{u}" not in mqtt)
    extra = sorted(u for u in mqtt if u.removeprefix("ildevice_") not in ours)
    assert not missing and not extra, (missing[:20], extra[:20])

    diffs = []
    for uid, e in ours.items():
        twin = mqtt[f"ildevice_{uid}"]
        assert e.domain == twin.domain, (uid, e.domain, twin.domain)
        if e.entity_category != twin.entity_category:
            diffs.append((uid, "entity_category", e.entity_category, twin.entity_category))
        a, b = hass.states.get(e.entity_id), hass.states.get(twin.entity_id)
        if not _same_state(e.domain, a.state, b.state):
            diffs.append((uid, "state", a.state, b.state))
        for key in (set(a.attributes) | set(b.attributes)) - IGNORED:
            va, vb = _norm(a.attributes.get(key)), _norm(b.attributes.get(key))
            if va != vb and not _known(e.domain, str(key), va, vb):
                dev_id, _, ekey = uid.partition("-")
                if (CODES.get(dev_id), ekey, str(key)) not in UPSTREAM:
                    diffs.append((uid, str(key), va, vb))
    assert not diffs, "\n".join(map(str, diffs[:60])) + f"\n... {len(diffs)} differences"


def _cases(state) -> list[tuple[str, dict]]:
    """Service calls to try on an entity of this state's domain (only the ones it supports)."""
    domain, attrs = state.domain, state.attributes
    feats = int(attrs.get("supported_features") or 0)
    if domain in ("switch", "siren", "humidifier"):
        out = [("turn_on", {}), ("turn_off", {})]
        if domain == "humidifier":
            out += [("set_humidity", {"humidity": attrs.get("min_humidity", 30) + 1})]
            out += [("set_mode", {"mode": m}) for m in (attrs.get("available_modes") or [])[:2]]
        return out
    if domain == "number":
        lo, hi = attrs.get("min", 0), attrs.get("max", 100)
        return [("set_value", {"value": lo}), ("set_value", {"value": hi})]
    if domain == "select":
        return [("select_option", {"option": o}) for o in attrs.get("options", [])[:2]]
    if domain == "text":
        return [("set_value", {"value": "abc"})]
    if domain == "button":
        return [("press", {})]
    if domain == "light":
        out = [("turn_off", {}), ("turn_on", {}), ("turn_on", {"brightness": 128})]
        modes = attrs.get("supported_color_modes") or []
        if "hs" in modes:
            out.append(("turn_on", {"hs_color": [120, 50]}))
        if "color_temp" in modes:
            out.append(("turn_on", {"color_temp_kelvin": attrs.get("min_color_temp_kelvin", 2700)}))
        return out
    if domain == "cover":
        out = [(svc, {}) for bit, svc in ((1, "open_cover"), (2, "close_cover"), (8, "stop_cover")) if feats & bit]
        if feats & 4:
            out.append(("set_cover_position", {"position": 40}))
        if feats & 128:
            out.append(("set_cover_tilt_position", {"tilt_position": 30}))
        return out
    if domain == "climate":
        out = [("set_hvac_mode", {"hvac_mode": m}) for m in attrs.get("hvac_modes", [])]
        if feats & 1:
            out.append(("set_temperature", {"temperature": attrs.get("min_temp", 16) + 1}))
        out += [("set_fan_mode", {"fan_mode": m}) for m in (attrs.get("fan_modes") or [])[:2]]
        if attrs.get("swing_modes"):
            out += [("set_swing_mode", {"swing_mode": "on"}), ("set_swing_mode", {"swing_mode": "off"})]
        if attrs.get("swing_horizontal_modes"):
            out += [("set_swing_horizontal_mode", {"swing_horizontal_mode": "on"})]
        return out
    if domain == "fan":
        out = [("turn_on", {}), ("turn_off", {})]
        if feats & 1:
            out += [("set_percentage", {"percentage": 50}), ("set_percentage", {"percentage": 100}),
                    ("set_percentage", {"percentage": 0})]
        if feats & 2:
            out.append(("oscillate", {"oscillating": True}))
        if feats & 4:
            out.append(("set_direction", {"direction": "reverse"}))
        out += [("set_preset_mode", {"preset_mode": m}) for m in (attrs.get("preset_modes") or [])[:2]]
        return out
    if domain == "lock":
        return [("lock", {}), ("unlock", {})] + ([("open", {})] if feats & 1 else [])
    if domain == "valve":
        return [("open_valve", {}), ("close_valve", {})]
    if domain == "alarm_control_panel":
        return [("alarm_disarm", {})] + [(f"alarm_{m}", {}) for bit, m in ((1, "arm_home"), (2, "arm_away"),
                                                                          (4, "arm_night")) if feats & bit]
    if domain == "vacuum":
        out = [(svc, {}) for bit, svc in ((8192, "start"), (4, "pause"), (16, "return_to_base"), (512, "locate"))
               if feats & bit]
        out += [("set_fan_speed", {"fan_speed": s}) for s in (attrs.get("fan_speed_list") or [])[:2]]
        return out
    return []


async def _published(hass, mqtt_mock, domain, service, data, entity_id, routes, state=None):
    """The IL writes one service call ends in: what was published, with relay messages replaced by their writes."""
    mqtt_mock.async_publish.reset_mock()
    try:
        await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)
    except Exception as e:  # noqa: BLE001 - a refused call is compared like any other outcome
        return ("refused", type(e).__name__)
    out = []
    for c in mqtt_mock.async_publish.call_args_list:
        topic, payload = c.args[0], c.args[1]
        if topic in routes:
            out += [(w.topic, w.payload) for w in routes[topic].writes(payload, state)]
        else:
            out.append((topic, payload))
    return out


async def test_every_command_writes_the_same_il_values_through_discovery(hass, mqtt_mock):
    devices = [_device(code, n) for n, code in enumerate(_codes())]
    routes, il_values = await _setup(hass, devices)
    ours, mqtt = _twins(hass)
    diffs, calls = [], 0
    for uid, e in ours.items():
        twin = mqtt[f"ildevice_{uid}"]
        state = hass.states.get(e.entity_id)
        if state.state == "unavailable":
            continue
        for service, data in _cases(state):
            a = await _published(hass, mqtt_mock, e.domain, service, data, e.entity_id, routes)
            b = await _published(hass, mqtt_mock, e.domain, service, data, twin.entity_id, routes, il_values)
            calls += 1
            if a != b:
                diffs.append((uid, service, data, a, b))
    assert calls > 1000, calls
    assert not diffs, "\n".join(map(str, diffs[:60])) + f"\n... {len(diffs)} differences in {calls} calls"
