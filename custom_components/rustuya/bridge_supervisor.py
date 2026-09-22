"""An embedded rustuya-bridge: `pyrustuyabridge.PyBridgeServer` run in a background thread, tied to this config
entry's lifecycle instead of a separate rustuya-bridge process. Same class our own end-to-end tests drive
(tuya2ildevice/rustuya-local tests/e2e/test_full_stack.py) — proven against a real broker and real Tuya-protocol
devices, just supervised here instead of by a test fixture.

The embedded bridge still needs a real MQTT broker to talk through (it manages its own native MQTT client); it does
not remove that requirement, only who launches and owns the bridge process.
"""

from __future__ import annotations

import asyncio
import logging
import threading

_LOGGER = logging.getLogger(__name__)


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
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        import pyrustuyabridge as pb

        kwargs = dict(mqtt_broker=self.broker, mqtt_root_topic=self.root, mqtt_retain=True,
                      state_file=self.state_file, no_signals=True, log_level=self.log_level)
        if self.username:
            kwargs["mqtt_username"] = self.username
        if self.password:
            kwargs["mqtt_password"] = self.password
        self._server = pb.PyBridgeServer(**kwargs)
        loop = asyncio.get_running_loop()
        started = loop.create_future()

        def run() -> None:
            try:
                self._server.start()
            except Exception:
                _LOGGER.exception("the embedded bridge stopped unexpectedly")
            finally:
                if not started.done():
                    loop.call_soon_threadsafe(started.set_result, None)

        self._thread = threading.Thread(target=run, name="rustuya-embedded-bridge", daemon=True)
        self._thread.start()
        # give the bridge a moment to fail fast (a bad broker URL, a locked state file) before reporting success;
        # `run()` resolves `started` on exit, so a quick crash surfaces as this future finishing early too
        await asyncio.wait([asyncio.ensure_future(asyncio.sleep(0.2)), started], return_when=asyncio.FIRST_COMPLETED)
        if self._thread is not None and not self._thread.is_alive():
            raise RuntimeError("the embedded bridge exited during startup; check the log")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.stop()
        if self._thread is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._thread.join, 10)
            self._thread = None
