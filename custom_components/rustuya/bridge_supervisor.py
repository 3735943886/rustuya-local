"""Own the embedded bridge's native async task and verify its MQTT startup signal."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from urllib.parse import urlsplit

_LOGGER = logging.getLogger(__name__)
START_TIMEOUT = 15.0
STOP_TIMEOUT = 10.0


class EmbeddedBridge:
    def __init__(self, broker: str, root: str, state_file: str, log_level: str = "warn",
                 username: str | None = None, password: str | None = None) -> None:
        self.broker = broker
        self.root = root
        self.state_file = state_file
        self.log_level = log_level
        self.username = username
        self.password = password
        self._server = None
        self._task: asyncio.Future | None = None
        self._stopping = False

    async def start(self) -> None:
        import pyrustuyabridge as pb

        from rustuya_local.host.mqtt import MqttTransport

        if self._task is not None:
            raise RuntimeError("the embedded bridge has already been started")
        address = urlsplit(self.broker)
        observer = MqttTransport(address.hostname, address.port or 1883,
                                 client_id=f"rustuya-startup-{secrets.token_hex(8)}",
                                 username=self.username, password=self.password,
                                 tls=address.scheme == "mqtts")
        ready = asyncio.Event()

        def on_config(message) -> None:
            # A retained config may belong to an earlier, dead bridge instance.
            if message.retain or not message.payload:
                return
            try:
                config = json.loads(message.payload)
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(config, dict) and config:
                ready.set()

        waiter = None
        try:
            async with asyncio.timeout(START_TIMEOUT):
                await observer.connect()
                await observer.subscribe(f"{self.root}/bridge/config", on_config)
                kwargs = {"mqtt_broker": self.broker, "mqtt_root_topic": self.root, "mqtt_retain": True,
                          "state_file": self.state_file, "no_signals": True, "log_level": self.log_level}
                if self.username:
                    kwargs["mqtt_username"] = self.username
                if self.password:
                    kwargs["mqtt_password"] = self.password
                self._server = pb.PyBridgeServer(**kwargs)
                self._stopping = False
                self._task = asyncio.ensure_future(self._server.start_async())
                self._task.add_done_callback(self._exited)
                waiter = asyncio.create_task(ready.wait())
                await asyncio.wait((waiter, self._task), return_when=asyncio.FIRST_COMPLETED)
                if self._task.done():
                    await self._task  # preserve the native startup exception
                    raise RuntimeError("the embedded bridge exited during startup")
        except BaseException:
            await self.stop()
            raise
        finally:
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            try:
                await observer.close()
            except BaseException:
                # Startup has not returned ownership to the caller yet.
                await self.stop()
                raise

    def _exited(self, task: asyncio.Future) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if not self._stopping:
            _LOGGER.error("the embedded bridge stopped unexpectedly: %s", error or "server exited")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        self._server.stop()
        # Shield the native task: a timed-out shutdown must retain ownership so
        # another stop can still wait for cleanup, rather than losing the server.
        await asyncio.wait_for(asyncio.shield(asyncio.gather(self._task, return_exceptions=True)), STOP_TIMEOUT)
        self._task = None
        self._server = None
