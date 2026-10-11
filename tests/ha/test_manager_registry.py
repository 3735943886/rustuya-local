"""Use the installed manager's real paging handler with interleaved broadcast replies."""
import asyncio
from types import SimpleNamespace

import pytest
from rustuya_manager.mqtt import BridgeClient
from rustuya_manager.state import State

from custom_components.rustuya.manager_registry import ready_registry


def page(offset, ids, total, more):
    return {"offset": offset, "returned": len(ids), "device_count": total, "has_more": more,
            "devices": {id: {"id": id, "name": id, "type": "WiFi"} for id in ids}}


async def test_registry_ignores_other_clients_pages_and_waits_for_complete_snapshot():
    client = BridgeClient("mqtt://localhost", "rustuya", State())
    client._bootstrap_done.set()
    client._status_active = True
    client._status_accum = {"stale": object()}
    commands = asyncio.Queue()

    async def publish(action, **kw):
        await commands.put(kw.get("extra", {}).get("offset", 0))

    client.publish_command = publish
    pending = asyncio.create_task(ready_registry(SimpleNamespace(client=client), 1))
    assert await commands.get() == 0
    try:
        await client._handle_status_page(page(2, ["c"], 3, False))
        assert not pending.done() and not client.state.bridge
        await client._handle_status_page(page(0, ["a", "b"], 3, True))
        assert await commands.get() == 2
        # Duplicate first page and a final page from a different fleet cannot commit.
        await client._handle_status_page(page(0, ["a", "b"], 3, True))
        await client._handle_status_page(page(2, ["z"], 4, False))
        await client._handle_status_page(page(2, ["a"], 3, False))
        assert not pending.done() and not client.state.bridge
        await client._handle_status_page(page(2, ["c"], 3, False))
        await pending
        assert set(client.state.bridge) == {"a", "b", "c"}
        # A later request resets pagination and rejects stale later pages too.
        await client._request_status()
        assert await commands.get() == 0
        await client._handle_status_page(page(2, ["c"], 3, False))
        assert set(client.state.bridge) == {"a", "b", "c"}
        await client._handle_status_page(page(0, [], 0, False))
        assert client.state.bridge == {}
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_registry_does_not_query_before_bootstrap_and_fails_on_timeout():
    client = BridgeClient("mqtt://localhost", "rustuya", State())
    commands = []

    async def publish(*args, **kw):
        commands.append(kw)

    client.publish_command = publish
    with pytest.raises(TimeoutError, match="subscriptions"):
        await ready_registry(SimpleNamespace(client=client), 0.01)
    assert commands == []
    client._bootstrap_done.set()
    with pytest.raises(TimeoutError):
        await ready_registry(SimpleNamespace(client=client), 0.01)
    assert len(commands) == 1
