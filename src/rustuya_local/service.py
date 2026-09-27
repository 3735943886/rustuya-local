"""The one assembly every shell runs: rustuya-bridge -> `tuya2ildevice.Hub` -> IL, with the device file and the user
overrides followed as they change. The CLI daemon, the Home Assistant integration and the rustuya-manager plugin differ
only in how they connect the two transports and how long they keep the service; nothing here imports any of them.

    service = Service(settings, connect_bridge=..., connect_il=...)
    await service.start()          # raises (and releases what it had connected) if the start fails
    ...
    await service.stop()

`connect_bridge()` returns a connected `Transport` to the bridge's broker; `connect_il(will)` returns one to the IL
broker with `will` (topic, payload, qos, retain) registered as its Last Will (M-12), or ignores it for a transport
that has none (in-process). The service closes both on `stop()` (when they have an async `close()`).

One producer per IL prefix and source: `start()` raises `AnotherProducer` while another one (the Home Assistant
integration, the rustuya-manager plugin or a daemon) serves them, since every device would show twice. A presence left
`online` that nobody answers for (a Last Will lost with the broker, or a producer on tuya2ildevice < 0.3.5) does not
stop the start.

With `pack` on, the override pack (tuya2ildevice's `pack/`) is copied into the overrides directory in the background at
start and every `pack_interval` seconds; the directory watcher loads what it brings.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tuya2ildevice import Hub, IlTopics, preload
from tuya2ildevice.host import (
    DeviceWatcher,
    OverrideWatcher,
    Runner,
    load_devices,
    producer_running,
)
from tuya2ildevice.host.transport import Transport

from .bridge_client import BridgeClient

_LOGGER = logging.getLogger(__name__)


def _close_later(stack: contextlib.AsyncExitStack, transport: Transport) -> None:
    close = getattr(transport, "close", None)          # optional: an in-process transport has nothing to close
    if close is not None:
        stack.push_async_callback(close)


Will = tuple[str, str, int, bool]


class AnotherProducer(RuntimeError):
    """Another producer serves this IL prefix and source; stop it first (or give this one another source)."""


@dataclass
class Settings:
    root: str = "rustuya"                      # the bridge's MQTT root
    prefix: str = "il"
    source: str = "tuya"
    devices: list[dict] = field(default_factory=list)
    devices_path: Path | None = None           # followed every `watch_interval` s when set (read at start if not)
    overrides_path: Path | None = None         # a custom_converters directory (or one .json), followed likewise
    watch_interval: float = 5.0
    pack: bool = False                         # sync tuya2ildevice's override pack into `overrides_path` (a directory)
    pack_interval: float = 86400.0
    pack_url: str | None = None                # the pack's base URL; tuya2ildevice's `master` when None
    hub_options: dict[str, Any] = field(default_factory=dict)
    """`tuya2ildevice.Hub` keyword arguments: `allow_hazardous`, `expose_unused`, `use_quirks`, `overrides` (inline,
    merged over the directory's), `converters`, `converter_types`."""


class Service:
    def __init__(self, settings: Settings, *, connect_bridge: Callable[[], Awaitable[Transport]],
                 connect_il: Callable[[Will], Awaitable[Transport]]) -> None:
        self.settings = settings
        self._connect_bridge = connect_bridge
        self._connect_il = connect_il
        self.bridge: Transport | None = None
        self.il: Transport | None = None
        self.hub: Hub | None = None
        self.runner: Runner | None = None
        self.bridge_client: BridgeClient | None = None
        self.device_watcher: DeviceWatcher | None = None
        self.override_watcher: OverrideWatcher | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self.pack_status: dict[str, Any] | None = None
        """The last pack sync: `{"at": epoch s, "error": str}` or `{"at", "added", "updated", "removed", "kept",
        "failed"}`; None before the first."""
        self._pack_now = asyncio.Event()
        self._pack_running = False

    async def start(self) -> None:
        s = self.settings
        # tuya2ildevice reads its data files on first use; here, not in the event loop when the first device arrives
        await asyncio.to_thread(preload)
        async with contextlib.AsyncExitStack() as stack:
            self.bridge = await self._connect_bridge()
            _close_later(stack, self.bridge)
            self.bridge_client = BridgeClient(self.bridge, s.root)

            options = dict(s.hub_options)
            inline = options.pop("overrides", None) or {}
            if s.overrides_path is not None:
                self.override_watcher = OverrideWatcher(s.overrides_path, None, s.watch_interval, base=inline)
                loaded = await self.override_watcher.load_initial()      # file I/O and imports run off the loop
                options["overrides"] = loaded.overrides
                options["converter_types"] = {**loaded.converter_types, **(options.get("converter_types") or {})}
            else:
                options["overrides"] = inline
            # no devices yet: the bridge client hands the Hub the ones the bridge holds
            self.hub = Hub([], il=IlTopics(s.prefix, s.source), **options)
            will = self.hub.presence(False)
            self.il = await self._connect_il((will.topic, will.payload, will.qos, will.retain))
            _close_later(stack, self.il)

            await self._refuse_if_another_producer(will.topic)
            self.runner = Runner(self.hub, self.il, on_bridge_command=self.bridge_client.send_command)
            self.bridge_client.runner = self.runner
            if self.override_watcher is not None:
                self.override_watcher.runner = self.runner
            await self.runner.start()
            stack.push_async_callback(self.runner.stop)
            await self.bridge_client.start()
            self.bridge_client.sync_devices(s.devices)

            if s.devices_path is not None and s.watch_interval > 0:
                self.device_watcher = DeviceWatcher(s.devices_path, self.bridge_client, s.watch_interval)
                self.device_watcher.start()
                stack.push_async_callback(self.device_watcher.stop)
            if self.override_watcher is not None and s.watch_interval > 0:
                self.override_watcher.watch()
                stack.push_async_callback(self.override_watcher.stop)
            if s.pack and s.overrides_path is not None and s.overrides_path.suffix != ".json":
                task = asyncio.ensure_future(self._pack_loop(s.overrides_path))
                stack.callback(task.cancel)
                stack.callback(setattr, self, "_pack_running", False)
            self._stack = stack.pop_all()
        _LOGGER.info("%d device(s) in the device file; IL follows the ones registered on %s", len(s.devices), s.root)

    async def _refuse_if_another_producer(self, presence_topic: str) -> None:
        running = await producer_running(self.il, presence_topic)
        if running:
            raise AnotherProducer(f"another producer (the Home Assistant integration, the rustuya-manager plugin or a "
                                  f"daemon) is running for {presence_topic}; stop it, or give this one another IL source")
        if running is None:
            _LOGGER.warning("%s says online but no producer answers (a stale Last Will, or one on tuya2ildevice before "
                            "0.3.5): starting; if an older producer does run, stop it", presence_topic)

    async def _pack_loop(self, directory: Path) -> None:
        import time

        from tuya2ildevice.host import pack

        url = self.settings.pack_url or pack.BASE_URL
        self._pack_running = True
        while True:
            self._pack_now.clear()
            try:
                result = await asyncio.to_thread(pack.sync, directory, base_url=url)
                self.pack_status = {"at": time.time(), **result.as_dict()}
            except pack.PackError as e:
                _LOGGER.warning("override pack: %s", e)
                self.pack_status = {"at": time.time(), "error": str(e)}
            except Exception as e:
                _LOGGER.exception("override pack")
                self.pack_status = {"at": time.time(), "error": f"{type(e).__name__}: {e}"}
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._pack_now.wait(), self.settings.pack_interval)

    def sync_pack_now(self) -> bool:
        """Run the pack sync now instead of at the end of its interval; False when the pack is off (nothing runs).
        `pack_status` changes once it is done."""
        if not self._pack_running:
            return False
        self._pack_now.set()
        return True

    async def stop(self) -> None:
        """Presence goes offline, pending bridge commands are sent, then both transports close. Idempotent."""
        stack, self._stack = self._stack, None
        if stack is None:
            return
        if self.bridge_client is not None:
            await self.bridge_client.drain()
        await stack.aclose()

    def refresh_devices(self, records: list[dict] | None = None) -> None:
        """Apply the device file as it is now (or `records`), without restarting anything."""
        if self.bridge_client is None:
            return
        if records is None:
            if self.settings.devices_path is None:
                return
            records = load_devices(self.settings.devices_path)
        self.bridge_client.sync_devices(records)
