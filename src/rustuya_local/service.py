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
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from tuya2ildevice import Hub, IlTopics
from tuya2ildevice.host import DeviceWatcher, OverrideWatcher, Runner, load_devices
from tuya2ildevice.host.transport import Transport

from .bridge_client import BridgeClient

_LOGGER = logging.getLogger(__name__)


def _close_later(stack: contextlib.AsyncExitStack, transport: Transport) -> None:
    close = getattr(transport, "close", None)          # optional: an in-process transport has nothing to close
    if close is not None:
        stack.push_async_callback(close)


Will = tuple[str, str, int, bool]


@dataclass
class Settings:
    root: str = "rustuya"                      # the bridge's MQTT root
    prefix: str = "il"
    source: str = "tuya"
    devices: list[dict] = field(default_factory=list)
    devices_path: Path | None = None           # followed every `watch_interval` s when set (read at start if not)
    overrides_path: Path | None = None         # a custom_converters directory (or one .json), followed likewise
    watch_interval: float = 5.0
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

    async def start(self) -> None:
        s = self.settings
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

            await self._warn_if_another_producer(will.topic)
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
            self._stack = stack.pop_all()
        _LOGGER.info("%d device(s) in the device file; IL follows the ones registered on %s", len(s.devices), s.root)

    async def _warn_if_another_producer(self, presence_topic: str, wait: float = 0.5) -> None:
        """Our presence topic already `online` means another producer with the same prefix and source is running
        (this one's Last Will would have set it `offline`): the devices would be published twice."""
        seen: list[str] = []
        unsub = await self.il.subscribe(presence_topic, lambda m: seen.append(
            m.payload.decode() if isinstance(m.payload, (bytes, bytearray)) else m.payload))
        try:
            for _ in range(int(wait / 0.05)):
                if seen:
                    break
                await asyncio.sleep(0.05)
        finally:
            unsub()
        if seen and seen[-1] == "online":
            _LOGGER.warning("%s is already online: another producer (the Home Assistant integration, the "
                            "rustuya-manager plugin or a daemon) serves the same IL prefix and source; run one", presence_topic)

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
