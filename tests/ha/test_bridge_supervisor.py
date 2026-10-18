"""Embedded bridge readiness and ownership of native async shutdown."""
import asyncio

import pytest

from custom_components.rustuya import bridge_supervisor
from custom_components.rustuya.bridge_supervisor import EmbeddedBridge


async def test_real_embedded_bridge_start_and_stop(broker, tmp_path, socket_enabled):
    from rustuya_local.bridge_client import BridgeClient
    from rustuya_local.host import Hub, InProcessTransport, MqttTransport, Runner

    bridge = EmbeddedBridge(f"mqtt://127.0.0.1:{broker}", "startup-test", str(tmp_path / "bridge.json"))
    transport = MqttTransport("127.0.0.1", broker)
    client = BridgeClient(transport, "startup-test")
    client.runner = Runner(Hub([]), InProcessTransport())
    try:
        await bridge.start()
        assert bridge._task is not None and not bridge._task.done()
        await transport.connect()
        client.sync_devices([])
        await client.start()
        await client.registry()
        await client.publish_command("add", target_id="ack-test",
                                     extra={"key": "thisisarealkey00", "ip": "127.0.0.2"})
        assert "ack-test" in await client.registry(refresh=True)
        await client.publish_command("remove", target_id="ack-test")
        assert "ack-test" not in await client.registry(refresh=True)
    finally:
        client.stop()
        await client.drain()
        await transport.close()
        await bridge.stop()
    assert bridge._task is None
    await bridge.stop()


async def test_shutdown_timeout_keeps_ownership(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    bridge = EmbeddedBridge("mqtt://localhost", "test", "unused.json")
    bridge._server = SimpleNamespace(stop=Mock())
    task = bridge._task = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(bridge_supervisor, "STOP_TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        await bridge.stop()
    assert bridge._task is task and not task.cancelled()
    task.set_result(None)
    await bridge.stop()
    assert bridge._task is None


@pytest.mark.parametrize("outcome", ["failure", "timeout", "cancel"])
async def test_failed_start_closes_observer_and_native_task(monkeypatch, outcome):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import pyrustuyabridge

    from rustuya_local.host import mqtt

    stopped = asyncio.Event()
    entered = asyncio.Event()

    async def run():
        entered.set()
        if outcome == "failure":
            raise RuntimeError("native startup failed")
        await stopped.wait()

    server = SimpleNamespace(start_async=run, stop=Mock(side_effect=stopped.set))
    observer = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())

    async def subscribe(topic, callback):
        # A stale retained config must never count as readiness.
        callback(SimpleNamespace(retain=True, payload=b'{"session_id":"old"}'))

    observer.subscribe = subscribe
    monkeypatch.setattr(mqtt, "MqttTransport", lambda *args, **kwargs: observer)
    monkeypatch.setattr(pyrustuyabridge, "PyBridgeServer", lambda **kwargs: server)
    monkeypatch.setattr(bridge_supervisor, "START_TIMEOUT", 0.05)
    bridge = EmbeddedBridge("mqtt://localhost", "test", "unused.json")
    pending = asyncio.create_task(bridge.start())
    if outcome == "cancel":
        await entered.wait()
        pending.cancel()
        expected = asyncio.CancelledError
    else:
        expected = RuntimeError if outcome == "failure" else TimeoutError
    with pytest.raises(expected):
        await pending
    observer.close.assert_awaited_once()
    assert bridge._task is None
