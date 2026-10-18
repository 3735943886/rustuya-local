"""IL follows what rustuya-bridge actually holds, not the whole device file: a device the bridge does not have is not
advertised, removing one from the bridge clears its retained IL topics, and adding one brings it (and what was already
heard about it) back. In one process on in-memory brokers, the bridge side played by hand."""
import json

import pytest
from devices import lamp, status_reply

from rustuya_local.bridge_client import BridgeClient
from rustuya_local.host import Hub, IlTopics, InProcessTransport, Runner


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
    bridge_client.stop()
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


def status_requests(bridge):
    return [json.loads(p) for t, p, *_ in bridge.published
            if t.startswith("rustuya/command") and json.loads(p).get("action") == "status"]


async def test_another_clients_status_pages_are_left_alone(chain):
    """What r5c showed: 61 devices, pages of 50 + 11. When another client (the config flow's Manager) asked, this one
    took its first page as its own, asked for the second page too, and the second copy of that last page was then
    committed as the whole list: 61 devices read as 11, and 50 taken out of IL."""
    bridge, il, runner, bridge_client = chain
    ids = [f"d{i:02d}" for i in range(61)]
    bridge_client.sync_devices([lamp(i) for i in ids])
    await reply(bridge, status_reply(ids[:50], offset=0, has_more=True))        # its own request, answered
    await settle(runner, bridge_client, bridge, il)
    await reply(bridge, status_reply(ids[50:], offset=50))
    await settle(runner, bridge_client, bridge, il)
    assert len(advertised(il)) == 61
    asked = len(status_requests(bridge))

    # another client's request: its pages, and its last page twice (it and the other client each asked for it)
    await reply(bridge, status_reply(ids[:50], offset=0, has_more=True))
    await reply(bridge, status_reply(ids[50:], offset=50))
    await reply(bridge, status_reply(ids[50:], offset=50))
    await settle(runner, bridge_client, bridge, il)
    assert len(advertised(il)) == 61
    assert len(status_requests(bridge)) == asked                                # asked for no page of it


async def test_one_status_request_at_a_time_and_once_more_if_asked_meanwhile(chain):
    bridge, il, runner, bridge_client = chain
    bridge_client.sync_devices([lamp("a"), lamp("b")])
    await settle(runner, bridge_client, bridge, il)
    assert len(status_requests(bridge)) == 1
    bridge_client._request_status()                     # e.g. the bridge announces a registry change meanwhile
    bridge_client._request_status()
    await settle(runner, bridge_client, bridge, il)
    assert len(status_requests(bridge)) == 1            # not a second request whose pages would mix with the first's

    await reply(bridge, status_reply(["a"]))
    await settle(runner, bridge_client, bridge, il)
    assert len(status_requests(bridge)) == 2            # once more, for the change asked about meanwhile
    await reply(bridge, status_reply(["a", "b"]))
    await settle(runner, bridge_client, bridge, il)
    assert advertised(il) == {"a", "b"} and len(status_requests(bridge)) == 2


async def test_commands_take_the_command_templates_form():
    """r5c's templates: a one-DP write is the bare value on `.../set/<id>/<dp>` (pyrustuyabridge.render_command, checked
    against the bridge's own parser), a multi-DP write stays one command, and nothing is left as a literal `{dp}`."""
    from rustuya_local.host import BridgeCommand

    bridge = InProcessTransport()
    await bridge.publish("rustuya/bridge/config", json.dumps({
        "mqtt_root_topic": "rustuya", "mqtt_command_topic": "{root}/command/{action}/{id}/{dp}",
        "mqtt_event_topic": "{root}/event/{type}/{id}/{dp}", "mqtt_message_topic": "{root}/{level}/{id}"}), 0, True)
    bridge_client = BridgeClient(bridge, "rustuya")
    await bridge_client.start(timeout=1)
    bridge_client.send_command(BridgeCommand("eb1", "set", {"1": False}))
    bridge_client.send_command(BridgeCommand("eb1", "set", {"21": "colour", "24": "000003e803e8"}))
    bridge_client.send_command(BridgeCommand("eb1", "get", None))
    await bridge_client.drain()
    sent = [(t, p) for t, p, *_ in bridge.published if t.startswith("rustuya/command")]
    assert sent[0] == ("rustuya/command/set/eb1/1", "false")
    assert sent[1][0] == "rustuya/command/set/eb1/-"
    assert json.loads(sent[1][1]) == {"action": "set", "id": "eb1", "dps": {"21": "colour", "24": "000003e803e8"}}
    assert sent[2][0] == "rustuya/command/get/eb1/-" and json.loads(sent[2][1]) == {"action": "get", "id": "eb1"}


async def test_a_restart_republishes_devices_as_they_were_without_a_flap():
    """What a restarting producer hears first is the bridge's retained topics: the link state and, with a `{dp}` in the
    event topic, one retained message per dp. All of it is handed over with the device, so the first thing IL sees
    of it is `available: true` and every value — never `available: false` in between (an il consumer's automation
    reads that as the device going away and coming back). Nothing is asked of the bridge: its retained snapshot is its
    current copy. A live reconnect afterwards does ask for the state (`get`)."""
    bridge, il = InProcessTransport(), InProcessTransport()
    await bridge.publish("rustuya/bridge/config", json.dumps({
        "mqtt_root_topic": "rustuya", "mqtt_command_topic": "{root}/command/{action}/{id}/{dp}",
        "mqtt_event_topic": "{root}/event/{type}/{id}/{dp}", "mqtt_message_topic": "{root}/{level}/{id}"}), 0, True)
    await bridge.publish("rustuya/error/a", '{"errorCode":0,"errorMsg":"Connection Successful"}', 0, True)
    for dp, value in (("20", "true"), ("22", "1000"), ("23", "0")):
        await bridge.publish(f"rustuya/event/state/a/{dp}", value, 0, True)
        await bridge.publish(f"rustuya/event/state/sub/{dp}", value, 0, True)
    hub = Hub([], il=IlTopics("il", "tuya"))
    bridge_client = BridgeClient(bridge, "rustuya")
    runner = Runner(hub, il, on_bridge_command=bridge_client.send_command)
    bridge_client.runner = runner
    await runner.start()
    await bridge_client.start(timeout=1)
    await settle(runner, bridge_client, bridge, il)
    bridge_client.sync_devices([lamp("a"), lamp("sub")])
    await reply(bridge, status_reply(["a", "sub"]))
    await settle(runner, bridge_client, bridge, il)
    try:
        for device in ("a", "sub"):
            assert [p for t, p, *_ in il.published if t == f"il/{device}/available"] == ["true"]
            assert il.retained[f"il/{device}/brightness"].payload == "100"
            assert il.retained[f"il/{device}/switch_led"].payload == "true"
        gets = [t for t, *_ in bridge.published if t.startswith("rustuya/command/get/")]
        assert gets == []
        await bridge.publish("rustuya/error/a", '{"errorCode":0,"errorMsg":"Connection Successful"}', 0, False)
        await settle(runner, bridge_client, bridge, il)
        gets = [t for t, *_ in bridge.published if t.startswith("rustuya/command/get/")]
        assert gets == ["rustuya/command/get/a/-"]
    finally:
        await runner.stop()


async def test_foreign_tail_cannot_replace_a_snapshot(chain):
    bridge, il, runner, client = chain
    client.sync_devices([lamp("a"), lamp("b")])
    await reply(bridge, status_reply(["b"], offset=50, total=51))
    assert client._registered is None
    await reply(bridge, status_reply(["a"], has_more=True, total=2))
    # Duplicate page zero and a tail of a different-sized snapshot are ignored.
    await reply(bridge, status_reply(["b"], total=1))
    await reply(bridge, status_reply(["b"], offset=1, total=3))
    assert client._registered is None
    await reply(bridge, status_reply(["b"], offset=1, total=2))
    await settle(runner, client, bridge, il)
    assert advertised(il) == {"a", "b"}
    client.stop()


async def test_lost_status_retries_without_external_trigger(chain, monkeypatch):
    import asyncio

    import rustuya_local.bridge_client as bc

    bridge, il, runner, client = chain
    monkeypatch.setattr(bc, "STATUS_CYCLE_TIMEOUT", 0.02)
    client.sync_devices([lamp("a")])
    await asyncio.sleep(0.05)
    await client.drain()
    assert len(status_requests(bridge)) >= 2
    await reply(bridge, status_reply(["a"], total=1))
    await settle(runner, client, bridge, il)
    assert advertised(il) == {"a"}
    client.stop()
    count = len(status_requests(bridge))
    await asyncio.sleep(0.05)
    assert len(status_requests(bridge)) == count


async def test_shared_registry_waits_for_full_pages_and_coalesces_refreshes(chain):
    import asyncio

    bridge, il, runner, client = chain
    client.sync_devices([])
    first = asyncio.create_task(client.registry())
    second = asyncio.create_task(client.registry())
    await reply(bridge, status_reply(['a'], offset=0, has_more=True, total=2))
    await settle(runner, client, bridge, il)
    assert not first.done() and not second.done()
    assert len(status_requests(bridge)) == 2  # one first-page request and its next page
    await reply(bridge, status_reply(['b'], offset=1, total=2))
    await settle(runner, client, bridge, il)
    assert set(await first) == set(await second) == {'a', 'b'}
    first = asyncio.create_task(client.registry(refresh=True))
    second = asyncio.create_task(client.registry(refresh=True))
    await asyncio.sleep(0)
    await settle(runner, client, bridge, il)
    assert len(status_requests(bridge)) == 3
    await reply(bridge, status_reply(['a']))
    await settle(runner, client, bridge, il)
    assert not first.done() and not second.done()  # queued refresh still in flight
    assert len(status_requests(bridge)) == 4
    await reply(bridge, status_reply([]))
    await settle(runner, client, bridge, il)
    assert await first == await second == {}


async def test_panel_uses_service_registry_and_transport_without_a_manager_connection(chain, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from custom_components.rustuya.panel_bridge import PanelBridge

    bridge, il, runner, client = chain
    client.sync_devices([])
    path = tmp_path / 'devices.json'
    path.write_text(json.dumps([{'id': 'a', 'name': 'N/A'}, {'id': 'sub', 'cid': 'c1', 'parent_id': 'a'}]))

    class Hass:
        async def async_add_executor_job(self, fn):
            return await asyncio.to_thread(fn)

    panel = PanelBridge(Hass(), SimpleNamespace(bridge_client=client), str(path))
    ready = asyncio.create_task(panel.ready())
    snapshot = json.loads(status_reply(['a', 'sub']))
    snapshot['devices']['sub'].update(cid='c1', parent_id='a')
    snapshot['devices']['sub'].pop('ip', None)
    await reply(bridge, json.dumps(snapshot))
    await settle(runner, client, bridge, il)
    await ready
    asked = len(status_requests(bridge))
    diff = await panel.sync()
    assert {d.id for d in diff.synced} == {'a', 'sub'}
    assert len(status_requests(bridge)) == asked
    command = asyncio.create_task(panel.publish_command(
        'add', target_id='new', target_name='New', extra={'cid': 'c2', 'parent_id': 'a'}))
    await settle(runner, client, bridge, il)
    await asyncio.sleep(0)
    assert json.loads(bridge.published[-1][1]) == {
        'action': 'add', 'id': 'new', 'name': 'New', 'cid': 'c2', 'parent_id': 'a'}
    assert not command.done()
    await reply(bridge, json.dumps({'action': 'add', 'id': 'new', 'status': 'ok'}))
    await command
    command = asyncio.create_task(panel.remove_device('a'))
    await asyncio.sleep(0)
    await reply(bridge, json.dumps({'action': 'remove', 'id': 'a', 'status': 'ok'}))
    await command
    refresh = asyncio.create_task(panel.wait_registry_refresh())
    await asyncio.sleep(0)
    await settle(runner, client, bridge, il)
    await reply(bridge, status_reply(['sub', 'new']))
    await settle(runner, client, bridge, il)
    await refresh
    diff = await panel.sync()
    assert [d.id for d in diff.missing] == ['a']
    assert [d.id for d in diff.orphaned] == ['new']


async def test_panel_command_ignores_retained_and_unrelated_acknowledgements(chain):
    import asyncio

    from rustuya_local.host.transport import Message

    bridge, _il, _runner, client = chain
    command = asyncio.create_task(client.publish_command('add', target_id='new'))
    await asyncio.sleep(0)
    client._on_message(Message('rustuya/response/new', '{"action":"add","id":"new","status":"ok"}', True))
    await reply(bridge, '{"action":"add","id":"other","status":"ok"}')
    await reply(bridge, '{"action":"remove","id":"new","status":"ok"}')
    await asyncio.sleep(0)
    assert not command.done()
    await reply(bridge, '{"action":"add","id":"new","status":"ok"}')
    await command
    assert not client._command_waiters


@pytest.mark.parametrize('outcome', ['timeout', 'cancel', 'stop', 'error'])
async def test_panel_command_cleans_up_pending_acknowledgement(chain, outcome):
    import asyncio

    bridge, _il, _runner, client = chain
    command = asyncio.create_task(client.publish_command('add', target_id='new', timeout=0.02))
    await asyncio.sleep(0)
    if outcome == 'cancel':
        command.cancel()
        expected = asyncio.CancelledError
    elif outcome == 'stop':
        client.stop()
        expected = RuntimeError
    elif outcome == 'error':
        await reply(bridge, '{"action":"add","id":"new","status":"error","error":"rejected"}')
        expected = RuntimeError
    else:
        expected = TimeoutError
    with pytest.raises(expected):
        await command
    assert not client._command_waiters
