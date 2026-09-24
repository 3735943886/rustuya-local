"""Talks rustuya-bridge's own MQTT wire protocol: its topic and payload templates are configurable
(`mqtt_event_topic`, `mqtt_command_topic`, `mqtt_payload_template`, ...), announced retained on
`{root}/bridge/config`. Interpreting them correctly needs the bridge's own parser, not a guess at its shape — so
this uses `pyrustuyabridge`'s bindings (`match_topic`, `render_template`, `tpl_to_wildcard`, `parse_seed_dps`),
which mirror the real Rust bridge's own template/payload logic byte-for-byte.

`tuya2ildevice.Hub` never sees any of this: it only takes already-decoded `Connected`/`Disconnected`/`Message`
input (via `Runner.on_bridge_message`) and emits abstract `BridgeCommand`s (via `Runner`'s `on_bridge_command`
callback, wired to `BridgeClient.send_command` here) — rendering a real bridge topic/payload is entirely this
module's job.

It also tracks which devices the bridge actually holds (its `status` reply, kept current by the `add` / `remove` /
`clear` acks and by `bridge/config` republishes), so that `sync_devices(records)` can hand the Hub only the devices
that are both in the device file and registered on the bridge: removing a device from the bridge takes it out of IL
(retained topics cleared) and adding it puts it back, instead of IL advertising the whole cloud list regardless.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import pyrustuyabridge as pb
from tuya2ildevice import BridgeCommand, Connected, Disconnected, Message
from tuya2ildevice.host import Runner
from tuya2ildevice.host.transport import Message as TransportMessage
from tuya2ildevice.host.transport import Transport, Unsubscribe

_LOGGER = logging.getLogger(__name__)

DEFAULT_EVENT = "{root}/event/{type}/{id}"
DEFAULT_MESSAGE = "{root}/{level}/{id}"
DEFAULT_COMMAND = "{root}/command"
DEFAULT_PAYLOAD = "{value}"


@dataclass(frozen=True)
class BridgeTemplates:
    root: str
    event: str
    message: str
    command: str
    payload: str


class BridgeClient:
    """One per bridge connection. `runner` is set after construction (it needs `send_command` to exist first —
    see the wiring in `cli.py` / `custom_components/rustuya_local/__init__.py`)."""

    def __init__(self, transport: Transport, root: str) -> None:
        self.transport = transport
        self.root = root
        self.runner: Runner | None = None
        self._templates: BridgeTemplates | None = None
        self._bootstrapped = asyncio.Event()
        self._event_unsub: Unsubscribe | None = None
        self._message_unsub: Unsubscribe | None = None
        self._pending: set[asyncio.Task] = set()
        # registration tracking (only when `sync_devices` was ever called; otherwise the caller owns the Hub's set)
        self._records: dict[str, dict] | None = None      # every device in the device file, by id
        self._registered: set[str] | None = None          # what the bridge holds; None until its first `status`
        self._status_accum: dict[str, Any] | None = None
        self._devices_updated_at: Any = None
        # the latest thing heard per device, replayed when a device becomes driven after it was already heard
        self._last_conn: dict[str, Connected | Disconnected] = {}
        self._last_state: dict[tuple[str, str], dict] = {}

    async def start(self, timeout: float = 3.0) -> None:
        """Subscribe to the bridge's retained config and wait for it (falling back to the default topic layout if
        the bridge never publishes one within `timeout`); after that, reconfigures keep being applied live."""
        await self.transport.subscribe(f"{self.root}/bridge/config", self._on_config)
        try:
            await asyncio.wait_for(self._bootstrapped.wait(), timeout)
        except TimeoutError:
            _LOGGER.warning("no bridge config on %s/bridge/config within %.0fs; using the default topic layout",
                            self.root, timeout)
            await self._apply_templates({})

    def send_command(self, cmd: BridgeCommand) -> None:
        """Bind this to `Runner(..., on_bridge_command=...)`. Synchronous (Hub's output dispatch is sync); the
        actual publish runs as a background task — `drain()` waits for any still in flight."""
        self._spawn(self._send_command(cmd))

    async def drain(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending))
            await asyncio.sleep(0)          # let the done-callbacks drop finished tasks (awaiting done ones never yields)

    async def _send_command(self, cmd: BridgeCommand) -> None:
        await self._publish(cmd.action, cmd.device_id, {"dps": cmd.dps} if cmd.dps is not None else {})

    async def _publish(self, action: str, device_id: str, extra: dict[str, Any]) -> None:
        if self._templates is None:
            _LOGGER.warning("dropping a bridge command for %s: no bridge config received yet", device_id)
            return
        topic = pb.render_template(self._templates.command, {"action": action, "id": device_id})
        await self.transport.publish(topic, json.dumps({"action": action, "id": device_id, **extra}), 1, False)

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    # ---- bridge/config bootstrap --------------------------------------------------------------------

    def _on_config(self, msg: TransportMessage) -> None:
        asyncio.ensure_future(self._handle_config(msg))

    async def _handle_config(self, msg: TransportMessage) -> None:
        payload = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, bytes) else msg.payload
        if not payload.strip():
            return                                          # cleared retained config (bridge offline); keep what we had
        try:
            cfg = json.loads(payload)
        except ValueError:
            _LOGGER.warning("the bridge's config on %s is not JSON", msg.topic)
            return
        await self._apply_templates(cfg)
        updated = cfg.get("devices_updated_at")
        if updated != self._devices_updated_at:
            self._devices_updated_at = updated
            if updated is not None:
                self._request_status()                      # a bridge >= 0.4 announces registry changes here

    async def _apply_templates(self, cfg: dict) -> None:
        root = cfg.get("mqtt_root_topic") or self.root

        def resolve(key: str, default: str) -> str:
            return pb.render_template(cfg.get(key) or default, {"root": root})

        templates = BridgeTemplates(root=root, event=resolve("mqtt_event_topic", DEFAULT_EVENT),
                                    message=resolve("mqtt_message_topic", DEFAULT_MESSAGE),
                                    command=resolve("mqtt_command_topic", DEFAULT_COMMAND),
                                    payload=cfg.get("mqtt_payload_template") or DEFAULT_PAYLOAD)
        if templates == self._templates:
            self._bootstrapped.set()
            return

        if self._event_unsub is not None:
            self._event_unsub()
        if self._message_unsub is not None:
            self._message_unsub()
        self._event_unsub = await self.transport.subscribe(pb.tpl_to_wildcard(templates.event, root), self._on_message)
        self._message_unsub = await self.transport.subscribe(pb.tpl_to_wildcard(templates.message, root),
                                                             self._on_message)
        self._templates = templates
        self._bootstrapped.set()
        if self._records is not None:
            self._request_status()

    # ---- which devices the bridge holds ---------------------------------------------------------------

    def sync_devices(self, records: list[dict]) -> None:
        """Make IL advertise the devices in `records` (the device file) that the bridge also holds. Call it with the
        file's contents at startup and again whenever the file changes (a `DeviceWatcher` can be pointed at this
        object, it only needs a `sync_devices(records)` method). The Hub must have been created without devices."""
        first = self._records is None
        self._records = {r["id"]: r for r in records}
        if first:
            self._request_status()
        self._reconcile()

    def _request_status(self) -> None:
        if self._templates is not None and self._records is not None:
            self._status_accum = None
            self._spawn(self._publish("status", "bridge", {}))

    def _reconcile(self) -> None:
        if self.runner is None or self._records is None or self._registered is None:
            return
        wanted = [rec for did, rec in self._records.items() if did in self._registered]
        done = self.runner.sync_devices(wanted)
        for device_id in (*done["added"], *done["changed"]):
            if (conn := self._last_conn.get(device_id)) is not None:
                self.runner.on_bridge_message(device_id, conn)
            for (did, channel), dps in self._last_state.items():
                if did == device_id:
                    self.runner.on_bridge_message(device_id, Message(channel, dps), retained=True)

    def _forget(self, device_id: str) -> None:
        self._last_conn.pop(device_id, None)
        for key in [k for k in self._last_state if k[0] == device_id]:
            del self._last_state[key]

    # ---- inbound bridge messages ---------------------------------------------------------------------

    def _on_message(self, msg: TransportMessage) -> None:
        if self.runner is None or self._templates is None:
            return
        payload = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, bytes) else msg.payload
        if (vars_ := pb.match_topic(msg.topic, self._templates.event)) is not None:
            self._on_event(vars_, payload, msg.retain)
        elif (vars_ := pb.match_topic(msg.topic, self._templates.message)) is not None:
            self._on_reply(vars_, payload, msg.retain)

    def _on_event(self, vars_: dict[str, str], payload: str, retained: bool) -> None:
        device_id = vars_.get("id")
        if not device_id:
            return
        dps = pb.parse_seed_dps(payload, vars_.get("dp"), self._templates.payload)
        if not isinstance(dps, dict):
            _LOGGER.debug("could not extract dps from event payload for %s: %r", device_id, payload)
            return
        if self._records is not None and vars_["type"] == "state":
            self._last_state[(device_id, "state")] = dps
        self.runner.on_bridge_message(device_id, Message(vars_["type"], dps), retained=retained)

    def _on_reply(self, vars_: dict[str, str], payload: str, retained: bool) -> None:
        """Everything on the message topic: a device's connection state (`errorCode`) or a reply to a command
        (`action`). An empty payload is the bridge clearing a retained message, and is ignored."""
        try:
            parsed = json.loads(payload)
        except ValueError:
            return
        if not isinstance(parsed, dict):
            return
        device_id = vars_.get("id") or parsed.get("id")
        if "errorCode" in parsed and device_id and device_id != "bridge":
            conn = Connected() if parsed.get("errorCode") == 0 else Disconnected()
            if self._records is not None:
                self._last_conn[device_id] = conn
            self.runner.on_bridge_message(device_id, conn)
            return
        if self._records is None or retained:                # a retained reply is an old one; `status` is asked live
            return
        action = parsed.get("action")
        if action == "status" and isinstance(parsed.get("devices"), dict):
            self._on_status_page(parsed)
        elif parsed.get("status") == "ok" and self._registered is not None:
            target = parsed.get("id") or device_id
            if action == "add" and target and target != "bridge":
                self._registered.add(target)
                self._reconcile()
            elif action == "remove" and target and target != "bridge":
                self._registered.discard(target)
                self._forget(target)
                self._reconcile()
            elif action == "clear" and target in (None, "all", "bridge"):
                self._registered.clear()
                self._last_conn.clear()
                self._last_state.clear()
                self._reconcile()

    def _on_status_page(self, parsed: dict) -> None:
        """`status` is paged: a page with `has_more` is followed by asking for the next offset."""
        offset = parsed.get("offset", 0)
        returned = parsed.get("returned", len(parsed["devices"]))
        if offset == 0 or self._status_accum is None:
            self._status_accum = {}
        self._status_accum.update(parsed["devices"])
        if parsed.get("has_more") and returned > 0:
            self._spawn(self._publish("status", "bridge", {"offset": offset + returned}))
            return
        self._registered = set(self._status_accum)
        self._status_accum = None
        for device_id in [d for d in self._last_conn if d not in self._registered]:
            self._forget(device_id)
        self._reconcile()
