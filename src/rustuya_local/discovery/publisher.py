"""Home Assistant MQTT discovery for IL devices, live: an IL consumer that needs no Home Assistant integration.

It follows the retained descriptors under `<il_prefix>/+` and keeps a retained discovery config for each planned
entity (`render.py`), clearing the ones a device no longer has. Home Assistant then reads and writes the IL topics
itself; this only has to carry the few commands Home Assistant's MQTT platforms send on one topic for what IL keeps
apart (the relay routes), and republish the JSON document a vacuum reads (the mirrors).

Owned configs are the retained topics under `<discovery_prefix>/+/<node_id>/+/config`: after `sweep()` the ones no
current device produces are cleared, so a device removed while this was not running does not linger. Nothing here
imports Home Assistant; it runs on any `Transport` (paho, in-process).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ildevice.core import DescriptorError, parse_descriptor
from tuya2ildevice.host.transport import Message, Transport, Unsubscribe

from .render import Mirror, Options, Rendered, Route, render

_LOGGER = logging.getLogger(__name__)


def _text(payload: str | bytes) -> str:
    return payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else payload


class DiscoveryPublisher:
    """`il`: the IL broker. `ha`: Home Assistant's broker, where configs, relays and mirrors live (the IL one when
    None). `options`: prefixes and the node id."""

    def __init__(self, il: Transport, ha: Transport | None = None, options: Options | None = None) -> None:
        self.il = il
        self.ha = ha or il
        self.options = options or Options()
        self.devices: dict[str, Rendered] = {}        # device id -> what it rendered to
        self.owned: set[str] = set()                   # our config topics seen retained on the broker
        self._routes: dict[str, Route] = {}
        self._mirror_sources: dict[str, str] = {}      # IL state topic -> latest raw payload
        self._mirror_subs: dict[str, Unsubscribe] = {}
        self._unsubs: list[Unsubscribe] = []
        self._out: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    # ---- lifecycle ---------------------------------------------------------------------------------

    async def start(self) -> None:
        o = self.options
        self._worker = asyncio.ensure_future(self._publish_loop())
        self._unsubs.append(await self.ha.subscribe(f"{o.discovery_prefix}/+/{o.node_id}/+/config", self._on_owned))
        self._unsubs.append(await self.il.subscribe(f"{o.il_prefix}/+", self._on_descriptor))
        self._unsubs.append(await self.ha.subscribe(f"{o.relay_prefix}/+/+/+", self._on_relay))

    async def stop(self) -> None:
        """Stop following; the configs stay retained (availability says when the producer is gone)."""
        if self._worker is None:
            return
        await self.drain()
        for unsub in [*self._unsubs, *self._mirror_subs.values()]:
            unsub()
        self._unsubs, self._mirror_subs = [], {}
        self._worker.cancel()
        self._worker = None

    async def drain(self) -> None:
        await self._out.join()

    def sweep(self) -> list[str]:
        """Clear the owned configs no current device produces (call it once the retained descriptors have arrived).
        Returns the topics cleared."""
        wanted = {t for r in self.devices.values() for t in r.configs}
        stale = sorted(self.owned - wanted)
        for topic in stale:
            self._send(self.ha, topic, "", retain=True)
            self.owned.discard(topic)
        return stale

    def clear_all(self) -> list[str]:
        """Clear every owned config and forget the devices (entities disappear from Home Assistant)."""
        topics = sorted(self.owned | {t for r in self.devices.values() for t in r.configs})
        for topic in topics:
            self._send(self.ha, topic, "", retain=True)
        self.owned.clear()
        self.devices.clear()
        self._routes.clear()
        return topics

    # ---- inbound -------------------------------------------------------------------------------------

    def _on_owned(self, msg: Message) -> None:
        if _text(msg.payload):
            self.owned.add(msg.topic)
        else:
            self.owned.discard(msg.topic)

    def _on_descriptor(self, msg: Message) -> None:
        device_id = msg.topic.rsplit("/", 1)[-1]
        if device_id.startswith("_"):                  # `<il_prefix>/_producer/...` is presence, not a device
            return
        text = _text(msg.payload)
        if not text.strip():
            self._remove(device_id)
            return
        try:
            desc = parse_descriptor(json.loads(text))
        except (ValueError, DescriptorError) as e:
            _LOGGER.warning("descriptor on %s not used: %s", msg.topic, e)
            return
        if desc.id != device_id:
            _LOGGER.warning("descriptor on %s names another device (%s); not used", msg.topic, desc.id)
            return
        try:
            new = render(desc, self.options)
        except Exception:                               # one odd device must not stop the others
            _LOGGER.exception("cannot render discovery for %s", device_id)
            return
        self._apply(device_id, new)

    def _on_relay(self, msg: Message) -> None:
        if msg.retain:                                  # an old command replayed by the broker (il-messages.md W-12)
            return
        route = self._routes.get(msg.topic)
        if route is None:
            return
        for w in route.writes(_text(msg.payload), self._mirror_sources):
            self._send(self.il, w.topic, w.payload, retain=False)

    def _on_mirror_source(self, msg: Message) -> None:
        self._mirror_sources[msg.topic] = _text(msg.payload)
        for rendered in self.devices.values():
            for m in rendered.mirrors:
                if msg.topic in m.sources():
                    self._publish_mirror(m)

    # ---- applying a device ------------------------------------------------------------------------------

    def _apply(self, device_id: str, new: Rendered) -> None:
        old = self.devices.get(device_id, Rendered())
        for topic in sorted(old.configs.keys() - new.configs.keys()):
            self._send(self.ha, topic, "", retain=True)
            self.owned.discard(topic)
        for topic, cfg in sorted(new.configs.items()):
            if old.configs.get(topic) != cfg:
                self._send(self.ha, topic, json.dumps(cfg, sort_keys=True), retain=True)
            self.owned.add(topic)
        for topic in old.routes.keys() - new.routes.keys():
            self._routes.pop(topic, None)
        self._routes.update(new.routes)
        for m in old.mirrors:
            if m.topic not in {n.topic for n in new.mirrors}:
                self._send(self.ha, m.topic, "", retain=True)
        self.devices[device_id] = new
        self._follow_mirrors()
        for m in new.mirrors:
            self._publish_mirror(m)

    def _remove(self, device_id: str) -> None:
        old = self.devices.pop(device_id, None)
        if old is None:
            return
        for topic in sorted(old.configs):
            self._send(self.ha, topic, "", retain=True)
            self.owned.discard(topic)
        for topic in old.routes:
            self._routes.pop(topic, None)
        for m in old.mirrors:
            self._send(self.ha, m.topic, "", retain=True)
        self._follow_mirrors()

    def _follow_mirrors(self) -> None:
        wanted = {topic for r in self.devices.values() for m in r.mirrors for topic in m.sources()}
        for topic in list(self._mirror_subs.keys() - wanted):
            self._mirror_subs.pop(topic)()
            self._mirror_sources.pop(topic, None)
        for topic in wanted - self._mirror_subs.keys():
            self._mirror_subs[topic] = lambda: None          # placeholder until the subscription is in place
            self._spawn_subscribe(topic)

    def _spawn_subscribe(self, topic: str) -> None:
        async def go() -> None:
            unsub = await self.il.subscribe(topic, self._on_mirror_source)
            if topic in self._mirror_subs:
                self._mirror_subs[topic] = unsub
            else:
                unsub()
        self._out.put_nowait(go)

    def _publish_mirror(self, m: Mirror) -> None:
        self._send(self.ha, m.topic, m.payload(self._mirror_sources), retain=True)

    # ---- ordered output ---------------------------------------------------------------------------------

    def _send(self, transport: Transport, topic: str, payload: str, retain: bool) -> None:
        async def go() -> None:
            await transport.publish(topic, payload, 1, retain)
        self._out.put_nowait(go)

    async def _publish_loop(self) -> None:
        while True:
            job = await self._out.get()
            try:
                await job()
            except Exception:  # a failed publish must not stop the ones after it
                _LOGGER.exception("discovery publish failed")
            finally:
                self._out.task_done()


def expected_configs(descriptors: dict[str, Any], options: Options | None = None) -> dict[str, dict]:
    """The configs a set of retained descriptors (`{topic: payload}`) should produce, for `status` and `preview`."""
    o = options or Options()
    out: dict[str, dict] = {}
    for topic, payload in descriptors.items():
        device_id = topic.rsplit("/", 1)[-1]
        text = _text(payload)
        if device_id.startswith("_") or not text.strip():
            continue
        try:
            out.update(render(parse_descriptor(json.loads(text)), o).configs)
        except (ValueError, DescriptorError):
            continue
    return out
