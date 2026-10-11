"""Run the host-owned `Hub` on the IL transport (il-ha or any IL consumer). The Hub maps Rust driver data to host actions;
this runner executes its `Publish`, `Schedule` and `Unschedule` outputs. It knows nothing about rustuya-bridge's own MQTT wire, either
— Hub's bridge-facing `BridgeCommand` outputs (asking the bridge to read/write a device) are handed to
`on_bridge_command`, supplied by whatever owns the real bridge connection (e.g. rustuya-local's bridge client). A
setting written through IL (`SaveSettings`) goes to `on_settings`, e.g. `OverrideWatcher.save_settings`."""

from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Sequence

from tuya2ildevice import Connected, Disconnected
from tuya2ildevice import Message as DriverInput

from rustuya_local.host.il import (
    IL,
    BridgeCommand,
    Publish,
    SaveSettings,
    Schedule,
    Unschedule,
)

from ..async_tools import cancel_task
from .hub import Hub
from .transport import Message, Transport, Unsubscribe

_LOGGER = logging.getLogger(__name__)


def _text(payload: bytes | str) -> str:
    return payload.decode() if isinstance(payload, (bytes, bytearray)) else payload


async def producer_running(il: Transport, presence: str, timeout: float = 2.0) -> bool | None:
    """Is a producer serving `presence` (`<prefix>/_producer/<source>`) right now? Call it before starting one.

    False: the presence topic is not `online`. True: it is, and a running `Runner` answered a probe on
    `<presence>/probe` (on `<presence>/alive`, neither retained; below `_producer/+`, so IL consumers do not see them).
    None: it is `online` but nothing answered: a producer whose Last Will never reached the broker (a power cut that
    took the broker along), or one on tuya2ildevice before 0.3.5, which does not answer."""
    seen: list[str] = []
    unsub = await il.subscribe(presence, lambda m: seen.append(_text(m.payload)) if m.retain else None)
    try:
        for _ in range(10):                        # the retained replay, if any
            if seen:
                break
            await asyncio.sleep(0.05)
    finally:
        unsub()
    if not seen or seen[-1] != "online":
        return False
    nonce = secrets.token_hex(8)
    answered = asyncio.Event()
    unsub = await il.subscribe(f"{presence}/alive",
                               lambda m: answered.set() if not m.retain and _text(m.payload) == nonce else None)
    try:
        await il.publish(f"{presence}/probe", nonce, 1, False)
        try:
            await asyncio.wait_for(answered.wait(), timeout)
        except TimeoutError:
            return None
        return True
    finally:
        unsub()


class Runner:
    """`on_bridge_command(command)`: carry out a `BridgeCommand` on the real bridge. `on_settings(device_id, block)`:
    keep a device's settings block (`SaveSettings`) and reload the overrides with it, e.g.
    `OverrideWatcher.save_settings`; a coroutine function runs as a task, which `drain()` waits for."""

    def __init__(self, hub: Hub, il: Transport, clock: Callable[[], float] = time.time, *,
                 on_bridge_command: Callable[[BridgeCommand], None] | None = None,
                 on_settings: Callable[[str, dict], Awaitable[None] | None] | None = None) -> None:
        self.hub = hub
        self.il = il
        self.clock = clock
        self._on_bridge_command = on_bridge_command
        self._on_settings = on_settings
        self._tasks: set[asyncio.Task] = set()
        self._unsubs: list[Unsubscribe] = []
        self._timers: dict[tuple[str, str], asyncio.TimerHandle] = {}
        self._out: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._presence_unsub: Unsubscribe | None = None
        self._accepting = False
        self._failed: dict[str, dict] = {}         # records sync_devices could not drive, tried again on reload

    async def start(self) -> None:
        """Subscribe on the IL side, publish presence and every descriptor (retained)."""
        if self._worker is not None:
            return
        self._worker = asyncio.create_task(self._publish_loop())
        self._accepting = True
        try:
            for sub in self.hub.subscriptions():
                self._unsubs.append(await self.il.subscribe(sub.topic, self._on_il, qos=sub.qos))
            # Answer a replaced producer's offline message while this Runner is active.
            self._presence_unsub = await self.il.subscribe(self.hub.il.presence, self._on_presence)
            self._unsubs.append(await self.il.subscribe(f"{self.hub.il.presence}/probe", self._on_probe))
            hooks = getattr(self.il, "on_connect", None)
            if hooks is not None:
                hooks.append(self._reconnected)
            self.run(self.hub.start(now=self.clock()))
        except BaseException:
            await self.stop(offline=False)
            raise

    async def stop(self, offline: bool = True) -> None:
        """Presence goes offline, the queue drains, then subscriptions and timers are released. `offline=False` leaves
        the presence `online` for a host that starts again at once (a restart): il consumers then see no gap."""
        if self._worker is None:                   # not started, or already stopped
            return
        self._accepting = False
        hooks = getattr(self.il, "on_connect", None)
        if hooks is not None and self._reconnected in hooks:
            hooks.remove(self._reconnected)
        if self._presence_unsub is not None:       # first: this one's own `offline` is not to be answered
            self._presence_unsub()
            self._presence_unsub = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        self._cancel_timers()
        try:
            if offline:
                self.run(self.hub.stop())
            await self.drain()
        finally:
            # A settings reload may have scheduled timers while draining.
            self._cancel_timers()
            worker, self._worker = self._worker, None
            await cancel_task(worker)

    async def drain(self) -> None:
        while self._tasks:                         # settings being saved: their reload publishes too
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._out.join()

    # ---- runtime device changes (each returns nothing; the Hub's outputs are executed) --------------

    def set_device(self, device: dict, seed: Sequence[Connected | Disconnected | DriverInput] = ()) -> None:
        self.run(self.hub.set_device(device, seed, self.clock()))

    def remove_device(self, device_id: str) -> None:
        self.run(self.hub.remove_device(device_id))

    def sync_devices(self, records: list[dict], seed: Callable[[str], Sequence] | None = None) -> dict[str, list[str]]:
        """Make the driven devices exactly `records`: add the new, replace the changed, remove the gone. A record the
        Hub cannot drive is reported and skipped, the others go on; `reload` tries it again (an override that
        refused it may have been fixed). `seed(device_id)`: what the host already knows of a device it adds or
        replaces (see `Hub.set_device`). Returns what was done, by kind."""
        wanted = {r["id"]: r for r in records}
        have = self.hub.records
        done: dict[str, list[str]] = {"added": [], "changed": [], "removed": [], "failed": []}
        self._failed = {}
        for device_id in have.keys() - wanted.keys():
            self.remove_device(device_id)
            done["removed"].append(device_id)
        for device_id, record in wanted.items():
            if have.get(device_id) == record:
                continue
            try:
                self.set_device(record, seed(device_id) if seed is not None else ())
            except Exception:
                _LOGGER.exception("cannot drive device %s", device_id)
                done["failed"].append(device_id)
                self._failed[device_id] = record
                continue
            done["changed" if device_id in have else "added"].append(device_id)
        return done

    def reload(self, overrides: dict | None, *, rules: dict | None = None) -> None:
        self.run(self.hub.reload(overrides, rules=rules, now=self.clock()))
        failed, self._failed = self._failed, {}
        for device_id, record in failed.items():
            try:
                self.set_device(record)
            except Exception:
                _LOGGER.exception("still cannot drive device %s", device_id)
                self._failed[device_id] = record
            else:
                _LOGGER.info("device %s is driven now", device_id)

    # ---- plumbing ----------------------------------------------------------------------

    def run(self, outs: list) -> None:
        for p in outs:
            if isinstance(p, Schedule):
                self._cancel(p.device_id, p.name)
                self._timers[(p.device_id, p.name)] = asyncio.get_running_loop().call_later(
                    p.after, self._fire, p.device_id, p.name)
            elif isinstance(p, Unschedule):
                self._cancel(p.device_id, p.name)
            elif isinstance(p, BridgeCommand):
                if self._on_bridge_command is not None:
                    self._on_bridge_command(p)
            elif isinstance(p, SaveSettings):
                if self._on_settings is not None:
                    self._save(p)
            else:
                self._out.put_nowait(p)

    def _save(self, p: SaveSettings) -> None:
        try:
            out = self._on_settings(p.device_id, p.block)
        except Exception:
            _LOGGER.exception("could not save the settings of %s", p.device_id)
            return
        if inspect.isawaitable(out):
            task = asyncio.create_task(self._saving(p.device_id, out))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _saving(device_id: str, out: Awaitable) -> None:
        try:
            await out
        except Exception:
            _LOGGER.exception("could not save the settings of %s", device_id)

    def _cancel(self, device_id: str, name: str) -> None:
        if handle := self._timers.pop((device_id, name), None):
            handle.cancel()

    def _cancel_timers(self) -> None:
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()

    def _fire(self, device_id: str, name: str) -> None:
        self._timers.pop((device_id, name), None)
        self._guard(lambda: self.hub.on_timer(self.clock(), device_id, name))

    def _on_presence(self, msg: Message) -> None:
        if msg.retain or _text(msg.payload) != "offline" or self._presence_unsub is None:
            return                                 # a replay on subscribe is older than this one's own `online`
        _LOGGER.info("%s went offline while this producer runs (another one stopped): online again", msg.topic)
        self.run([self.hub.presence(True)])

    def _on_probe(self, msg: Message) -> None:
        if not msg.retain and self._presence_unsub is not None:   # `producer_running` of one about to start
            self.run([Publish(IL, f"{self.hub.il.presence}/alive", _text(msg.payload), False, 1)])

    def _on_il(self, msg: Message) -> None:
        self._guard(lambda: self.hub.on_il(self.clock(), msg.topic, msg.payload, msg.retain))

    def on_bridge_message(self, device_id: str, inp: Connected | Disconnected | DriverInput,
                           retained: bool = False) -> None:
        """The bridge client (whoever decoded a real rustuya-bridge message into one of these — e.g. rustuya-local's
        `pyrustuyabridge`-based bridge client) calls this instead of touching `hub`/`run` directly, so a bad message
        is isolated the same way `_on_il` isolates one (see `_guard`)."""
        self._guard(lambda: self.hub.on_bridge_message(self.clock(), device_id, inp, retained))

    def _guard(self, produce: Callable[[], list]) -> None:
        if not self._accepting:
            return
        try:
            self.run(produce())
        except Exception:  # a bad message must not stop the runner
            _LOGGER.exception("could not handle a message")

    def _reconnected(self) -> None:
        if self._accepting:
            self.run(self.hub.start(now=self.clock()))             # restore presence and descriptors after reconnect

    async def _publish_loop(self) -> None:
        while True:
            p: Publish = await self._out.get()
            try:
                await self.il.publish(p.topic, p.payload, p.qos, p.retain)
            except Exception:
                _LOGGER.exception("publish to %s failed", p.topic)
            finally:
                self._out.task_done()
