import json

import pytest
from tuya_interpreter import Connected, Disconnected, Message

from rustuya_local.host import BridgeCommand, Hub, Publish
from tests.unit.tuya_samples import curtain, light


def topics(pubs, side=None):
    return {p.topic: p for p in pubs if isinstance(p, Publish) and (side is None or p.side == side)}


def test_bridge_to_il_and_back():
    hub = Hub([light(), curtain()])
    start = topics(hub.start(), "il")
    assert start["il/_producer/tuya"].payload == "online" and start["il/_producer/tuya"].retain
    assert json.loads(start["il/lamp1/descriptor"].payload)["kind"] == "light" and start["il/lamp1/descriptor"].retain
    assert start["il/lamp1/available"].payload == "false"

    # bridge says connected -> a `get` goes back to the bridge
    out = hub.on_bridge_message(0, "lamp1", Connected())
    assert BridgeCommand("lamp1", "get") in out
    # a retained link state with no state yet asks too: the bridge keeps no snapshot of a device nobody asked
    assert BridgeCommand("lamp1", "get") in hub.on_bridge_message(0, "lamp1", Connected(), retained=True)

    # retained full snapshot, then a live delta
    out = topics(hub.on_bridge_message(1, "lamp1", Message("state", {"20": True, "22": 1000, "23": 0}), retained=True))
    assert out["il/lamp1/switch_led"].payload == "true" and out["il/lamp1/brightness"].payload == "100"
    assert out["il/lamp1/available"].payload == "true"
    out = hub.on_bridge_message(2, "lamp1", Message("active", {"22": 505}))
    assert out == [Publish("il", "il/lamp1/brightness", "50", True, 1)]
    assert hub.on_bridge_message(3, "lamp1", Message("active", {"22": 100}), retained=True) == []   # replayed delta
    # with its state in hand, a retained link state (a resubscribe) asks for nothing; a live one (a reconnect) does
    assert BridgeCommand("lamp1", "get") not in hub.on_bridge_message(3, "lamp1", Connected(), retained=True)
    assert BridgeCommand("lamp1", "get") in hub.on_bridge_message(3, "lamp1", Connected())

    # ildevice-homeassistant writes a value -> a `set` for the bridge; a bad one -> a reject for ildevice-homeassistant
    (cmd,) = hub.on_il(4, "il/lamp1/brightness/set", "50")
    assert cmd == BridgeCommand("lamp1", "set", {"20": True, "22": 507})
    (rej,) = hub.on_il(5, "il/lamp1/brightness/set", "500")
    assert rej.topic == "il/lamp1/reject" and not rej.retain and json.loads(rej.payload)["code"] == "out_of_range"
    assert hub.on_il(6, "il/lamp1/brightness/set", "50", retained=True) == []                 # M-9

    # trigger with an empty payload, and a link drop
    (cmd,) = hub.on_il(7, "il/lamp1/switch_led/set", "off")
    assert cmd.dps == {"20": False}
    out = topics(hub.on_bridge_message(8, "lamp1", Disconnected()))
    assert out["il/lamp1/brightness"].payload == "" and out["il/lamp1/available"].payload == "false"


def test_bad_ids_refused():
    d = light()
    d["id"] = "a/b"
    with pytest.raises(ValueError):
        Hub([d])


def test_a_multi_segment_il_prefix_still_parses_writes():
    """A prefix that itself contains a "/" (e.g. namespacing a producer under "il/tuya") must not break `set` parsing:
    a write on it was silently dropped (parse_set assumed the prefix was one path segment)."""
    from rustuya_local.host import IlTopics

    t = IlTopics("il/tuya")
    assert t.set_subscription() == "il/tuya/+/+/set"
    assert t.parse_set("il/tuya/dev1/power/set") == ("dev1", "power")
    assert t.parse_set("il/dev1/power/set") is None                 # a shorter prefix must not match
    assert t.parse_set("il/tuya/dev1/power") is None                 # not a set topic
    assert t.parse_set("il/tuya/_producer/tuya") is None             # reserved (M-2)

    hub = Hub([light()], il=t)
    hub.on_bridge_message(0, "lamp1", Connected())
    (cmd,) = hub.on_il(0, "il/tuya/lamp1/switch_led/set", "true")
    assert cmd.dps == {"20": True}


def test_reload_overrides_republishes_and_clears_removed_props():
    from tuya_interpreter import OverrideError

    hub = Hub([curtain()])
    hub.start()
    hub.on_bridge_message(0, "cur1", Connected())
    hub.on_bridge_message(1, "cur1", Message("state", {"3": 30}))
    assert hub.reload({}) == []                                                    # nothing changed
    pubs = hub.reload({"cur1": {"props": {"stop": {"hide": True}}, "device": {"label": "Bedroom"}}})
    by = topics(pubs)
    assert by["il/cur1/stop"].payload == "" and by["il/cur1/stop"].retain          # M-11
    assert json.loads(by["il/cur1/descriptor"].payload)["label"] == "Bedroom" and "stop" not in json.loads(by["il/cur1/descriptor"].payload)["props"]
    assert by["il/cur1/position"].payload == "30"                                  # worked out again from the last dps
    assert BridgeCommand("cur1", "get") not in pubs                                # which the host has: nothing to ask
    with pytest.raises(OverrideError):
        hub.reload({"cur1": {"props": {"missing": {"hide": True}}}})
    (cmd,) = hub.on_il(2, "il/cur1/position/set", "70")
    assert cmd.dps                                                                 # still works


def test_timers_surface_as_schedule_and_on_timer():
    from rustuya_local.host import Schedule, Unschedule

    hub = Hub([curtain()], overrides={"cur1": {"cover": {"state_source": "inferred", "settle": 5}}})
    hub.start()
    hub.on_bridge_message(0, "cur1", Connected())
    hub.on_bridge_message(1, "cur1", Message("state", {"3": 50}))
    out = hub.on_bridge_message(2, "cur1", Message("active", {"1": "close"}))
    assert Schedule("cur1", "c0:settle", 5.0) in out and Publish("il", "il/cur1/cover_state", "closing", True, 1) in out
    assert hub.on_timer(3, "cur1", "c0:settle") == [Publish("il", "il/cur1/cover_state", "stopped", True, 1)]
    hub.on_bridge_message(4, "cur1", Message("active", {"1": "open"}))
    assert Unschedule("cur1", "c0:settle") in hub.on_bridge_message(5, "cur1", Disconnected())


def test_a_seeded_device_goes_out_up_with_its_values_and_never_false():
    """A host restarting knows each device's link and retained state: seeded, the device is published as it already
    was (descriptor, `available: true`, its values), with no `available: false` for an il consumer to flap on."""
    hub = Hub([])
    out = hub.set_device(light(), [Connected(), Message("state", {"20": True, "22": 1000, "23": 0})])
    order = [p.topic for p in out if isinstance(p, Publish)]
    assert order[0] == "il/lamp1/descriptor"                                          # R-2: the descriptor first
    pubs = topics(out)
    assert pubs["il/lamp1/available"].payload == "true" and pubs["il/lamp1/brightness"].payload == "100"
    assert [p for p in out if isinstance(p, Publish) and p.topic == "il/lamp1/available"] == [pubs["il/lamp1/available"]]
    assert BridgeCommand("lamp1", "get") not in out                        # the bridge's snapshot is current

    linked_only = Hub([]).set_device(light(), [Connected()])               # no snapshot yet: A-2 says not available
    assert topics(linked_only)["il/lamp1/available"].payload == "false" and BridgeCommand("lamp1", "get") in linked_only
    down = Hub([]).set_device(light(), [Disconnected()])
    assert topics(down)["il/lamp1/available"].payload == "false" and BridgeCommand("lamp1", "get") not in down
    assert next(p.topic for p in down if isinstance(p, Publish)) == "il/lamp1/descriptor"
    # a sub-device has no link state of its own, only its retained snapshot: up with its values, and no `get`
    sub = Hub([]).set_device(light(), [Message("state", {"20": False, "22": 10, "23": 0})])
    assert topics(sub)["il/lamp1/available"].payload == "true" and not any(isinstance(p, BridgeCommand) for p in sub)


def test_set_device_adds_replaces_and_remove_clears_first():
    hub = Hub([light()])
    hub.start()
    cid = curtain()["id"]
    added = topics(hub.set_device(curtain()))                      # runtime add: descriptor out, retained
    assert added[f"il/{cid}/descriptor"].retain and json.loads(added[f"il/{cid}/descriptor"].payload)["kind"] == "cover"
    assert cid in hub.drivers

    # replacing a known device that lost a property clears that property before the new descriptor (M-11)
    small = light()
    del small["function"]["temp_value_v2"]                         # function and status_range are one dict in helpers.light
    small["local_strategy"].pop("23")
    out = hub.set_device(small)
    order = [p.topic for p in out]
    assert order == ["il/lamp1/color_temperature", "il/lamp1/descriptor", "il/lamp1/available"]
    assert out[0].payload == "" and out[0].retain

    # removal: every retained value cleared, then the descriptor last, and the device is gone
    out = hub.remove_device("lamp1")
    assert out[-1].topic == "il/lamp1/descriptor" and out[-1].payload == "" and out[-1].retain
    assert all(p.payload == "" and p.retain for p in out) and len(out) > 2
    assert hub.on_bridge_message(2, "lamp1", Message("state", {"20": True}), retained=True) == [] and hub.remove_device("lamp1") == []

    bad = light()
    bad["id"] = "_x"
    with pytest.raises(ValueError):
        hub.set_device(bad)
    assert "_x" not in hub.drivers
