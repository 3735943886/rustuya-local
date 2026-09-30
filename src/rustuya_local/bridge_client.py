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

`status` replies go to every client on the response topic, whoever asked, and a large fleet comes in pages. Only the
contiguous pages matching the expected offset and total are taken, one request at a time: another client's pages (rustuya-manager, the
Home Assistant config flow) used to be read as this client's, and a repeated last page then committed as the whole
list (61 devices read as the 11 of the last page), taking the rest out of IL.
"""

from __future__ import annotations

import asyncio
import functools
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
STATUS_CYCLE_TIMEOUT = 10.0      # a request whose pages stop coming is given up after this, so a lost reply cannot stall


@functools.cache
def _warn_old_pyrustuyabridge() -> None:
    """Once per process."""
    _LOGGER.warning("pyrustuyabridge %s has no render_command (0.4.0.dev2+): bridge commands keep the old form, "
                    "which misses a {dp} command topic template's single-DP form", getattr(pb, "__version__", "?"))


@dataclass(frozen=True)
class BridgeTemplates:
    root: str
    event: str
    message: str
    command: str
    payload: str


def templates_from_config(cfg: dict, root: str) -> BridgeTemplates:
    """The bridge's topic and payload templates from its retained `bridge/config` (`{}`: the default layout), with
    `{root}` rendered by the bridge's own parser."""
    root = cfg.get("mqtt_root_topic") or root

    def resolve(key: str, default: str) -> str:
        return pb.render_template(cfg.get(key) or default, {"root": root})

    return BridgeTemplates(root=root, event=resolve("mqtt_event_topic", DEFAULT_EVENT),
                           message=resolve("mqtt_message_topic", DEFAULT_MESSAGE),
                           command=resolve("mqtt_command_topic", DEFAULT_COMMAND),
                           payload=cfg.get("mqtt_payload_template") or DEFAULT_PAYLOAD)


def payload_text(payload: bytes | str) -> str:
    """An MQTT payload as text: a transport hands over bytes or str."""
    return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload


def _json_object(text: str) -> dict | None:
    """`text` as a JSON object; None for anything else (not JSON, not an object, an empty payload clearing a retained
    message)."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _link_device(vars_: dict[str, str], parsed: dict) -> str | None:
    """The device whose connection a frame on the bridge's message topic reports (its `errorCode`); None for any other
    frame (a reply to a command, the bridge's own)."""
    device_id = vars_.get("id") or parsed.get("id")
    return device_id if "errorCode" in parsed and device_id and device_id != "bridge" else None


class _BridgeFollower:
    """Follows the bridge's retained `{root}/bridge/config`: its event and message topics are subscribed to
    `_on_message`, and subscribed again where a reconfigure moves them. `start()` waits for the config, falling back to
    the default topic layout if the bridge publishes none within `timeout`."""

    _warn_without_config = True

    def __init__(self, transport: Transport, root: str) -> None:
        self.transport = transport
        self.root = root
        self._templates: BridgeTemplates | None = None
        self._ready = asyncio.Event()
        self._unsubs: list[Unsubscribe] = []               # the config's subscription
        self._topic_unsubs: list[Unsubscribe] = []         # the event and message topics' (follow the config)

    async def start(self, timeout: float = 3.0) -> None:
        self._unsubs.append(await self.transport.subscribe(f"{self.root}/bridge/config", self._on_config))
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            if self._warn_without_config:
                _LOGGER.warning("no bridge config on %s/bridge/config within %.0fs; using the default topic layout",
                                self.root, timeout)
            await self._apply_templates({})

    def stop(self) -> None:
        for unsub in (*self._topic_unsubs, *self._unsubs):
            unsub()
        self._topic_unsubs, self._unsubs = [], []

    async def _on_config(self, msg: TransportMessage) -> None:
        payload = payload_text(msg.payload)
        if not payload.strip():
            return                                          # cleared retained config (bridge offline); keep what we had
        cfg = _json_object(payload)
        if cfg is None:
            _LOGGER.warning("the bridge's config on %s is not a JSON object", msg.topic)
            return
        await self._apply_templates(cfg)
        self._config_applied(cfg)

    async def _apply_templates(self, cfg: dict) -> None:
        templates = templates_from_config(cfg, self.root)
        if templates != self._templates:
            for unsub in self._topic_unsubs:
                unsub()
            self._templates = templates         # first: a broker may hand over the retained ones during `subscribe`
            self._topic_unsubs = [
                await self.transport.subscribe(pb.tpl_to_wildcard(tpl, templates.root), self._on_message)
                for tpl in (templates.event, templates.message)]
            self._templates_changed()
        self._ready.set()

    def _config_applied(self, cfg: dict) -> None:
        """A (re)published config, after its templates are in place."""

    def _templates_changed(self) -> None:
        """The event and message topics were (re)subscribed."""

    def _on_message(self, msg: TransportMessage) -> None:
        raise NotImplementedError


class BridgeClient(_BridgeFollower):
    """One per bridge connection. `runner` is set after construction (it needs `send_command` to exist first —
    see the wiring in `service.py`)."""

    def __init__(self, transport: Transport, root: str) -> None:
        super().__init__(transport, root)
        self.runner: Runner | None = None
        self._pending: set[asyncio.Task] = set()
        # registration tracking (only when `sync_devices` was ever called; otherwise the caller owns the Hub's set)
        self._records: dict[str, dict] | None = None      # every device in the device file, by id
        self._registered: set[str] | None = None          # what the bridge holds; None until its first `status`
        self._status_accum: dict[str, Any] | None = None
        self._status_since: float | None = None            # this client's open `status` request: when it went out
        self._status_rerun = False                          # asked for again while one was open: once more when it ends
        self._status_timer: asyncio.TimerHandle | None = None
        self._status_offset = 0
        self._status_total: int | None = None
        self.on_reconcile = None
        self._devices_updated_at: Any = None
        # the latest thing heard per device, replayed when a device becomes driven after it was already heard
        self._last_conn: dict[str, Connected | Disconnected] = {}
        self._last_state: dict[str, dict] = {}

    def stop(self) -> None:
        if self._status_timer is not None:
            self._status_timer.cancel()
            self._status_timer = None
        self._status_since = None
        super().stop()

    def _arm_status_timeout(self) -> None:
        if self._status_timer is not None:
            self._status_timer.cancel()
        self._status_timer = asyncio.get_running_loop().call_later(STATUS_CYCLE_TIMEOUT, self._retry_status)

    def _retry_status(self) -> None:
        self._status_since = None
        self._request_status()

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
        request = {"action": action, "id": device_id, **extra}
        render_command = getattr(pb, "render_command", None)
        if render_command is None:                          # pyrustuyabridge < 0.4.0.dev2, e.g. an older rustuya-manager
            _warn_old_pyrustuyabridge()                     # hosting the drop-in plugin: the old form, `{dp}` left as is
            topic = pb.render_template(self._templates.command, {"action": action, "id": device_id})
            await self.transport.publish(topic, json.dumps(request), 1, False)
            return
        # the bridge's own renderer: e.g. a one-DP `set` on `{root}/command/{action}/{id}/{dp}` is the bare value on
        # `.../set/<id>/<dp>`, checked against the bridge's parser
        rendered = render_command(self._templates.command, request)
        if rendered is None:
            _LOGGER.warning("dropping a bridge %s for %s: the command topic %r cannot carry it", action, device_id,
                            self._templates.command)
            return
        topic, payload = rendered
        await self.transport.publish(topic, payload, 1, False)

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    # ---- bridge/config ------------------------------------------------------------------------------

    def _config_applied(self, cfg: dict) -> None:
        self._devices_updated_at = cfg.get("devices_updated_at")
        # Config is replayed after reconnect, even when the timestamp did not change.
        self._request_status()

    def _templates_changed(self) -> None:
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
        if self._templates is None or self._records is None:
            return
        now = asyncio.get_running_loop().time()
        if self._status_since is not None and now - self._status_since < STATUS_CYCLE_TIMEOUT:
            self._status_rerun = True                       # one request at a time; its pages would be mixed up
            return
        self._status_since, self._status_rerun, self._status_accum = now, False, None
        self._status_offset, self._status_total = 0, None
        self._arm_status_timeout()
        self._spawn(self._publish("status", "bridge", {}))

    def _reconcile(self) -> None:
        if self.runner is None or self._records is None or self._registered is None:
            return
        wanted = [rec for did, rec in self._records.items() if did in self._registered]
        if self.on_reconcile is not None:
            self.on_reconcile({rec["id"] for rec in wanted})
        self.runner.sync_devices(wanted, seed=self._seed)

    def _seed(self, device_id: str) -> list:
        """What this client already heard of a device the Hub starts driving: its link state, then (unless the link is
        down) its retained `state`, handed over before anything is published — so a device that is up goes out up,
        with its values, and a restart shows il consumers no `available: false` (an event entity coming back from
        `unavailable` looks like a press to an automation that follows its state). A sub-device has no link state of
        its own, only its snapshot."""
        conn = self._last_conn.get(device_id)
        seed: list = [conn] if conn is not None else []
        state = self._last_state.get(device_id)
        if state and not isinstance(conn, Disconnected):
            seed.append(Message("state", state))
        return seed

    def _forget(self, device_id: str) -> None:
        self._last_conn.pop(device_id, None)
        self._last_state.pop(device_id, None)

    # ---- inbound bridge messages ---------------------------------------------------------------------

    def _on_message(self, msg: TransportMessage) -> None:
        if self.runner is None or self._templates is None:
            return
        payload = payload_text(msg.payload)
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
        if vars_["type"] == "state":             # kept even before `sync_devices`: retained ones may come first
            # merged: with a `{dp}` in the event topic every retained message carries one dp
            self._last_state[device_id] = {**self._last_state.get(device_id, {}), **dps}
        self.runner.on_bridge_message(device_id, Message(vars_["type"], dps), retained=retained)

    def _on_reply(self, vars_: dict[str, str], payload: str, retained: bool) -> None:
        """Everything on the message topic: a device's connection state (`errorCode`) or a reply to a command
        (`action`). An empty payload is the bridge clearing a retained message, and is ignored."""
        parsed = _json_object(payload)
        if parsed is None:
            return
        if (device_id := _link_device(vars_, parsed)) is not None:
            conn = Connected() if parsed.get("errorCode") == 0 else Disconnected()
            self._last_conn[device_id] = conn
            self.runner.on_bridge_message(device_id, conn, retained=retained)       # a retained one asks for no `get`
            return
        if self._records is None or retained:                # a retained reply is an old one; `status` is asked live
            return
        if parsed.get("action") == "status":
            if isinstance(parsed.get("devices"), dict):
                self._on_status_page(parsed)
        elif parsed.get("status") == "ok" and self._registered is not None:
            self._on_ack(parsed.get("action"), parsed.get("id") or vars_.get("id"))

    def _on_ack(self, action: str | None, target: str | None) -> None:
        """The bridge took an `add`, `remove` or `clear` (anyone's): what it holds changed."""
        if action == "clear" and target in (None, "all", "bridge"):
            self._registered.clear()
            self._last_conn.clear()
            self._last_state.clear()
        elif not target or target == "bridge":
            return
        elif action == "add":
            self._registered.add(target)
        elif action == "remove":
            self._registered.discard(target)
            self._forget(target)
        else:
            return
        self._reconcile()

    def _on_status_page(self, parsed: dict) -> None:
        """`status` is paged: a page with `has_more` is followed by asking for the next offset. Pages that arrive while
        this client has no request open answer someone else's, and are left alone."""
        offset = parsed.get("offset", 0)
        now = asyncio.get_running_loop().time()
        if self._status_since is None or now - self._status_since >= STATUS_CYCLE_TIMEOUT:
            _LOGGER.debug("status page (offset %s) of another client's request; ignored", offset)
            return
        # The wire protocol has no request ID. Accept only the expected contiguous page,
        # and verify a stable total and disjoint IDs before committing a snapshot.
        devices = parsed["devices"]
        returned = parsed.get("returned", len(devices))
        total = parsed.get("device_count")
        more = parsed.get("has_more", False)
        if type(offset) is not int or offset != self._status_offset:
            return
        if (type(returned) is not int or returned != len(devices)
                or type(more) is not bool or (more and not returned)
                or (total is not None and (type(total) is not int or total < offset + returned))):
            return
        if self._status_accum is None:
            self._status_accum = {}
            self._status_total = total
        if total != self._status_total or self._status_accum.keys() & devices.keys():
            return
        if total is not None and more != (offset + returned < total):
            return
        self._status_since = now
        self._status_accum.update(devices)
        self._status_offset += returned
        if more:
            self._arm_status_timeout()
            self._spawn(self._publish("status", "bridge", {"offset": self._status_offset}))
            return
        if self._status_timer is not None:
            self._status_timer.cancel()
            self._status_timer = None
        before = self._registered
        self._registered = set(self._status_accum)
        self._status_accum, self._status_since = None, None
        if before != self._registered:
            _LOGGER.info("the bridge holds %d device(s)", len(self._registered))
        if self._status_rerun:
            self._request_status()
        for device_id in [d for d in self._last_conn if d not in self._registered]:
            self._forget(device_id)
        self._reconcile()


# ---- link state, for a status view ------------------------------------------------------------------------------

_ERROR_ENVELOPE = frozenset({"errorCode", "errorMsg", "payloadStr", "errorPayloadObj", "payloadRaw"})


def error_text(parsed: dict) -> str:
    """A bridge error frame as one line, as rustuya-manager shows it: `errorMsg`, then any scalar extras
    (`reason=ip_mismatch, configured=...`)."""
    base = parsed.get("errorMsg") or parsed.get("payloadStr") or ""
    extras = ", ".join(f"{k}={v}" for k, v in parsed.items()
                       if k not in _ERROR_ENVELOPE and v is not None and not isinstance(v, (dict, list)))
    return f"{base} ({extras})" if base and extras else str(base or extras)


class LinkWatcher(_BridgeFollower):
    """Each device's connection as the bridge reports it, for a status view: the `errorCode` frames on the bridge's
    message topic (retained, so every device the bridge holds answers on subscribe), and a live event as proof of
    life, like rustuya-manager's live status. Topics come from the bridge's config, as `BridgeClient` follows them. It
    only listens; `on_change(device_id, link)` gets `{"online": bool, "code": int | None, "message": str}`."""

    _warn_without_config = False

    def __init__(self, transport: Transport, root: str, on_change: Any = None) -> None:
        super().__init__(transport, root)
        self.on_change = on_change
        self.links: dict[str, dict[str, Any]] = {}

    def _on_message(self, msg: TransportMessage) -> None:
        t = self._templates
        if t is None:
            return
        if (vars_ := pb.match_topic(msg.topic, t.message)) is not None:
            parsed = _json_object(payload_text(msg.payload))
            if parsed is not None and (device_id := _link_device(vars_, parsed)) is not None:
                code = parsed.get("errorCode")
                self._set(device_id, {"online": code == 0, "code": code,
                                      "message": "" if code == 0 else error_text(parsed)})
        elif not msg.retain and (vars_ := pb.match_topic(msg.topic, t.event)) is not None and vars_.get("id"):
            if not self.links.get(vars_["id"], {}).get("online"):
                self._set(vars_["id"], {"online": True, "code": 0, "message": ""})     # live data: it is connected

    def _set(self, device_id: str, link: dict[str, Any]) -> None:
        if self.links.get(device_id) == link:
            return
        self.links[device_id] = link
        if self.on_change is not None:
            self.on_change(device_id, link)
