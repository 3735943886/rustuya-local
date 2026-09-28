"""Rustuya as a Home Assistant integration: runs the same `rustuya_local.service.Service` the standalone
`rustuya-local` daemon does, tied to this config entry, with an optional embedded rustuya-bridge (`pyrustuyabridge`)
and user overrides from `<config>/rustuya_converters`. It creates no entities and does not depend on il-ha: it is only an IL
*producer* — install il-ha (or any IL consumer) separately to turn its devices into entities.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv

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
    CONF_PACK,
    CONF_PANEL,
    CONVERTERS_DIR,
    CREDS_FILE,
    DEFAULT_BRIDGE_STATE_FILE,
    DEFAULT_DEVICES_FILE,
    DOMAIN,
    LEGACY_BRIDGE_STATE_FILE,
    LEGACY_DEVICES_FILE,
    STORAGE_DIR,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SEND_COMMAND = "send_command"
SEND_COMMAND_SCHEMA = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("dp"): vol.All(vol.Coerce(str), vol.Match(r"^\d+$")),
    vol.Required("value"): vol.Any(bool, int, float, str),
})


@dataclass
class RuntimeData:
    service: Any
    embedded_bridge: Any | None
    options: dict[str, Any] | None = None       # what the service started with (a panel-only change needs no restart)


def _import_runtime() -> None:
    """Importing tuya2ildevice reads its data files; run in Home Assistant's import executor, not the event loop."""
    import tuya2ildevice.host  # noqa: F401

    import rustuya_local.service  # noqa: F401


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def send_command(call: ServiceCall) -> None:
        """One raw DP to a device, past the IL (`Service.send_dps`): fire and forget."""
        device_id = call.data["device_id"]
        runtime = next(iter(hass.data.get(DOMAIN, {}).values()), None)       # single_config_entry
        if runtime is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_running",
                                         translation_placeholders={"device_id": device_id})
        # imported already (the service is running): no module load in the event loop
        from rustuya_local.service import CommandRefused

        try:
            runtime.service.send_dps(_tuya_id(hass, runtime.service, device_id),
                                     {call.data["dp"]: call.data["value"]})
        except CommandRefused as e:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key=e.reason,
                                         translation_placeholders={"device_id": device_id}) from e

    hass.services.async_register(DOMAIN, SERVICE_SEND_COMMAND, send_command, schema=SEND_COMMAND_SCHEMA)
    return True


def _tuya_id(hass: HomeAssistant, service: Any, device_id: str) -> str:
    """`device_id` as the Tuya device id: as given when the service drives it, else a Home Assistant device's. An IL
    consumer registers a device with the IL id (the Tuya id) among its identifiers — il-ha as `("ildevice", id)`, under
    whatever platform name it runs as — so any identifier value the service drives is taken, when there is exactly one.
    Unresolved, it is returned as given (and refused as an unknown device)."""
    from homeassistant.helpers import device_registry as dr

    drivers = service.hub.drivers if service.hub is not None else {}
    if device_id in drivers:
        return device_id
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return device_id
    found = {value for _domain, value in device.identifiers if value in drivers}
    return found.pop() if len(found) == 1 else device_id


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from pathlib import Path

    await hass.async_add_import_executor_job(_import_runtime)
    from tuya2ildevice.host import MqttTransport

    from rustuya_local.service import AnotherProducer, Service, Settings

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
        await hass.async_add_executor_job(_ensure_parent, data[CONF_BRIDGE_STATE_FILE])
        embedded_bridge = EmbeddedBridge(broker, data[CONF_BRIDGE_ROOT], data[CONF_BRIDGE_STATE_FILE],
                                         data.get(CONF_BRIDGE_LOG_LEVEL, "warn"), username, password)
        await embedded_bridge.start()
    try:
        devices = await hass.async_add_executor_job(_load_devices_or_none, data[CONF_DEVICES_PATH])
        # the device file is not polled here: the config/options flow writes it and calls async_refresh_devices
        settings = Settings(root=data[CONF_BRIDGE_ROOT], prefix=data.get(CONF_IL_PREFIX, "il"),
                            source=data.get(CONF_IL_SOURCE, "tuya"), devices=devices,
                            overrides_path=Path(hass.config.path(CONVERTERS_DIR)), pack=options.get(CONF_PACK, True),
                            hub_options={"allow_hazardous": options.get(CONF_ALLOW_HAZARDOUS, False),
                                         "expose_unused": options.get(CONF_EXPOSE_UNUSED, False)})
        service = Service(settings, connect_bridge=lambda: connect("bridge"), connect_il=lambda will: connect("il", will))
        try:
            await service.start()      # releases whatever it had connected if it fails
        except AnotherProducer as e:   # retried by Home Assistant until the other one stops
            raise ConfigEntryNotReady(str(e)) from e
    except BaseException:
        if embedded_bridge:
            await embedded_bridge.stop()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = RuntimeData(service=service, embedded_bridge=embedded_bridge,
                                                                   options=dict(options))
    from . import panel

    await panel.async_setup(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.info("%d device(s) in the device file; IL follows the ones registered on %s (bridge: %s)", len(devices),
                 data[CONF_BRIDGE_ROOT], data[CONF_BRIDGE_MODE])
    return True


def _load_devices_or_none(path: str) -> list[dict]:
    """The device file's records; none yet when there is no file (set up without the cloud login: the login in
    Configure writes it, and `async_refresh_devices` hands it over)."""
    from tuya2ildevice.host import load_devices

    if not os.path.exists(path):
        _LOGGER.info("no device file at %s yet; fetch the devices from Tuya Cloud in the integration's Configure", path)
        return []
    return load_devices(path)


def _ensure_parent(path: str) -> None:
    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _move_file(old: str, new: str) -> None:
    """Move `old` to `new` unless `new` is already there (then `old` is left for the user to look at)."""
    import os

    _ensure_parent(new)
    if os.path.isfile(old) and not os.path.exists(new):
        os.replace(old, new)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 -> 1.2: the device file and the embedded bridge's state file move from `.storage/rustuya_*.json` to
    `.storage/rustuya/`, and with the device file the Tuya login rustuya-manager keeps beside it (`tuyacreds.json`,
    which sat loose in `.storage/`). Only a path still at the old default moves; one the user chose stays where it is."""
    if entry.version > 1:
        return False                       # a downgrade from a future version: nothing here can read it
    if entry.minor_version < 2:
        data = dict(entry.data)
        for key, old, new in ((CONF_DEVICES_PATH, LEGACY_DEVICES_FILE, DEFAULT_DEVICES_FILE),
                              (CONF_BRIDGE_STATE_FILE, LEGACY_BRIDGE_STATE_FILE, DEFAULT_BRIDGE_STATE_FILE)):
            if data.get(key) == hass.config.path(old):
                await hass.async_add_executor_job(_move_file, data[key], hass.config.path(new))
                data[key] = hass.config.path(new)
                if key == CONF_DEVICES_PATH:
                    await hass.async_add_executor_job(_move_file, hass.config.path(".storage", CREDS_FILE),
                                                      hass.config.path(STORAGE_DIR, CREDS_FILE))
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
        _LOGGER.info("migrated to entry version 1.2 (files under %s)", hass.config.path(STORAGE_DIR))
    return True


async def async_refresh_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hand the running service the device file as it is now: devices in it that the bridge holds get their descriptor
    published, ones that dropped out get every retained IL topic cleared (empty retained payloads), and no connection
    is restarted. A reload would not do that last part — a fresh Hub has no memory of what the old one published."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        # not running (it failed to start, e.g. without a device file before the first login): start it now, with the
        # file as it is
        if entry.state is not ConfigEntryState.LOADED:
            hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    records = await hass.async_add_executor_job(_load_devices_or_none, entry.data[CONF_DEVICES_PATH])
    runtime.service.refresh_devices(records)


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """An options change restarts the service, except one of the panel alone: that only adds or removes the panel."""
    from . import panel

    runtime: RuntimeData | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    new = dict(entry.options)
    if runtime is not None and runtime.options is not None and _without_panel(new) == _without_panel(runtime.options):
        runtime.options = new
        await panel.async_setup(hass, entry)            # adds it, or removes it when the option is off
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _without_panel(options: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in options.items() if k != CONF_PANEL}


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from . import panel

    # a restart (a reload) keeps the panel in the sidebar: taking it away closes it for whoever has it open, and the
    # setup that follows puts it back anyway; only disabling the entry (or deleting it, below) removes it
    if entry.disabled_by is not None:
        panel.async_remove(hass)
    runtime: RuntimeData = hass.data[DOMAIN].pop(entry.entry_id)
    await runtime.service.stop()
    if runtime.embedded_bridge:
        await runtime.embedded_bridge.stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deleted, not just unloaded (which only says `offline`, keeping everything for the next start). Clears what this
    entry leaves behind: its IL devices (retained descriptors and values, so il-ha and other IL consumers drop them),
    with the embedded bridge also that bridge's retained topics and its state file, and the files it kept in its own
    storage directory (the device list and the Tuya login). A path the user pointed elsewhere is left alone, as is
    everything on an external bridge. A broker that cannot be reached only leaves the retained topics behind; the
    removal goes on."""
    from . import panel

    panel.async_remove(hass)
    data = entry.data
    embedded = data.get(CONF_BRIDGE_MODE) == BRIDGE_EMBEDDED
    await async_clear_retained(hass, data, il=True, bridge=embedded)
    owned = [data.get(CONF_DEVICES_PATH)]
    if data.get(CONF_DEVICES_PATH):
        owned.append(os.path.join(os.path.dirname(data[CONF_DEVICES_PATH]), CREDS_FILE))
    if embedded:
        owned.append(data.get(CONF_BRIDGE_STATE_FILE))
    await hass.async_add_executor_job(_remove_owned, hass.config.path(STORAGE_DIR), [p for p in owned if p])


async def async_clear_retained(hass: HomeAssistant, data: dict[str, Any], *, il: bool, bridge: bool) -> None:
    """With the service stopped, clear what `data` (an entry's data) left retained on its broker: its IL devices and
    presence (`il`), and the whole bridge root (`bridge`, only for a bridge this entry owned). Used on delete, and when
    the panel's Settings move the IL prefix / source or the embedded bridge's root away (the old topics would stay). A
    broker that cannot be reached, or a producer / bridge still running there, leaves them and says so in the log."""
    await hass.async_add_import_executor_job(_import_runtime)
    from tuya2ildevice.host import MqttTransport

    from rustuya_local.service import AnotherProducer, purge_il, purge_retained

    prefix, source = data.get(CONF_IL_PREFIX, "il"), data.get(CONF_IL_SOURCE, "tuya")
    transport = MqttTransport(data[CONF_BROKER_HOST], data[CONF_BROKER_PORT],
                              client_id=f"rustuya-clear-{secrets.token_hex(4)}",
                              username=data.get(CONF_BROKER_USERNAME) or None,
                              password=data.get(CONF_BROKER_PASSWORD) or None)
    try:
        await transport.connect()
        if il:
            try:
                await purge_il(transport, prefix, source)
            except AnotherProducer as e:
                _LOGGER.warning("left the IL devices of %s/%s in place: %s", prefix, source, e)
        if bridge:
            try:
                await purge_retained(transport, data[CONF_BRIDGE_ROOT])
            except RuntimeError as e:
                _LOGGER.warning("%s", e)
    except Exception as e:  # noqa: BLE001 -- broker down, refused credentials: nothing to clear it with
        _LOGGER.warning("could not clear the retained topics of %s/%s from the broker: %s", prefix, source, e)
    finally:
        with contextlib.suppress(Exception):
            await transport.close()


def _remove_owned(storage: str, paths: list[str]) -> None:
    """Delete `paths` that lie in this integration's own storage directory (then the directory, once empty)."""
    root = os.path.realpath(storage)
    for path in paths:
        real = os.path.realpath(path)
        if os.path.dirname(real) != root:
            continue
        with contextlib.suppress(FileNotFoundError):
            os.remove(real)
            _LOGGER.info("removed %s", real)
    with contextlib.suppress(OSError):
        os.rmdir(root)
