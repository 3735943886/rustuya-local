"""IL follows what rustuya-bridge actually holds, not the whole device file: a device the bridge does not have is not
advertised, removing one from the bridge clears its retained IL topics, and adding one brings it (and what was already
heard about it) back. In one process on in-memory brokers, the bridge side played by hand."""
import json

import pytest
from devices import lamp, status_reply
from rustuya_local.bridge_client import BridgeClient
from tuya2ildevice import Hub, IlTopics
from tuya2ildevice.host import InProcessTransport, Runner


@pytest.fixture
async def chain():
    bridge, il = InProcessTransport(), InProcessTransport()
    hub = Hub([], il=IlTopics("il", "tuya"))
    bridge_client = BridgeClient(bridge, "rustuya")
    runner = Runner(hub, il, on_bridge_command=bridge_client.send_command)
    bridge_client.runner = runner
    await runner.start()
    await bridge_client.start(timeout=0.05)
    yield bridge, il, runner, bridge_client
    await runner.stop()


async def settle(runner, bridge_client, *buses):
    await runner.drain()
    await bridge_client.drain()
    for b in buses:
        await b.settle()


def advertised(il):
    return {t.split("/")[1] for t, m in il.retained.items() if t.count("/") == 1 and not t.startswith("il/_") and m.payload}


async def reply(bridge, payload):
    await bridge.publish("rustuya/response/bridge", payload, 0, False)


async def test_only_devices_the_bridge_holds_are_advertised(chain):
    bridge, il, runner, bridge_client = chain
    bridge_client.sync_devices([lamp("a"), lamp("b")])
    await settle(runner, bridge_client, bridge, il)
    assert json.loads(bridge.published[-1][1]) == {"action": "status", "id": "bridge"}      # it asked what the bridge holds
    assert advertised(il) == set()                                                          # nothing known yet

    await reply(bridge, status_reply(["a"]))
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"a"}


async def test_removing_a_device_from_the_bridge_clears_its_retained_topics(chain):
    bridge, il, runner, bridge_client = chain
    bridge_client.sync_devices([lamp("a")])
    await reply(bridge, status_reply(["a"]))
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"a"} and "il/a/available" in il.retained

    await reply(bridge, json.dumps({"action": "remove", "status": "ok", "id": "a"}))
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == set() and not il.retained.get("il/a/available", None or type("M", (), {"payload": ""})).payload


async def test_adding_a_device_on_the_bridge_advertises_it_with_what_was_already_heard(chain):
    bridge, il, runner, bridge_client = chain
    bridge_client.sync_devices([lamp("a"), lamp("b")])
    await reply(bridge, status_reply(["a"]))
    # b is not held yet, but its retained connection state and values are already on the broker
    await bridge.publish("rustuya/error/b", '{"errorCode":0,"errorMsg":"Connection Successful"}', 0, True)
    await bridge.publish("rustuya/event/state/b", '{"20":true,"22":1000,"23":0}', 0, True)
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"a"}

    await reply(bridge, json.dumps({"action": "add", "status": "ok", "id": "b"}))
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"a", "b"}
    assert il.retained["il/b/available"].payload == "true" and il.retained["il/b/brightness"].payload == "100"


async def test_a_paged_status_reply_is_assembled_before_anything_is_decided(chain):
    bridge, il, runner, bridge_client = chain
    bridge_client.sync_devices([lamp("a"), lamp("b"), lamp("c")])
    await reply(bridge, status_reply(["a"], offset=0, has_more=True))
    await settle(runner, bridge_client, bridge, il)
    assert json.loads(bridge.published[-1][1]) == {"action": "status", "id": "bridge", "offset": 1}   # asks for the next page
    assert advertised(il) == set()                                                                     # not decided yet

    await reply(bridge, status_reply(["b"], offset=1))
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"a", "b"}


async def test_a_new_device_file_is_intersected_with_the_bridge_too(chain):
    bridge, il, runner, bridge_client = chain
    bridge_client.sync_devices([lamp("a")])
    await reply(bridge, status_reply(["a", "b"]))
    await settle(runner, bridge_client, bridge, il)
    bridge_client.sync_devices([lamp("b"), lamp("c")])              # the file changes: a is gone, c is not on the bridge
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"b"}


async def test_a_retained_reply_is_an_old_one_and_is_ignored():
    """Only a message replayed from the broker's store on subscribe is flagged retained (a live publish reaches an
    already-subscribed client unflagged), so the stale reply has to be on the broker before the client subscribes."""
    bridge, il = InProcessTransport(), InProcessTransport()
    await bridge.publish("rustuya/response/bridge", status_reply(["a"]), 0, True)          # left over from an earlier run
    hub = Hub([], il=IlTopics("il", "tuya"))
    bridge_client = BridgeClient(bridge, "rustuya")
    runner = Runner(hub, il, on_bridge_command=bridge_client.send_command)
    bridge_client.runner = runner
    await runner.start()
    await bridge_client.start(timeout=0.05)
    bridge_client.sync_devices([lamp("a")])
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == set()                       # not trusted: it waits for the live answer to its own `status`

    await reply(bridge, status_reply(["a"]))
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"a"}
    await runner.stop()
