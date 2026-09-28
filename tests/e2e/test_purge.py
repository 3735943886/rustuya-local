"""Retiring a producer for good: `purge_il` clears the retained IL topics of one source (found on the broker, not in a
Hub), `purge_retained` a stopped bridge's leftovers, and `rustuya-local purge` runs the former for the daemon."""

import asyncio
import json
import threading
import time

import pytest
from tuya2ildevice.host import InProcessTransport

from rustuya_local.service import AnotherProducer, purge_il, purge_retained


def _desc(device_id: str, source: str) -> str:
    return json.dumps({"il": 1, "id": device_id, "source": source, "props": {}})


async def _retained(t: InProcessTransport, topic_filter: str) -> dict[str, str]:
    seen: dict[str, str] = {}

    def on(msg):
        if msg.retain:
            p = msg.payload
            seen[msg.topic] = p.decode() if isinstance(p, bytes) else p

    unsub = await t.subscribe(topic_filter, on)
    await asyncio.sleep(0.05)
    unsub()
    return {k: v for k, v in seen.items() if v}


async def test_purge_il_clears_one_source_and_leaves_the_others():
    t = InProcessTransport()
    for topic, payload in {"il/a": _desc("a", "tuya"), "il/a/power": "true", "il/a/brightness": "40",
                           "il/b": _desc("b", "tuya"), "il/z": _desc("z", "zigbee"), "il/z/power": "false",
                           "il/_producer/tuya": "offline", "il/_producer/zigbee": "online"}.items():
        await t.publish(topic, payload, 1, True)

    ids = await purge_il(t, "il", "tuya", settle=0.05)

    assert ids == ["a", "b"]
    assert await _retained(t, "il/#") == {"il/z": _desc("z", "zigbee"), "il/z/power": "false",
                                           "il/_producer/zigbee": "online"}


async def test_purge_il_refuses_while_the_producer_answers():
    from tuya2ildevice import Hub, IlTopics
    from tuya2ildevice.host import Runner

    t = InProcessTransport()
    runner = Runner(Hub([], il=IlTopics("il", "tuya")), t)
    await runner.start()
    await runner.drain()
    await t.publish("il/a", _desc("a", "tuya"), 1, True)
    try:
        with pytest.raises(AnotherProducer):
            await purge_il(t, "il", "tuya", settle=0.05)
        assert "il/a" in await _retained(t, "il/#")
    finally:
        await runner.stop()


async def test_purge_retained_clears_a_stopped_bridges_topics_only():
    t = InProcessTransport()
    for topic in ("rb/error/dev1", "rb/event/state/dev1", "other/error/dev1"):
        await t.publish(topic, '{"errorCode":905}', 1, True)
    assert await purge_retained(t, "rb", settle=0.05) == ["rb/error/dev1", "rb/event/state/dev1"]
    assert await _retained(t, "#") == {"other/error/dev1": '{"errorCode":905}'}


async def test_purge_retained_refuses_while_the_bridge_runs():
    t = InProcessTransport()
    await t.publish("rb/bridge/config", "{}", 1, True)
    await t.publish("rb/error/dev1", '{"errorCode":905}', 1, True)
    with pytest.raises(RuntimeError):
        await purge_retained(t, "rb", settle=0.05)
    assert "rb/error/dev1" in await _retained(t, "rb/#")


def test_the_purge_command_clears_the_daemons_devices(broker, tmp_path, capsys):
    import paho.mqtt.client as mqtt

    from rustuya_local.cli import main

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="purge-seed")
    c.connect("127.0.0.1", broker)
    c.loop_start()
    try:
        for topic, payload in (("ilp/a", _desc("a", "tuya")), ("ilp/a/power", "true"),
                               ("ilp/_producer/tuya", "offline")):
            c.publish(topic, payload, 1, True).wait_for_publish(5)
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"il": {"host": "127.0.0.1", "port": broker, "prefix": "ilp"}, "devices": []}))
        # in a thread of its own: `asyncio.run` on this one would take the event loop the later tests expect
        worker = threading.Thread(target=main, args=(["purge", "--config", str(config)],))
        worker.start()
        worker.join(30)
        assert "took 1 device(s)" in capsys.readouterr().out

        seen: dict[str, bytes] = {}
        late = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="purge-late")
        late.on_message = lambda _c, _u, m: seen.__setitem__(m.topic, m.payload)
        late.connect("127.0.0.1", broker)
        late.loop_start()
        late.subscribe("ilp/#", 1)
        time.sleep(0.5)
        late.loop_stop()
        late.disconnect()
        assert seen == {}
    finally:
        c.loop_stop()
        c.disconnect()
