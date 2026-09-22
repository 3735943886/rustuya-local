"""Rustuya Local as a Home Assistant integration: runs the same `tuya2ildevice.host.Runner` the standalone
`rustuya-local` daemon does, as a background task tied to this config entry, with an optional embedded
rustuya-bridge (`pyrustuyabridge`). It creates no entities and does not depend on il-ha: it is only an IL
*producer* — install il-ha (or any IL consumer) separately to turn its devices into entities.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    BRIDGE_EMBEDDED,
    CONF_ALLOW_HAZARDOUS,
    CONF_BRIDGE_LOG_LEVEL,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_BRIDGE_STATE_FILE,
    CONF_BROKER_HOST,
    CONF_BROKER_PASSWORD,
    CONF_BROKER_PORT,
    CONF_BROKER_USERNAME,
    CONF_DEVICES_PATH,
    CONF_EXPOSE_UNUSED,
    CONF_IL_PREFIX,
    CONF_IL_SOURCE,
    CONF_WATCH_INTERVAL,
    DEFAULT_WATCH_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeData:
    runner: Any
    watcher: Any | None
    bridge_transport: Any
    il_transport: Any
    embedded_bridge: Any | None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from tuya2ildevice import BridgeTopics, Hub, IlTopics
    from tuya2ildevice.host import DeviceWatcher, MqttTransport, Runner, load_devices, read_bridge_config

    from .bridge_supervisor import EmbeddedBridge

    data, options = entry.data, entry.options
    broker = f"mqtt://{data[CONF_BROKER_HOST]}:{data[CONF_BROKER_PORT]}"
    username, password = data.get(CONF_BROKER_USERNAME) or None, data.get(CONF_BROKER_PASSWORD) or None

    # a failure partway through (a later transport, the device file, Runner.start) must not leak what already
    # connected — each `push_async_callback` only runs if everything after it is unwound because of an exception,
    # or immediately by `stack.pop_all()` never being reached
    async with contextlib.AsyncExitStack() as stack:
        embedded_bridge = None
        if data[CONF_BRIDGE_MODE] == BRIDGE_EMBEDDED:
            embedded_bridge = EmbeddedBridge(broker, data[CONF_BRIDGE_ROOT], data[CONF_BRIDGE_STATE_FILE],
                                             data.get(CONF_BRIDGE_LOG_LEVEL, "warn"), username, password)
            await embedded_bridge.start()
            stack.push_async_callback(embedded_bridge.stop)

        bridge_transport = MqttTransport(data[CONF_BROKER_HOST], data[CONF_BROKER_PORT],
                                         client_id=f"rustuya_local-bridge-{entry.entry_id[:8]}",
                                         username=username, password=password)
        await bridge_transport.connect()
        stack.push_async_callback(bridge_transport.close)

        found = await read_bridge_config(bridge_transport, data[CONF_BRIDGE_ROOT])
        if found is None:
            _LOGGER.warning("no configuration from the bridge on %s/bridge/config; using the default topic layout",
                            data[CONF_BRIDGE_ROOT])
        topics = BridgeTopics.from_config(found or {}, data[CONF_BRIDGE_ROOT])

        devices = await hass.async_add_executor_job(load_devices, data[CONF_DEVICES_PATH])
        hub = Hub(devices, bridge=topics, il=IlTopics(data.get(CONF_IL_PREFIX, "il"), data.get(CONF_IL_SOURCE, "tuya")),
                 allow_hazardous=options.get(CONF_ALLOW_HAZARDOUS, False),
                 expose_unused=options.get(CONF_EXPOSE_UNUSED, False))
        will = hub.presence(False)
        il_transport = MqttTransport(data[CONF_BROKER_HOST], data[CONF_BROKER_PORT],
                                     client_id=f"rustuya_local-il-{entry.entry_id[:8]}", username=username,
                                     password=password, will=(will.topic, will.payload, will.qos, will.retain))
        await il_transport.connect()
        stack.push_async_callback(il_transport.close)

        runner = Runner(hub, bridge_transport, il_transport)
        await runner.start()
        stack.push_async_callback(runner.stop)

        watcher = None
        interval = options.get(CONF_WATCH_INTERVAL, DEFAULT_WATCH_INTERVAL)
        if interval > 0:
            watcher = DeviceWatcher(data[CONF_DEVICES_PATH], runner, interval)
            watcher.start()
            stack.push_async_callback(watcher.stop)

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = RuntimeData(
            runner=runner, watcher=watcher, bridge_transport=bridge_transport, il_transport=il_transport,
            embedded_bridge=embedded_bridge,
        )
        entry.async_on_unload(entry.add_update_listener(_async_reload))
        _LOGGER.info("driving %d device(s) via %s (bridge: %s)", len(devices), topics.root, data[CONF_BRIDGE_MODE])

        # everything above succeeded: async_unload_entry (RuntimeData) owns closing it now, not this stack
        stack.pop_all()
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: RuntimeData = hass.data[DOMAIN].pop(entry.entry_id)
    if runtime.watcher:
        await runtime.watcher.stop()
    await runtime.runner.stop()
    await runtime.il_transport.close()
    await runtime.bridge_transport.close()
    if runtime.embedded_bridge:
        await runtime.embedded_bridge.stop()
    return True
