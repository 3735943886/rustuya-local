"""A paho-mqtt transport. The in-process one is `memory.InProcessTransport`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ..async_tools import run_in_thread

try:
    import paho.mqtt.client as mqtt
except ImportError as err:                   # the extra is not installed
    raise ImportError("MqttTransport needs paho-mqtt: pip install paho-mqtt") from err

from .memory import matches
from .transport import Callback, Message, Unsubscribe, deliver

_LOGGER = logging.getLogger(__name__)


class MqttTransport:
    """One paho connection. Subscriptions are remembered and made again after a reconnect; `on_connect` callbacks run
    (in the event loop) after every (re)connect, which is where a host republishes what a Last Will took away."""

    def __init__(self, host: str, port: int = 1883, *, client_id: str | None = None, username: str | None = None,
                 password: str | None = None, will: tuple[str, str, int, bool] | None = None,
                 tls: bool = False) -> None:
        """`tls`: connect with TLS and the system's CA certificates (an `mqtts://` broker)."""
        self.host, self.port = host, port
        self.on_connect: list[Callable[[], None]] = []
        self._subs: list[tuple[str, int, Callback]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id or "")
        if username:
            self._client.username_pw_set(username, password)
        if will:
            topic, payload, qos, retain = will
            self._client.will_set(topic, payload, qos, retain)
        if tls:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    async def connect(self, timeout: float = 10.0) -> None:
        self._check_open()
        self._loop = asyncio.get_running_loop()
        self._client.connect_async(self.host, self.port)
        self._client.loop_start()
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except (TimeoutError, asyncio.CancelledError):   # asyncio.TimeoutError != TimeoutError before 3.11
            await self.close()   # a failed connect must not leave the paho thread running
            raise

    async def close(self) -> None:
        caller = asyncio.current_task()
        if self._close_task is not None and caller in self._tasks:
            # A cancelled handler may call close in its finally block; cleanup already waits for it.
            return
        if self._close_task is None:
            self._closing = True
            self._connected.clear()
            self._close_task = asyncio.create_task(self._close(caller))
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.gather(self._close_task, return_exceptions=True)
            raise

    async def _close(self, caller: asyncio.Task | None) -> None:
        try:
            await run_in_thread(self._disconnect)
        finally:
            # A handler initiating close cannot wait for itself to return from close.
            tasks = [task for task in self._tasks if task is not caller]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._subs.clear()
            self.on_connect.clear()

    def _disconnect(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    def _check_open(self) -> None:
        if self._closing:
            raise RuntimeError("MQTT transport is closed")

    async def subscribe(self, topic: str, callback: Callback, qos: int = 1) -> Unsubscribe:
        self._check_open()
        entry = (topic, qos, callback)
        self._subs.append(entry)
        if self._connected.is_set():
            self._client.subscribe(topic, qos)

        def unsubscribe() -> None:
            if entry in self._subs:
                self._subs.remove(entry)
                if not any(t == topic for t, _, _ in self._subs) and self._connected.is_set():
                    self._client.unsubscribe(topic)

        return unsubscribe

    async def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        self._check_open()
        self._client.publish(topic, payload, qos, retain)

    # ---- paho thread -> event loop -----------------------------------------------------

    def _on_connect(self, client, _userdata, _flags, reason_code, _props=None) -> None:
        if self._closing:
            return
        if reason_code != 0:
            _LOGGER.error("MQTT connect to %s:%s refused: %s", self.host, self.port, reason_code)
            return
        for topic, qos in {(t, q) for t, q, _ in self._subs}:
            client.subscribe(topic, qos)
        self._loop.call_soon_threadsafe(self._connected_in_loop)

    def _connected_in_loop(self) -> None:
        if self._closing:
            return
        self._connected.set()
        for fn in list(self.on_connect):
            fn()

    def _on_disconnect(self, _client, _userdata, _flags, _reason_code, _props=None) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._connected.clear)

    def _on_message(self, _client, _userdata, msg) -> None:
        self._loop.call_soon_threadsafe(self._dispatch, Message(msg.topic, msg.payload, bool(msg.retain)))

    def _dispatch(self, msg: Message) -> None:
        if self._closing:
            return
        for topic, _, callback in list(self._subs):
            if matches(topic, msg.topic):
                try:
                    deliver(callback, msg, self._tasks)
                except Exception:  # a consumer's bug must not stop the client loop
                    _LOGGER.exception("handler for %s failed", msg.topic)
