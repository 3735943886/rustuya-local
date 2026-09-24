"""Rustuya as a Home Assistant integration: runs the same `rustuya_local.service.Service` the standalone
`rustuya-local` daemon does, tied to this config entry, with an optional embedded rustuya-bridge (`pyrustuyabridge`)
and user overrides from `<config>/rustuya_converters`. It creates no entities and does not depend on il-ha: it is only an IL
*producer* — install il-ha (or any IL consumer) separately to turn its devices into entities.
"""

from __future__ import annotations

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
    CONVERTERS_DIR,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeData:
    service: Any
    embedded_bridge: Any | None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from pathlib import Path

    from rustuya_local.service import Service, Settings
    from tuya2ildevice.host import MqttTransport, load_devices

    from .bridge_supervisor import EmbeddedBridge

    data, options = entry.data, entry.options
    broker = f"mqtt://{data[CONF_BROKER_HOST]}:{data[CONF_BROKER_PORT]}"
    username, password = data.get(CONF_BROKER_USERNAME) or None, data.get(CONF_BROKER_PASSWORD) or None

    async def connect(kind: str, will=None):
        t = MqttTransport(data[CONF_BROKER_HOST], data[CONF_BROKER_PORT], client_id=f"rustuya-{kind}-{entry.entry_id[:8]}",
                          username=username, password=password, will=will)
        await t.connect()
        return t

    embedded_bridge = None
    if data[CONF_BRIDGE_MODE] == BRIDGE_EMBEDDED:
        embedded_bridge = EmbeddedBridge(broker, data[CONF_BRIDGE_ROOT], data[CONF_BRIDGE_STATE_FILE],
                                         data.get(CONF_BRIDGE_LOG_LEVEL, "warn"), username, password)
        await embedded_bridge.start()
    try:
        devices = await hass.async_add_executor_job(load_devices, data[CONF_DEVICES_PATH])
        # the device file is not polled here: the config/options flow writes it and calls async_refresh_devices
        settings = Settings(root=data[CONF_BRIDGE_ROOT], prefix=data.get(CONF_IL_PREFIX, "il"),
                            source=data.get(CONF_IL_SOURCE, "tuya"), devices=devices,
                            overrides_path=Path(hass.config.path(CONVERTERS_DIR)),
                            hub_options={"allow_hazardous": options.get(CONF_ALLOW_HAZARDOUS, False),
                                         "expose_unused": options.get(CONF_EXPOSE_UNUSED, False)})
        service = Service(settings, connect_bridge=lambda: connect("bridge"), connect_il=lambda will: connect("il", will))
        await service.start()          # releases whatever it had connected if it fails
    except BaseException:
        if embedded_bridge:
            await embedded_bridge.stop()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = RuntimeData(service=service, embedded_bridge=embedded_bridge)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.info("%d device(s) in the device file; IL follows the ones registered on %s (bridge: %s)", len(devices),
                 data[CONF_BRIDGE_ROOT], data[CONF_BRIDGE_MODE])
    return True


async def async_refresh_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hand the running service the device file as it is now: devices in it that the bridge holds get their descriptor
    published, ones that dropped out get every retained IL topic cleared (empty retained payloads), and no connection
    is restarted. A reload would not do that last part — a fresh Hub has no memory of what the old one published."""
    from tuya2ildevice.host import load_devices

    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        return
    records = await hass.async_add_executor_job(load_devices, entry.data[CONF_DEVICES_PATH])
    runtime.service.refresh_devices(records)


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: RuntimeData = hass.data[DOMAIN].pop(entry.entry_id)
    await runtime.service.stop()
    if runtime.embedded_bridge:
        await runtime.embedded_bridge.stop()
    return True
