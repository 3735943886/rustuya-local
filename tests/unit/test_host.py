"""The host-owned Hub around tuya2ildevice drivers (`rustuya_local.host`): Runner on the IL transport, the device file, the rule
directory, and paho against a real broker (mosquitto, skipped when it is not installed). The bridge side is simulated
with `runner.on_bridge_message`; `BridgeCommand` outputs are captured via the injected `on_bridge_command` callback."""
import asyncio
import json
import shutil
import socket
import subprocess
import time

import pytest
from tuya2ildevice import Connected, Message

from rustuya_local.host import (
    BridgeCommand,
    DeviceWatcher,
    Hub,
    IlTopics,
    InProcessTransport,
    OverrideWatcher,
    Runner,
    load_overrides,
    parse_devices,
    producer_running,
)
from rustuya_local.host.memory import matches
from tests.unit.tuya_samples import light

try:
    import pytest_socket  # noqa: F401 -- optional fixture availability
except ImportError:
    pass
else:
    @pytest.fixture(autouse=True)
    def _real_sockets(socket_enabled):
        """These host transport tests intentionally use a real broker, even with HA's socket guard installed."""



def lamp(dev_id):
    d = light()
    d["id"] = dev_id
    return d


# ---- transports ------------------------------------------------------------------------------------------------------
def test_topic_filters_are_mqtt_filters():
    assert matches("il/+", "il/d1") and not matches("il/+", "il/d1/x") and matches("il/#", "il/d1/x")
    assert not matches("il/+/x", "il/d1") and matches("a/b", "a/b") and not matches("a/b", "a/c")


async def test_retained_messages_replay_to_a_late_subscriber_and_empty_clears():
    bus, seen = InProcessTransport(), []
    await bus.publish("a/b", "1", 1, True)
    await bus.subscribe("a/+", lambda m: seen.append((m.topic, m.payload, m.retain)))
    await bus.publish("a/c", "2", 1, False)
    assert seen == [("a/b", "1", True), ("a/c", "2", False)]
    await bus.publish("a/b", "", 1, True)
    assert "a/b" not in bus.retained


# ---- the runner ------------------------------------------------------------------------------------------------------
@pytest.fixture
async def running():
    il = InProcessTransport()
    hub = Hub([lamp("lamp1")], il=IlTopics("il", "tuya"))
    commands: list[BridgeCommand] = []
    runner = Runner(hub, il, on_bridge_command=commands.append)
    await runner.start()
    await runner.drain()
    yield il, runner, hub, commands
    await runner.stop()


async def test_start_publishes_presence_and_descriptors_and_values_flow_both_ways(running):
    il, runner, _, commands = running
    assert il.retained["il/_producer/tuya"].payload == "online"
    assert json.loads(il.retained["il/lamp1"].payload)["kind"] == "light"

    runner.on_bridge_message("lamp1", Connected())
    runner.on_bridge_message("lamp1", Message("state", {"20": True, "22": 1000, "23": 0}))
    await runner.drain()
    assert il.retained["il/lamp1/brightness"].payload == "100"
    assert BridgeCommand("lamp1", "get") in commands

    commands.clear()
    await il.publish("il/lamp1/brightness/set", "50", 1)
    await runner.drain()
    assert BridgeCommand("lamp1", "set", {"20": True, "22": 507}) in commands

    await il.publish("il/lamp1/brightness/set", "500", 1)
    await runner.drain()
    assert json.loads(il.published[-1][1])["code"] == "out_of_range" and il.published[-1][0] == "il/lamp1/reject"


async def test_stop_is_idempotent_and_publishes_offline(running):
    il, runner, _, _ = running
    await runner.stop()
    await runner.stop()
    assert il.published[-1][:2] == ("il/_producer/tuya", "offline")


async def test_a_restart_can_leave_the_presence_online(running):
    il, runner, _, _ = running
    await runner.stop(offline=False)
    assert il.retained["il/_producer/tuya"].payload == "online"
    assert not [p for p in il.published if p[0] == "il/_producer/tuya" and p[1] == "offline"]


async def test_sync_adds_changes_and_removes_and_clears_before_removing(running):
    il, runner, hub, _ = running
    runner.set_device(lamp("gone"))
    runner.set_device(lamp("edit"))
    await runner.drain()
    edited = lamp("edit")
    edited["name"] = "Renamed"
    done = runner.sync_devices([lamp("lamp1"), edited, lamp("new")])
    assert done == {"added": ["new"], "changed": ["edit"], "removed": ["gone"], "failed": []}
    await runner.drain()
    assert set(hub.records) == {"lamp1", "edit", "new"} and not [t for t in il.retained if t.startswith("il/gone")]
    assert json.loads(il.retained["il/edit"].payload)["label"] == "Renamed"
    bad = lamp("bad")
    bad["id"] = "_reserved"
    assert runner.sync_devices([lamp("lamp1"), bad])["failed"] == ["_reserved"]


async def test_a_device_an_override_refused_is_driven_once_the_overrides_are_fixed(running):
    il, runner, hub, _ = running
    runner.reload({"late": {"bogus": True}})                          # no such device yet: nothing to refuse
    assert runner.sync_devices([lamp("lamp1"), lamp("late")])["failed"] == ["late"]
    runner.reload({"late": {"also_bogus": True}})                     # still refused
    await runner.drain()
    assert "late" not in hub.records and "il/late" not in il.retained
    runner.reload({"late": {"device": {"label": "Late"}}})            # fixed: driven now, without a device-list change
    await runner.drain()
    assert "late" in hub.records and json.loads(il.retained["il/late"].payload)["label"] == "Late"


# ---- the device file --------------------------------------------------------------------------------------------------
def test_a_list_or_a_dict_of_records_and_the_ones_that_cannot_be_driven():
    records = [lamp("a"), {"id": "nocat", "name": "x"}, {"name": "noid"}, "junk"]
    for raw in (json.dumps(records), json.dumps({str(i): r for i, r in enumerate(records)})):
        usable, skipped = parse_devices(raw)
        assert [r["id"] for r in usable] == ["a"] and skipped == ["nocat"]
    with pytest.raises(ValueError):
        parse_devices('"nope"')


async def test_the_watcher_follows_the_file_and_keeps_devices_when_it_cannot_be_read(running, tmp_path):
    _, runner, hub, _ = running
    path = tmp_path / "tuyadevices.json"
    path.write_text(json.dumps([lamp("lamp1")]))
    watcher = DeviceWatcher(path, runner, interval=0.01)
    assert watcher.check() and set(hub.records) == {"lamp1"}
    assert not watcher.check()
    path.write_text("{ half written")
    assert watcher.check() and set(hub.records) == {"lamp1"}
    path.write_text(json.dumps([lamp("lamp1"), lamp("late")]))
    assert watcher.check() and set(hub.records) == {"lamp1", "late"}


LIT = {"props": {"lit": {"type": "text"}}, "word": "on",
       "update": [{"emit": "lit", "value": {"op": "if", "condition": {"op": "get", "path": "codes.switch_led"},
                                             "then": {"op": "get", "path": "config.word"}, "else": "off"}}]}


def test_a_rule_directory_loads_in_filename_order_and_reports_bad_files(tmp_path):
    (tmp_path / "10_base.json").write_text(json.dumps({"overrides": {"p": {"device": {"model": "A"}}}}))
    (tmp_path / "99_local.json").write_text(json.dumps({"overrides": {"p": {"device": {"model": "B"}}}}))
    (tmp_path / "50_broken.json").write_text("{broken")
    (tmp_path / "60_bare.json").write_text(json.dumps({"p": {}}))
    (tmp_path / "lit.py").write_text("raise RuntimeError('never imported')\n")
    loaded = load_overrides(tmp_path)
    assert loaded.overrides == {"p": {"device": {"model": "B"}}}
    assert [w.split(":")[0] for w in loaded.warnings] == ["50_broken.json", "60_bare.json"]
    assert load_overrides(tmp_path / "missing").overrides == {}


async def test_the_override_watcher_reloads_the_hub(running, tmp_path):
    il, runner, _, _ = running
    watcher = OverrideWatcher(tmp_path, runner, interval=0.01, base={"lamp1": {"device": {"label": "Desk"}}})
    (tmp_path / "a.json").write_text(json.dumps({"overrides": {"p": {"converters": {"declarative": {**LIT, "word": "bright"}}}}}))
    assert watcher.check()
    await runner.drain()
    desc = json.loads(il.retained["il/lamp1"].payload)
    assert "lit" in desc["props"] and desc["label"] == "Desk"
    runner.on_bridge_message("lamp1", Connected())
    runner.on_bridge_message("lamp1", Message("state", {"20": True}))
    await runner.drain()
    assert il.retained["il/lamp1/lit"].payload == "bright"
    assert not watcher.check()
    (tmp_path / "a.json").write_text(json.dumps({"overrides": {"p": {"converters": {"nope": {}}}}}))      # refused: the old one stays
    assert watcher.check()
    await runner.drain()
    assert "lit" in json.loads(il.retained["il/lamp1"].payload)["props"]
    (tmp_path / "a.json").unlink()
    assert watcher.check()
    await runner.drain()
    assert "lit" not in json.loads(il.retained["il/lamp1"].payload)["props"]


# ---- paho against a real broker --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def broker():
    # Module-scoped setup happens before the per-test socket_enabled fixture.
    try:
        import pytest_socket
    except ImportError:
        pass
    else:
        pytest_socket.enable_socket()
    exe = shutil.which("mosquitto")
    pytest.importorskip("paho.mqtt.client")
    if exe is None:
        pytest.skip("mosquitto is not installed")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([exe, "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield port
    proc.terminate()
    proc.wait(5)


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "condition not reached in time"
        await asyncio.sleep(0.02)


async def test_paho_transport_runs_the_runner_survives_a_dropped_connection_and_fires_the_last_will(broker):
    from rustuya_local.host import MqttTransport
    hub = Hub([lamp("lamp1")], il=IlTopics("il", "tuya"))
    will = hub.presence(False)
    il_side = MqttTransport("127.0.0.1", broker, client_id="t-il", will=(will.topic, will.payload, will.qos, will.retain))
    sim = MqttTransport("127.0.0.1", broker, client_id="t-sim")
    for t in (il_side, sim):
        await t.connect()
    seen: dict[str, str] = {}
    commands: list[BridgeCommand] = []
    await sim.subscribe("il/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    runner = Runner(hub, il_side, on_bridge_command=commands.append)
    await runner.start()
    try:
        await until(lambda: seen.get("il/_producer/tuya") == "online" and "il/lamp1" in seen)
        runner.on_bridge_message("lamp1", Connected())
        await until(lambda: BridgeCommand("lamp1", "get") in commands)
        runner.on_bridge_message("lamp1", Message("state", {"20": True, "22": 1000, "23": 0}))
        await until(lambda: seen.get("il/lamp1/brightness") == "100")
        await sim.publish("il/lamp1/brightness/set", "50", 1)
        await until(lambda: any(c.action == "set" for c in commands))
        assert [c for c in commands if c.action == "set"] == [BridgeCommand("lamp1", "set", {"20": True, "22": 507})]

        seen.pop("il/_producer/tuya")
        il_side._client._sock.close()                       # an unclean drop: the broker publishes the Last Will
        await until(lambda: seen.get("il/_producer/tuya") == "offline")
        await until(lambda: seen.get("il/_producer/tuya") == "online")      # paho reconnects; the runner republishes
    finally:
        await runner.stop()
        await il_side.close()
        for topic in list(seen):
            await sim.publish(topic, "", 1, True)
        await asyncio.sleep(0.1)
        await sim.close()


async def test_a_failed_connect_stops_its_own_thread():
    """A timed-out connect() must not leave the paho loop thread running (the caller may just drop the object)."""
    import threading

    from rustuya_local.host import MqttTransport

    before = {t.name for t in threading.enumerate()}
    t = MqttTransport("127.0.0.1", 1, client_id="dead-end")   # nothing listens on port 1
    with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)):   # asyncio.TimeoutError != TimeoutError before 3.11
        await t.connect(timeout=0.3)
    after = {t.name for t in threading.enumerate()}
    assert not (after - before), f"leaked thread(s): {after - before}"


async def test_a_producer_stopping_after_its_replacement_started_does_not_leave_presence_offline():
    il = InProcessTransport()
    old = Runner(Hub([lamp("lamp1")], il=IlTopics("il", "tuya")), il)
    await old.start()
    await old.drain()
    new = Runner(Hub([lamp("lamp1")], il=IlTopics("il", "tuya")), il)
    await new.start()                                        # the replacement starts first...
    await new.drain()
    await old.stop()                                         # ...then the old one goes, publishing `offline`
    for _ in range(3):
        await il.settle()
        await new.drain()
    assert il.retained["il/_producer/tuya"].payload == "online"
    await new.stop()
    await il.settle()
    assert il.retained["il/_producer/tuya"].payload == "offline"          # its own `offline` is not answered


async def test_producer_running_tells_a_live_producer_from_a_stale_presence():
    il = InProcessTransport()
    assert await producer_running(il, "il/_producer/tuya") is False                  # nobody ever ran
    runner = Runner(Hub([lamp("lamp1")], il=IlTopics("il", "tuya")), il)
    await runner.start()
    await runner.drain()
    assert await producer_running(il, "il/_producer/tuya") is True                   # it answers the probe
    assert await producer_running(il, "il/_producer/other") is False                 # another source is not it
    assert "il/_producer/tuya/alive" not in il.retained and "il/_producer/tuya/probe" not in il.retained
    await runner.stop()
    await il.settle()
    assert await producer_running(il, "il/_producer/tuya") is False                  # its `offline`
    await il.publish("il/_producer/tuya", "online", 1, True)                         # a Last Will that never came
    assert await producer_running(il, "il/_producer/tuya", timeout=0.2) is None


async def test_the_handover_on_a_real_broker(broker):
    """What a host sees: each producer on its own connection with its Last Will, the old one stopping after the new."""
    # paho is optional: the broker fixture skips without it
    from rustuya_local.host import MqttTransport

    async def producer(name):
        hub = Hub([lamp("lamp1")], il=IlTopics("il", "tuya"))
        will = hub.presence(False)
        t = MqttTransport("127.0.0.1", broker, client_id=name, will=(will.topic, will.payload, will.qos, will.retain))
        await t.connect()
        runner = Runner(hub, t)
        await runner.start()
        await runner.drain()
        return runner, t

    old, old_t = await producer("handover-old")
    probe = MqttTransport("127.0.0.1", broker, client_id="handover-probe")
    await probe.connect()
    assert await producer_running(probe, "il/_producer/tuya") is True
    await probe.close()
    new, new_t = await producer("handover-new")
    await old.stop()
    await old_t.close()
    seen = []
    watch = MqttTransport("127.0.0.1", broker, client_id="handover-watch")
    await watch.connect()
    for _ in range(40):
        seen.clear()
        unsub = await watch.subscribe("il/_producer/tuya", lambda m: seen.append(m.payload))
        await asyncio.sleep(0.1)
        unsub()
        if seen and seen[-1] in ("online", b"online"):
            break
    assert seen and seen[-1] in ("online", b"online")
    await new.stop()
    await new_t.close()
    await watch.publish("il/_producer/tuya", "", 1, True)
    await watch.close()
