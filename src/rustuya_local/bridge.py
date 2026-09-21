"""What rustuya-bridge says about itself: it publishes its configuration, retained, on `{root}/bridge/config`. The topic
templates in it (they can be changed on the bridge) tell the runner where events and commands really are."""

from __future__ import annotations

import asyncio
import json
import logging

from il_ha.core.transport import Message, Transport

_LOGGER = logging.getLogger(__name__)

_KEYS = {"event": "mqtt_event_topic", "message": "mqtt_message_topic", "command": "mqtt_command_topic"}


def topic_templates(config: dict, root: str) -> tuple[str, dict[str, str]]:
    """(root, keyword arguments for `tuya2ildevice.BridgeTopics`) from a bridge config document. The bridge may run on
    another root than the one asked for; what it says wins."""
    root = config.get("mqtt_root_topic") or root
    return root, {name: config[key] for name, key in _KEYS.items() if config.get(key)}


async def read_bridge_config(transport: Transport, root: str, timeout: float = 3.0) -> dict | None:
    """The retained `{root}/bridge/config`, or None if the bridge has not published one within `timeout` (it is
    not running yet, or it is an older bridge): the caller then uses the default layout."""
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_message(msg: Message) -> None:
        if got.done():
            return
        try:
            body = json.loads(msg.payload)
        except ValueError:
            _LOGGER.warning("the bridge's config on %s is not JSON", msg.topic)
            return
        if isinstance(body, dict):
            got.set_result(body)

    unsub = await transport.subscribe(f"{root}/bridge/config", on_message)
    try:
        return await asyncio.wait_for(got, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        unsub()
