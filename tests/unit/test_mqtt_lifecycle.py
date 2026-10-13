"""Closing a transport releases callbacks and the paho thread without blocking its event loop."""

import asyncio
import threading
from unittest.mock import Mock

import pytest

from rustuya_local.host import MqttTransport
from rustuya_local.host.transport import Message


async def test_close_cancels_callbacks_and_ignores_queued_messages():
    transport = MqttTransport("localhost")
    transport._client = Mock()
    started, cleaned = asyncio.Event(), asyncio.Event()
    received = []

    async def callback(msg):
        received.append(msg.payload)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            await transport.close()  # nested cleanup must not wait on itself
            cleaned.set()

    await transport.subscribe("device", callback)
    transport._dispatch(Message("device", "first"))
    await started.wait()
    asyncio.get_running_loop().call_soon(transport._dispatch, Message("device", "queued"))
    await transport.close()
    transport._connected_in_loop()
    assert cleaned.is_set() and not transport._tasks
    assert received == ["first"] and not transport._connected.is_set()
    await transport.close()
    transport._client.disconnect.assert_called_once()
    transport._client.loop_stop.assert_called_once()
    with pytest.raises(RuntimeError, match="closed"):
        await transport.publish("device", "late")
    with pytest.raises(RuntimeError, match="closed"):
        await transport.subscribe("device", callback)


async def test_cancelled_close_finishes_thread_cleanup_off_the_loop():
    transport = MqttTransport("localhost")
    transport._client = Mock()
    entered, release = threading.Event(), threading.Event()

    def loop_stop():
        entered.set()
        assert release.wait(5)

    transport._client.loop_stop.side_effect = loop_stop
    closing = asyncio.create_task(transport.close())
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()  # cancellation cannot leave the paho thread running
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert transport._close_task.done() and not transport._tasks


async def test_failed_async_callback_is_reported_and_released(caplog):
    transport = MqttTransport("localhost")
    transport._client = Mock()

    async def fail(msg):
        raise ValueError("broken callback")

    await transport.subscribe("device", fail)
    transport._dispatch(Message("device", "value"))
    await asyncio.gather(*transport._tasks, return_exceptions=True)
    await asyncio.sleep(0)
    assert not transport._tasks
    assert "handler for device failed" in caplog.text and "broken callback" in caplog.text
    await transport.close()


async def test_connect_timeout_closes_the_client():
    transport = MqttTransport("localhost")
    transport._client = Mock()
    with pytest.raises(TimeoutError):
        await transport.connect(timeout=0.001)
    transport._client.disconnect.assert_called_once()
    transport._client.loop_stop.assert_called_once()
    assert transport._closing and transport._close_task.done()


async def test_async_handler_can_initiate_close_without_waiting_on_itself():
    transport = MqttTransport("localhost")
    transport._client = Mock()
    finished = asyncio.Event()

    async def callback(msg):
        await transport.close()
        finished.set()

    await transport.subscribe("device", callback)
    transport._dispatch(Message("device", "value"))
    await asyncio.wait_for(finished.wait(), 5)
    await asyncio.sleep(0)
    assert not transport._tasks and transport._close_task.done()
