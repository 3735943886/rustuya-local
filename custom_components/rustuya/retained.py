"""The retained IL publications kept across Home Assistant restarts, as a broker keeps them.

The in-process transport starts empty. Replaying what it last retained, before the service starts, keeps a device's
last values (a battery sensor may not report for hours) and lets the service find devices that dropped out while Home
Assistant was down, as it finds them on a broker (`Service.start`'s previous retained topics).

A restored device is marked unavailable (its `available` role property set to false) until the service says otherwise:
the stored value is whatever it was when Home Assistant stopped.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from ildevice_ha.core.memory import InProcessTransport
    from ildevice_ha.core.transport import Message, Unsubscribe

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 30


class RetainedStore:
    def __init__(self, hass: HomeAssistant, entry_id: str, transport: InProcessTransport, prefix: str) -> None:
        self._store = _store(hass, entry_id)
        self._transport = transport
        self._prefix = prefix
        self._unsubscribe: Unsubscribe | None = None
        self._pending = False

    @staticmethod
    async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
        await _store(hass, entry_id).async_remove()

    def _device_of(self, topic: str) -> str | None:
        """The device a topic under the prefix belongs to; None for `_producer` and other reserved levels."""
        if not topic.startswith(f"{self._prefix}/"):
            return None
        device = topic[len(self._prefix) + 1:].split("/", 1)[0]
        return device if device and not device.startswith("_") else None

    async def async_restore(self) -> None:
        """Publish the stored publications into the transport, each device marked unavailable."""
        stored = await self._store.async_load() or {}
        for topic, payload in {**stored, **_unavailable(stored, self._prefix)}.items():
            await self._transport.publish(topic, payload, qos=1, retain=True)
        if stored:
            _LOGGER.debug("restored %d retained publications", len(stored))

    async def async_start(self) -> None:
        """Save whenever a device publishes."""
        self._unsubscribe = await self._transport.subscribe(f"{self._prefix}/#", self._on_message)

    async def async_stop(self) -> None:
        """Stop following and save now, so a setup that follows at once reads what this one had."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._pending = False
        await self._store.async_save(self._data())

    def _on_message(self, msg: Message) -> None:
        if not self._pending and self._device_of(msg.topic) is not None:
            self._pending = True
            self._store.async_delay_save(self._saved, SAVE_DELAY)

    def _saved(self) -> dict[str, str]:
        self._pending = False
        return self._data()

    def _data(self) -> dict[str, str]:
        data = {}
        for topic, msg in self._transport.retained.items():
            if self._device_of(topic) is not None:
                payload = msg.payload
                data[topic] = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        return data


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, str]]:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.retained")


def _unavailable(stored: dict[str, str], prefix: str) -> dict[str, str]:
    """`false` on the state topic of each stored descriptor's `available` role property."""
    from ildevice_ha.core.descriptor import DescriptorError, parse_descriptor
    from ildevice_ha.core.plan import availability_prop
    from ildevice_ha.core.topics import descriptor_id, resolve_topics, state_topic

    marks = {}
    for topic, payload in stored.items():
        if descriptor_id(topic, prefix) is None:
            continue
        try:
            desc = parse_descriptor(json.loads(payload))
        except (ValueError, DescriptorError):
            continue
        if (prop := availability_prop(desc)) is not None:
            marks[state_topic(desc, resolve_topics(desc, prefix), prop)] = "false"
    return marks
