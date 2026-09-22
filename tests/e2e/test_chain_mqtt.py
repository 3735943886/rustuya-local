"""The same chain over a real MQTT broker (mosquitto): bridge simulator -> Runner -> il-ha's model, three clients."""
import asyncio
import json

import pytest
from devices import lamp
from ildevice.core import IlModel
from tuya2ildevice import BridgeTopics, Hub, IlTopics

from tuya2ildevice.host import Runner
from tuya2ildevice.host import MqttTransport
from test_chain_in_process import Recorder, Timers


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


@pytest.fixture
async def mqtt_chain(broker):
    hub = Hub([lamp()], bridge=BridgeTopics("rustuya"), il=IlTopics("il", "tuya"))
    will = hub.presence(False)
    bridge_side = MqttTransport("127.0.0.1", broker, client_id="t2il-bridge")
    il_side = MqttTransport("127.0.0.1", broker, client_id="t2il-il", will=(will.topic, will.payload, will.qos, will.retain))
    consumer = MqttTransport("127.0.0.1", broker, client_id="il-consumer")
    sim = MqttTransport("127.0.0.1", broker, client_id="bridge-sim")
    for t in (bridge_side, il_side, consumer, sim):
        await t.connect()
    sink = Recorder()
    model = IlModel(consumer, sink, Timers())
    await model.start()
    runner = Runner(hub, bridge_side, il_side)
    await runner.start()
    yield sim, runner, model, il_side, sink
    await runner.stop()
    await model.stop()
    for t in (bridge_side, il_side, consumer, sim):
        await t.close()
    # leave no retained state behind for the next test
    cleaner = MqttTransport("127.0.0.1", broker, client_id="cleaner")
    await cleaner.connect()
    for topic in ("il/lamp1", "il/lamp1/available", "il/lamp1/switch_led", "il/lamp1/brightness",
                  "il/lamp1/color_temperature", "il/_producer/tuya", "rustuya/error/lamp1", "rustuya/event/state/lamp1"):
        await cleaner.publish(topic, "", 1, True)
    await asyncio.sleep(0.1)
    await cleaner.close()


async def test_values_and_commands_over_a_real_broker(mqtt_chain):
    sim, runner, model, il_side, sink = mqtt_chain
    await until(lambda: "lamp1" in model.devices)
    commands = []
    await sim.subscribe("rustuya/command", lambda m: commands.append(json.loads(m.payload)))
    await sim.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 1, True)
    await until(lambda: {"action": "get", "id": "lamp1"} in commands)
    await sim.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', 1, True)
    dev = model.devices["lamp1"]
    await until(lambda: dev.online and dev.values.get("brightness") == 100)
    await model.transport.publish("il/lamp1/brightness/set", "50", 1)
    await until(lambda: any(c.get("action") == "set" for c in commands))
    assert [c for c in commands if c.get("action") == "set"] == [{"action": "set", "id": "lamp1", "dps": {"20": True, "22": 507}}]


async def test_a_dropped_connection_fires_the_last_will_and_the_runner_recovers(mqtt_chain):
    sim, runner, model, il_side, sink = mqtt_chain
    await until(lambda: "lamp1" in model.devices)
    presence = []
    await sim.subscribe("il/_producer/tuya", lambda m: presence.append(m.payload.decode()))
    await until(lambda: presence[-1:] == ["online"])
    il_side._client._sock.close()                     # an unclean drop: the broker publishes the Last Will
    await until(lambda: "offline" in presence)
    await until(lambda: presence[-1] == "online")     # paho reconnects; the runner republishes presence
    assert "lamp1" in model.devices
