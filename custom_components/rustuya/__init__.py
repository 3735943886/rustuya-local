"""Rustuya as a Home Assistant integration: runs the same `rustuya_local.service.Service` the standalone
`rustuya-local` daemon does, tied to this config entry, with an optional embedded rustuya-bridge (`pyrustuyabridge`)
and user overrides from `<config>/rustuya_converters`. The service produces IL on an in-process transport, and an
ildevice-homeassistant hub embedded on that transport turns the IL devices into entities registered under this
integration. That hub is the `ildevice_ha` package (manifest `requirements`); the IL device integration itself need not
be installed. The bridge still talks to the service over MQTT: on the user's broker, or, for a user who has none, on one
this integration runs (`builtin_broker.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry

from . import broker
from .const import (
    BRIDGE_EMBEDDED,
    CONF_ALLOW_HAZARDOUS,
    CONF_BRIDGE_LOG_LEVEL,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_BRIDGE_STATE_FILE,
    CONF_BROKER_BUILTIN,
    CONF_DEVICES_PATH,
    CONF_EXPOSE_UNUSED,
    CONF_IL_PREFIX,
    CONF_IL_SOURCE,
    CONF_PACK,
    CONF_PANEL,
    CONVERTERS_DIR,
    CREDS_FILE,
    DEFAULT_BRIDGE_LOG_LEVEL,
    DEFAULT_BRIDGE_STATE_FILE,
    DEFAULT_DEVICES_FILE,
    DEFAULT_OPTIONS,
    DOMAIN,
    IL_PREFIX,
    IL_SOURCE,
    LEGACY_BRIDGE_STATE_FILE,
    LEGACY_DEVICES_FILE,
    PLATFORMS,
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
    hub: Any = None                             # the ildevice hub creating this entry's entities; outlives the service
    transport: Any = None                       # the in-process IL transport between the service and the hub
    retained: Any = None                        # keeps what the transport retains across Home Assistant restarts
    service_lock: asyncio.Lock = field(default_factory=asyncio.Lock)    # one restart or stop of the service at a time


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
    consumer registers a device with the IL id (the Tuya id) among its identifiers — the embedded hub as `("rustuya", id)`,
    a separate ildevice-homeassistant as `("ildevice", id)` — so any identifier value the service drives is taken, when
    there is exactly one.
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


def _make_service(hass: HomeAssistant, entry: ConfigEntry, data: dict[str, Any], transport: Any,
                  devices: list[dict]) -> Any:
    """A service for the entry as it is now (`data`: its data, `broker.resolve`d), producing IL on `transport`. It has no
    Last Will there: nothing outlives the process that holds both ends."""
    from rustuya_local.service import Service, Settings

    options = {**DEFAULT_OPTIONS, **entry.options}

    async def connect_bridge():
        t = broker.transport(data, f"rustuya-bridge-{entry.entry_id[:8]}")
        await t.connect()
        return t

    async def connect_il(_will):
        return transport

    # the device file is not polled here: the config/options flow writes it and calls async_refresh_devices
    settings = Settings(root=data[CONF_BRIDGE_ROOT], prefix=IL_PREFIX, source=IL_SOURCE, devices=devices,
                        overrides_path=Path(hass.config.path(CONVERTERS_DIR)), pack=options[CONF_PACK],
                        hub_options={key: options[key] for key in (CONF_ALLOW_HAZARDOUS, CONF_EXPOSE_UNUSED)})
    return Service(settings, connect_bridge=connect_bridge, connect_il=connect_il)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await broker.import_runtime(hass)
    from ildevice_ha.attach import build_hub
    from ildevice_ha.const import CONF_AUTO_ADD
    from ildevice_ha.const import CONF_IL_PREFIX as HUB_IL_PREFIX
    from ildevice_ha.core.memory import InProcessTransport

    from .bridge_supervisor import EmbeddedBridge
    from .retained import RetainedStore

    data = await broker.resolve(hass, entry.data)

    embedded_bridge = None
    if data[CONF_BRIDGE_MODE] == BRIDGE_EMBEDDED:
        await hass.async_add_executor_job(_ensure_parent, data[CONF_BRIDGE_STATE_FILE])
        embedded_bridge = EmbeddedBridge(broker.url(data), data[CONF_BRIDGE_ROOT], data[CONF_BRIDGE_STATE_FILE],
                                         data.get(CONF_BRIDGE_LOG_LEVEL, DEFAULT_BRIDGE_LOG_LEVEL),
                                         *broker.credentials(data))
        await embedded_bridge.start()
    runtime = None
    forwarded = False
    try:
        devices = await hass.async_add_executor_job(_load_devices_or_none, data[CONF_DEVICES_PATH])
        transport = InProcessTransport()
        # Every device the bridge holds is the user's own: add it, never offer it (ildevice's offers would go to the
        # ildevice domain, not this one).
        hub = build_hub(hass, {HUB_IL_PREFIX: IL_PREFIX, CONF_AUTO_ADD: True}, transport, DOMAIN, entry.entry_id)
        retained = RetainedStore(hass, entry.entry_id, transport, IL_PREFIX)
        await retained.async_restore()
        runtime = RuntimeData(service=_make_service(hass, entry, data, transport, devices),
                              embedded_bridge=embedded_bridge,
                              options=dict(entry.options), hub=hub, transport=transport, retained=retained)
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
        await retained.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        forwarded = True
        # The hub goes first: it has to see the tombstones of devices that dropped out while Home Assistant was down, or
        # their registry entries would stay behind.
        await hub.async_start()
        await runtime.service.start()         # releases whatever it had connected if it fails
    except BaseException:
        if runtime is not None:
            hass.data[DOMAIN].pop(entry.entry_id, None)
            if forwarded:
                await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            await runtime.hub.async_stop()
            await runtime.retained.async_stop()
        if embedded_bridge:
            await embedded_bridge.stop()
        raise

    from . import panel

    await panel.async_setup(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.info("%d device(s) in the device file; IL follows the ones registered on %s (bridge: %s%s)", len(devices),
                 data[CONF_BRIDGE_ROOT], data[CONF_BRIDGE_MODE], ", built-in broker" if data.get(CONF_BROKER_BUILTIN) else "")
    return True


async def async_restart_service(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start the service over with the entry's current options, keeping the hub, the entities and the transport. The
    presence stays `online` and the retained values stay, so every device stays available: going `unavailable` and back
    is a state change, and an automation that follows an event entity's state takes it for a press. Raises what the
    start raises; the devices are then shown offline and `runtime.service` is a stopped service."""
    runtime: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    async with runtime.service_lock:
        await runtime.service.stop(offline=False)
        runtime.options = dict(entry.options)
        devices = await hass.async_add_executor_job(_load_devices_or_none, entry.data[CONF_DEVICES_PATH])
        data = await broker.resolve(hass, entry.data)
        runtime.service = _make_service(hass, entry, data, runtime.transport, devices)
        await runtime.service.start(resume=True)


def _load_devices_or_none(path: str) -> list[dict]:
    """The device file's records; none yet when there is no file (set up without the cloud login: the login in
    Configure writes it, and `async_refresh_devices` hands it over)."""
    from rustuya_local.host import load_devices

    if not os.path.exists(path):
        _LOGGER.info("no device file at %s yet; fetch the devices from Tuya Cloud in the integration's Configure", path)
        return []
    return load_devices(path)


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _move_file(old: str, new: str) -> None:
    """Move `old` to `new` unless `new` is already there (then `old` is left for the user to look at)."""
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
    if runtime is None:
        return
    if runtime.options is not None and _without_panel(new) == _without_panel(runtime.options):
        runtime.options = new
        await panel.async_setup(hass, entry)            # adds it, or removes it when the option is off
        return
    try:
        await async_restart_service(hass, entry)
    except Exception:
        _LOGGER.exception("could not restart the service with the new options; reloading the integration")
        hass.config_entries.async_schedule_reload(entry.entry_id)


def _without_panel(options: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in options.items() if k != CONF_PANEL}


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from . import panel

    # a restart (a reload) keeps the panel in the sidebar: taking it away closes it for whoever has it open, and the
    # setup that follows puts it back anyway; only disabling the entry (or deleting it, below) removes it
    if entry.disabled_by is not None:
        panel.async_remove(hass)
    runtime: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    # Ask HA to unload the entity platforms before stopping their publisher. If HA refuses, leave the complete
    # runtime in place so the entry remains usable and HA can retry the unload.
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    stop_error: Exception | None = None
    try:
        async with runtime.service_lock:
            await runtime.service.stop()
    except Exception as err:  # noqa: BLE001 -- teardown must continue if draining a command fails
        # Service.stop closes its AsyncExitStack even when draining a pending command fails. The HA platforms are
        # already unloaded, so finish tearing down the hub and bridge rather than leave a half-loaded entry behind.
        stop_error = err
    finally:
        try:
            await runtime.hub.async_stop()
        finally:
            try:
                await runtime.retained.async_stop()
            finally:
                if runtime.embedded_bridge:
                    await runtime.embedded_bridge.stop()
                hass.data[DOMAIN].pop(entry.entry_id, None)
    if stop_error is not None:
        _LOGGER.error("service stop failed during unload; the service resources were closed",
                      exc_info=(type(stop_error), stop_error, stop_error.__traceback__))
    return True


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device_entry: DeviceEntry) -> bool:
    """A device still driven would come right back, so it is refused until it is removed from the bridge; one nothing
    drives any more (left over in the registry) just goes."""
    runtime: RuntimeData | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        return False
    service_hub = runtime.service.hub
    live = set(runtime.hub.devices) | set(service_hub.drivers if service_hub is not None else ())
    if any(domain == DOMAIN and value in live for domain, value in device_entry.identifiers):
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="device_on_bridge",
                                 translation_placeholders={"name": device_entry.name_by_user or device_entry.name or ""})
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deleted, not just unloaded. Clears what this entry leaves behind: its stored IL publications, what an entry from
    before the embedded hub left on the broker (it has an IL prefix in its data), with the embedded bridge also that
    bridge's retained topics and its state file, and the files it kept in its own storage directory (the device list and
    the Tuya login). A path the user pointed elsewhere is left alone, as is everything on an external bridge. A broker
    that cannot be reached only leaves the retained topics behind; the removal goes on. The built-in broker is stopped
    instead: what it retains goes with it."""
    from . import panel
    from .retained import RetainedStore

    panel.async_remove(hass)
    await RetainedStore.async_remove(hass, entry.entry_id)
    data = entry.data
    embedded = data.get(CONF_BRIDGE_MODE) == BRIDGE_EMBEDDED
    il = CONF_IL_PREFIX in data
    if data.get(CONF_BROKER_BUILTIN):
        from . import builtin_broker

        await builtin_broker.async_stop(hass)
    elif il or embedded:
        await async_clear_retained(hass, data, il=il, bridge=embedded)
    owned = [data.get(CONF_DEVICES_PATH)]
    if data.get(CONF_DEVICES_PATH):
        owned.append(os.path.join(os.path.dirname(data[CONF_DEVICES_PATH]), CREDS_FILE))
    if embedded:
        owned.append(data.get(CONF_BRIDGE_STATE_FILE))
    await hass.async_add_executor_job(_remove_owned, hass.config.path(STORAGE_DIR), [p for p in owned if p])


async def async_clear_retained(hass: HomeAssistant, data: dict[str, Any], *, il: bool, bridge: bool) -> None:
    """With the service stopped, clear what `data` (an entry's data) left retained on its broker: the IL devices and
    presence an entry from before the embedded hub published there (`il`), and the whole bridge root (`bridge`, only for
    a bridge this entry owned). Used on delete, and when the panel's Settings move the embedded bridge's root away (the
    old topics would stay). A broker that cannot be reached, or a producer / bridge still running there, leaves them and
    says so in the log."""
    await broker.import_runtime(hass)
    from rustuya_local.service import AnotherProducer, purge_il, purge_retained

    prefix, source = data.get(CONF_IL_PREFIX, IL_PREFIX), data.get(CONF_IL_SOURCE, IL_SOURCE)
    transport = None
    try:
        data = await broker.resolve(hass, data)
        transport = broker.transport(data, f"rustuya-clear-{secrets.token_hex(4)}")
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
        if transport is not None:
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
