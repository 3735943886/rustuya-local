"""rustuya-bridge -> Hub (Runner) -> IL -> IL consumer model, all in one process on in-memory brokers, no HA."""
import json

import pytest
from devices import lamp
from il_ha.core import IlModel
from tuya2ildevice.host import InProcessTransport
from tuya2ildevice import BridgeTopics, Hub, IlTopics

from tuya2ildevice.host import Runner


class Recorder:
    def __init__(self):
        self.values = []

    def discovered(self, desc): pass
    def withdrawn(self, device_id): pass
    async def replacing(self, known): pass
    async def ready(self, dev, known): self.values.append(("ready", dev.desc.id))
    async def removed(self, dev): self.values.append(("removed", dev.desc.id))
    def values_changed(self, dev): self.values.append(("values", dict(dev.values)))
    def event(self, dev, prop, kind): pass
    def rejected(self, dev, data): self.values.append(("rejected", data))


class Timers:
    def __call__(self, delay, fn):
        return lambda: None


@pytest.fixture
async def chain():
    bridge, il = InProcessTransport(), InProcessTransport()
    hub = Hub([lamp()], bridge=BridgeTopics("rustuya"), il=IlTopics("il", "tuya"))
    runner = Runner(hub, bridge, il)
    sink = Recorder()
    model = IlModel(il, sink, Timers())
    await model.start()                       # the consumer first, as il-ha would be running already
    await runner.start()
    await runner.drain()
    await il.settle()
    yield bridge, il, runner, model, sink
    await runner.stop()


async def settle(runner, *buses):
    await runner.drain()
    for b in buses:
        await b.settle()


async def test_device_appears_and_values_flow_from_the_bridge(chain):
    bridge, il, runner, model, sink = chain
    assert "lamp1" in model.devices and model.devices["lamp1"].desc.kind == "light"
    assert il.retained["il/_producer/tuya"].payload == "online"
    assert model.devices["lamp1"].online is False                       # nothing heard from the device yet

    await bridge.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 0, True)
    await settle(runner, bridge, il)
    assert bridge.published[-1][:2] == ("rustuya/command", '{"action": "get", "id": "lamp1"}')   # asks for the state

    await bridge.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', 0, True)
    await settle(runner, bridge, il)
    dev = model.devices["lamp1"]
    assert dev.online is True and dev.values["switch_led"] is True and dev.values["brightness"] == 100


async def test_a_consumer_command_reaches_the_bridge_as_dps(chain):
    bridge, il, runner, model, _ = chain
    await bridge.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 0, True)
    await bridge.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', 0, True)
    await settle(runner, bridge, il)
    bridge.published.clear()
    await model.transport.publish("il/lamp1/brightness/set", "50", 1)
    await settle(runner, bridge, il)
    (topic, payload, *_), = [p for p in bridge.published if p[0] == "rustuya/command"]
    assert json.loads(payload) == {"action": "set", "id": "lamp1", "dps": {"20": True, "22": 507}}


async def test_a_refused_command_comes_back_as_a_rejection(chain):
    bridge, il, runner, model, sink = chain
    await bridge.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 0, True)
    await settle(runner, bridge, il)
    await il.publish("il/lamp1/brightness/set", "500", 1)
    await settle(runner, bridge, il)
    assert [v[1]["code"] for v in sink.values if v[0] == "rejected"] == ["out_of_range"]


async def test_devices_can_be_added_and_removed_while_running(chain):
    bridge, il, runner, model, sink = chain
    runner.set_device(lamp("lamp2"))
    await settle(runner, bridge, il)
    assert set(model.devices) == {"lamp1", "lamp2"}
    runner.remove_device("lamp2")
    await settle(runner, bridge, il)
    assert set(model.devices) == {"lamp1"} and "il/lamp2" not in il.retained
    assert not [t for t in il.retained if t.startswith("il/lamp2/")]     # M-11: values cleared before the descriptor


async def test_stopping_publishes_offline_presence(chain):
    bridge, il, runner, model, sink = chain
    await runner.stop()
    assert il.retained.get("il/_producer/tuya") is None or il.retained["il/_producer/tuya"].payload == "offline"
    assert il.published[-1][:2] == ("il/_producer/tuya", "offline")
