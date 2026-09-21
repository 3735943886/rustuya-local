import asyncio
import json
import sys

import pytest

sys.path.insert(0, "tests/e2e")
from devices import lamp
from il_ha.core.memory import InProcessTransport
from tuya2ildevice import Hub

from rustuya_local.bridge import read_bridge_config, topic_templates
from rustuya_local.devices import DeviceWatcher, parse_devices
from rustuya_local.runner import Runner


def test_a_list_or_a_dict_of_records_and_the_ones_that_cannot_be_driven():
    records = [lamp("a"), {"id": "nocat", "name": "x"}, {"name": "noid"}, "junk"]
    for raw in (json.dumps(records), json.dumps({r["id"] if isinstance(r, dict) and "id" in r else str(i): r
                                                 for i, r in enumerate(records)})):
        usable, skipped = parse_devices(raw)
        assert [r["id"] for r in usable] == ["a"] and skipped == ["nocat"]
    with pytest.raises(ValueError):
        parse_devices('"nope"')


def test_the_bridges_own_topic_templates_win_over_the_default_root():
    root, kw = topic_templates({"mqtt_root_topic": "tuya", "mqtt_event_topic": "{root}/ev/{type}/{id}",
                                "mqtt_command_topic": "{root}/cmd"}, "rustuya")
    assert root == "tuya" and kw == {"event": "{root}/ev/{type}/{id}", "command": "{root}/cmd"}
    assert topic_templates({}, "rustuya") == ("rustuya", {})


async def test_the_bridge_config_is_read_once_it_is_published_and_absence_times_out():
    bus = InProcessTransport()
    assert await read_bridge_config(bus, "rustuya", timeout=0.05) is None
    await bus.publish("rustuya/bridge/config", '{"mqtt_root_topic": "rustuya", "version": "0.4"}', 1, True)
    assert (await read_bridge_config(bus, "rustuya", timeout=1))["version"] == "0.4"
    await bus.publish("other/bridge/config", "not json", 1, True)
    assert await read_bridge_config(bus, "other", timeout=0.05) is None          # not JSON: treated as absent


async def test_sync_adds_changes_and_removes_and_the_watcher_follows_the_file(tmp_path):
    bridge, il = InProcessTransport(), InProcessTransport()
    hub = Hub([lamp("keep"), lamp("gone")])
    runner = Runner(hub, bridge, il)
    await runner.start()
    changed = lamp("edit")
    runner.set_device(changed)
    await runner.drain()

    edited = json.loads(json.dumps(changed))
    edited["name"] = "Renamed"
    done = runner.sync_devices([lamp("keep"), edited, lamp("new")])
    assert done == {"added": ["new"], "changed": ["edit"], "removed": ["gone"], "failed": []}
    await runner.drain()
    assert set(hub.records) == {"keep", "edit", "new"} and "il/gone" not in il.retained
    assert json.loads(il.retained["il/edit"].payload)["label"] == "Renamed"
    bad = lamp("bad")
    bad["id"] = "_reserved"
    assert runner.sync_devices([lamp("keep"), bad])["failed"] == ["_reserved"]

    path = tmp_path / "tuyadevices.json"
    path.write_text(json.dumps([lamp("keep")]))
    watcher = DeviceWatcher(path, runner, interval=0.01)
    watcher._stamp = None                                       # as if the file just appeared
    assert watcher.check() and set(hub.records) == {"keep"}
    assert not watcher.check()                                   # unchanged: nothing to do
    path.write_text("{ half written")
    assert watcher.check() and set(hub.records) == {"keep"}      # unreadable: devices stay as they are
    path.write_text(json.dumps([lamp("keep"), lamp("late")]))
    assert watcher.check() and set(hub.records) == {"keep", "late"}
    await runner.stop()
