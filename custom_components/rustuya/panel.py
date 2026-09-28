"""The advanced panel: a sidebar page for `<config>/rustuya_converters` and the bridge's device list against the cloud
list, turned on by the `panel` option.

The panel is registered while the entry is loaded with the option on, and removed on unload (an options change reloads
the entry, so it follows the option). The API views and the static path cannot be unregistered in Home Assistant, so
they are registered once and answer 404 whenever no loaded entry has the panel on. The files are the same ones the
manager plugin tab edits (`rustuya_local.manager_plugin.api`); `.py` converters run in Home Assistant's process, so
every view is for administrators only.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import (
    CONF_BRIDGE_ROOT,
    CONF_BROKER_HOST,
    CONF_BROKER_PASSWORD,
    CONF_BROKER_PORT,
    CONF_BROKER_USERNAME,
    CONF_DEVICES_PATH,
    CONF_PACK,
    CONF_PANEL,
    CONVERTERS_DIR,
    DOMAIN,
)

PANEL_URL = "rustuya"
STATIC_URL = "/rustuya_static"
_VIEWS_KEY = f"{DOMAIN}_panel_views"


async def async_setup(hass: HomeAssistant, entry: Any) -> None:
    if getattr(hass, "http", None) is None:          # no web server (a headless test instance): nothing to serve
        return
    if not hass.data.get(_VIEWS_KEY):
        hass.data[_VIEWS_KEY] = True
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), False)])
        for view in (ConvertersView, ConverterView, PackView, BridgeView, PanelView):
            hass.http.register_view(view())
        websocket_api.async_register_command(hass, ws_subscribe_links)
    if entry.options.get(CONF_PANEL, False) and PANEL_URL not in hass.data.get(frontend.DATA_PANELS, {}):
        version = (await async_get_integration(hass, DOMAIN)).version     # a new release is not served from cache
        await panel_custom.async_register_panel(
            hass, frontend_url_path=PANEL_URL, webcomponent_name="rustuya-panel",
            module_url=f"{STATIC_URL}/rustuya-panel.js?v={version}",
            sidebar_title="Rustuya", sidebar_icon="mdi:tune-variant", require_admin=True)
            # no `config_panel_domain`: it makes the integration's Configure open this panel instead of the options
            # flow, which is where the panel is turned back off


def async_remove(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)


def _runtime(hass: HomeAssistant) -> tuple[Any, Any] | None:
    """(entry, runtime) of the loaded entry with the panel on; None otherwise (the views then answer 404)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and entry.options.get(CONF_PANEL, False):
            runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if runtime is not None:
                return entry, runtime
    return None


class _View(HomeAssistantView):
    requires_auth = True

    def _loaded(self, request: web.Request) -> tuple[Any, Any] | web.Response:
        if not request["hass_user"].is_admin:
            return self.json_message("administrators only", HTTPStatus.FORBIDDEN)
        found = _runtime(request.app["hass"])
        if found is None:
            return self.json_message("the Rustuya panel is off", HTTPStatus.NOT_FOUND)
        return found

    async def _files(self, request: web.Request, fn: Callable[..., dict[str, Any]], *args: Any,
                     extend: Callable[[Any, Any, dict[str, Any]], dict[str, Any]] | None = None) -> web.Response:
        """`fn(<config>/rustuya_converters, *args)` in the executor, its errors as 400 / 404."""
        from rustuya_local.manager_plugin.api import Invalid

        found = self._loaded(request)
        if isinstance(found, web.Response):
            return found
        hass: HomeAssistant = request.app["hass"]
        try:
            result = await hass.async_add_executor_job(fn, Path(hass.config.path(CONVERTERS_DIR)), *args)
        except Invalid as e:
            return self.json_message(str(e), HTTPStatus.BAD_REQUEST)
        except FileNotFoundError as e:
            return self.json_message(f"no such file: {e}", HTTPStatus.NOT_FOUND)
        return self.json(extend(*found, result) if extend else result)


class ConvertersView(_View):
    url = "/api/rustuya/converters"
    name = "api:rustuya:converters"

    async def get(self, request: web.Request) -> web.Response:
        from rustuya_local.manager_plugin.api import list_converters

        def with_pack(entry: Any, runtime: Any, result: dict[str, Any]) -> dict[str, Any]:
            return {**result, "pack": {"enabled": entry.options.get(CONF_PACK, True),
                                       "status": runtime.service.pack_status}}

        return await self._files(request, list_converters, extend=with_pack)


class ConverterView(_View):
    url = "/api/rustuya/converters/{name}"
    name = "api:rustuya:converter"

    async def get(self, request: web.Request, name: str) -> web.Response:
        from rustuya_local.manager_plugin.api import read_converter

        return await self._files(request, read_converter, name)

    async def put(self, request: web.Request, name: str) -> web.Response:
        from rustuya_local.manager_plugin.api import save_converter

        try:
            body = await request.json()
        except ValueError:
            return self.json_message('the body is JSON: {"content": "..."}', HTTPStatus.BAD_REQUEST)
        return await self._files(request, save_converter, name, body.get("content") if isinstance(body, dict) else None)

    async def delete(self, request: web.Request, name: str) -> web.Response:
        from rustuya_local.manager_plugin.api import delete_converter

        return await self._files(request, delete_converter, name)


class PanelView(_View):
    """DELETE: turn the panel off from the panel itself (the `panel` option; the entry reloads and takes it away).
    It comes back on from the integration's Configure -> Tuning."""

    url = "/api/rustuya/panel"
    name = "api:rustuya:panel"

    async def delete(self, request: web.Request) -> web.Response:
        found = self._loaded(request)
        if isinstance(found, web.Response):
            return found
        entry = found[0]
        request.app["hass"].config_entries.async_update_entry(entry, options={**entry.options, CONF_PANEL: False})
        return self.json({"panel": False})


class PackView(_View):
    """POST: sync the override pack now rather than at the end of its day; `pack` in the list shows the result."""

    url = "/api/rustuya/pack"
    name = "api:rustuya:pack"

    async def post(self, request: web.Request) -> web.Response:
        found = self._loaded(request)
        if isinstance(found, web.Response):
            return found
        return self.json({"started": found[1].service.sync_pack_now()})


def _online(service: Any) -> dict[str, bool]:
    """Whether the bridge has each device connected, as the running service follows it (live, unlike the manager
    session opened per request): the devices both in the device file and on the bridge."""
    hub = getattr(service, "hub", None)
    return {device_id: bool(driver.linked) for device_id, driver in hub.drivers.items()} if hub is not None else {}


class BridgeView(_View):
    """The cloud list against what the bridge holds, through a rustuya-manager session like the options flow's
    `bridge_sync` step (and sharing its lock: one session at a time). GET: every device with its category. POST
    `{"add": [...], "update": [...], "remove": [...]}`: those commands (only for ids the diff has in that category),
    then the list again. `online` is the running service's link state by id."""

    url = "/api/rustuya/bridge"
    name = "api:rustuya:bridge"
    SETTLE = 1.5            # seconds for the bridge's replies to land before the list is read again after a POST

    async def get(self, request: web.Request) -> web.Response:
        return await self._session(request, None)

    async def post(self, request: web.Request) -> web.Response:
        from . import bridge_sync

        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not all(
                isinstance(body.get(k, []), list) and all(isinstance(i, str) for i in body.get(k, []))
                for k in (bridge_sync.ADD, bridge_sync.UPDATE, bridge_sync.REMOVE)):
            return self.json_message('the body is {"add": [ids], "update": [ids], "remove": [ids]}',
                                     HTTPStatus.BAD_REQUEST)
        return await self._session(request, body)

    async def _session(self, request: web.Request, selection: dict[str, list[str]] | None) -> web.Response:
        from . import bridge_sync, manager_session

        found = self._loaded(request)
        if isinstance(found, web.Response):
            return found
        if not manager_session.available():
            return self.json_message("rustuya-manager is not installed", HTTPStatus.NOT_IMPLEMENTED)
        data = found[0].data
        try:
            manager = await manager_session.open_manager(
                broker=f"mqtt://{data[CONF_BROKER_HOST]}:{data[CONF_BROKER_PORT]}", root=data[CONF_BRIDGE_ROOT],
                devices_path=data[CONF_DEVICES_PATH], username=data.get(CONF_BROKER_USERNAME) or None,
                password=data.get(CONF_BROKER_PASSWORD) or None)
        except manager_session.Busy:
            return self.json_message("a Tuya login or device sync window is open; close it and try again",
                                     HTTPStatus.CONFLICT)
        except Exception as e:  # noqa: BLE001 -- broker down, bridge not answering: any of it is a 502
            return self.json_message(f"cannot reach the bridge: {e}", HTTPStatus.BAD_GATEWAY)
        try:
            diff = await manager.sync()
            sent = 0
            if selection is not None:
                selection = {**selection, bridge_sync.ADD: bridge_sync.parents_first(
                    selection.get(bridge_sync.ADD, []), diff)}
                sent = await bridge_sync.apply(manager, diff, selection)
                if sent:
                    await asyncio.sleep(self.SETTLE)
                    diff = await manager.sync()
            state = getattr(manager, "state", None)
            bridge = getattr(state, "bridge", None) if state is not None else None
            return self.json({"devices": bridge_sync.listing(diff, bridge), "sent": sent,
                              "cloud_loaded": bool(getattr(state, "cloud", True)),
                              "online": _online(found[1].service)})
        except RuntimeError as e:                                  # a publish failed
            return self.json_message(f"could not send the command to the bridge: {e}", HTTPStatus.BAD_GATEWAY)
        finally:
            await manager_session.close_manager(manager)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "rustuya/subscribe_links"})
@websocket_api.async_response
async def ws_subscribe_links(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                             msg: dict[str, Any]) -> None:
    """Every device's bridge connection, live, for as long as the panel is open: its own MQTT connection listening
    to the bridge's messages (`LinkWatcher`, topics resolved the way `BridgeClient` does), closed when the panel
    unsubscribes or its websocket goes. Events are `{"links": {id: {"online", "code", "message"}}}`: all of them
    first, then each change."""
    from tuya2ildevice.host import MqttTransport

    from rustuya_local.bridge_client import LinkWatcher

    found = _runtime(hass)
    if found is None:
        connection.send_error(msg["id"], "not_found", "the Rustuya panel is off")
        return
    data = found[0].data
    transport = MqttTransport(data[CONF_BROKER_HOST], data[CONF_BROKER_PORT],
                              client_id=f"rustuya-panel-{secrets.token_hex(4)}",
                              username=data.get(CONF_BROKER_USERNAME) or None,
                              password=data.get(CONF_BROKER_PASSWORD) or None)
    watcher = LinkWatcher(transport, data[CONF_BRIDGE_ROOT])
    try:
        await transport.connect()
        await watcher.start()
    except Exception as e:  # noqa: BLE001 -- broker down, refused credentials: the panel keeps the service's view
        with contextlib.suppress(Exception):
            await transport.close()
        connection.send_error(msg["id"], "cannot_connect", f"cannot reach the broker: {e}")
        return

    def send(links: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"links": links}))

    def close() -> None:
        watcher.stop()
        hass.async_create_task(transport.close())

    connection.subscriptions[msg["id"]] = close
    connection.send_result(msg["id"])
    send(dict(watcher.links))                     # what the retained messages said so far; later ones come as changes
    watcher.on_change = lambda device_id, link: send({device_id: link})
