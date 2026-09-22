"""Talks rustuya-bridge's own MQTT wire protocol: its topic and payload templates are configurable
(`mqtt_event_topic`, `mqtt_command_topic`, `mqtt_payload_template`, ...), announced retained on
`{root}/bridge/config`. Interpreting them correctly needs the bridge's own parser, not a guess at its shape — so
this uses `pyrustuyabridge`'s bindings (`match_topic`, `render_template`, `tpl_to_wildcard`, `parse_seed_dps`),
which mirror the real Rust bridge's own template/payload logic byte-for-byte.

`tuya2ildevice.Hub` never sees any of this: it only takes already-decoded `Connected`/`Disconnected`/`Message`
input (via `Runner.on_bridge_message`) and emits abstract `BridgeCommand`s (via `Runner`'s `on_bridge_command`
callback, wired to `BridgeClient.send_command` here) — rendering a real bridge topic/payload is entirely this
module's job.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import pyrustuyabridge as pb
from tuya2ildevice import BridgeCommand, Connected, Disconnected, Message
from tuya2ildevice.host import Runner
from tuya2ildevice.host.transport import Message as TransportMessage, Transport, Unsubscribe

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
        self._error_template: str | None = None
        self._bootstrapped = asyncio.Event()
        self._event_unsub: Unsubscribe | None = None
        self._error_unsub: Unsubscribe | None = None
        self._pending: set[asyncio.Task] = set()

    async def start(self, timeout: float = 3.0) -> None:
        """Subscribe to the bridge's retained config and wait for it (falling back to the default topic layout if
        the bridge never publishes one within `timeout`); after that, reconfigures keep being applied live."""
        await self.transport.subscribe(f"{self.root}/bridge/config", self._on_config)
        try:
            await asyncio.wait_for(self._bootstrapped.wait(), timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning("no bridge config on %s/bridge/config within %.0fs; using the default topic layout",
                            self.root, timeout)
            await self._apply_templates({})

    def send_command(self, cmd: BridgeCommand) -> None:
        """Bind this to `Runner(..., on_bridge_command=...)`. Synchronous (Hub's output dispatch is sync); the
        actual publish runs as a background task — `drain()` waits for any still in flight."""
        task = asyncio.ensure_future(self._send_command(cmd))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending))

    async def _send_command(self, cmd: BridgeCommand) -> None:
        if self._templates is None:
            _LOGGER.warning("dropping a bridge command for %s: no bridge config received yet", cmd.device_id)
            return
        topic = pb.render_template(self._templates.command, {"action": cmd.action, "id": cmd.device_id})
        payload = {"action": cmd.action, "id": cmd.device_id}
        if cmd.dps is not None:
            payload["dps"] = cmd.dps
        await self.transport.publish(topic, json.dumps(payload), 1, False)

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

        error_template = pb.render_template(templates.message, {"level": "error"})
        if self._event_unsub is not None:
            self._event_unsub()
        if self._error_unsub is not None:
            self._error_unsub()
        self._event_unsub = await self.transport.subscribe(pb.tpl_to_wildcard(templates.event, root), self._on_message)
        self._error_unsub = await self.transport.subscribe(pb.tpl_to_wildcard(error_template, root), self._on_message)
        self._templates, self._error_template = templates, error_template
        self._bootstrapped.set()

    # ---- inbound bridge messages ---------------------------------------------------------------------

    def _on_message(self, msg: TransportMessage) -> None:
        if self.runner is None or self._templates is None:
            return
        payload = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, bytes) else msg.payload
        if (vars_ := pb.match_topic(msg.topic, self._templates.event)) is not None:
            self._on_event(vars_, payload, msg.retain)
        elif (vars_ := pb.match_topic(msg.topic, self._error_template)) is not None:
            self._on_error(vars_, payload)

    def _on_event(self, vars_: dict[str, str], payload: str, retained: bool) -> None:
        device_id = vars_.get("id")
        if not device_id:
            return
        dps = pb.parse_seed_dps(payload, vars_.get("dp"), self._templates.payload)
        if not isinstance(dps, dict):
            _LOGGER.debug("could not extract dps from event payload for %s: %r", device_id, payload)
            return
        self.runner.on_bridge_message(device_id, Message(vars_["type"], dps), retained=retained)

    def _on_error(self, vars_: dict[str, str], payload: str) -> None:
        device_id = vars_.get("id")
        if not device_id:
            return
        try:
            code = json.loads(payload).get("errorCode")
        except (ValueError, AttributeError):
            return
        self.runner.on_bridge_message(device_id, Connected() if code == 0 else Disconnected())
