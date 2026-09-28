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
import functools
import hashlib
import secrets
from collections.abc import Awaitable, Callable
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

from . import broker
from .const import (
    BRIDGE_EMBEDDED,
    BRIDGE_LOG_LEVELS,
    CONF_BRIDGE_LOG_LEVEL,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_BRIDGE_STATE_FILE,
    CONF_DEVICES_PATH,
    CONF_IL_PREFIX,
    CONF_IL_SOURCE,
    CONF_PACK,
    CONF_PANEL,
    CONVERTERS_DIR,
    DEFAULT_OPTIONS,
    DOMAIN,
)

PANEL_URL = "rustuya"
STATIC_URL = "/rustuya_static"
WWW = Path(__file__).parent / "www"
_VIEWS_KEY = f"{DOMAIN}_panel_views"
_CLOUD_KEY = f"{DOMAIN}_cloud_fetch"         # the panel's running (or last) cloud fetch


async def async_setup(hass: HomeAssistant, entry: Any) -> None:
    if getattr(hass, "http", None) is None:          # no web server (a headless test instance): nothing to serve
        return
    if not hass.data.get(_VIEWS_KEY):
        hass.data[_VIEWS_KEY] = True
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(WWW), False)])
        for view in (ConvertersView, ConverterView, PackView, BridgeView, PanelView, SettingsView, OptionsView,
                     CloudView):
            hass.http.register_view(view())
        websocket_api.async_register_command(hass, ws_subscribe_links)
    if not entry.options.get(CONF_PANEL, False):
        async_remove(hass)
        return
    if PANEL_URL not in hass.data.get(frontend.DATA_PANELS, {}):
        # the file's own hash, not only the release: a changed file is never served from a browser's cache
        version = (await async_get_integration(hass, DOMAIN)).version
        digest = await hass.async_add_executor_job(_digest, WWW / "rustuya-panel.js")
        await panel_custom.async_register_panel(
            hass, frontend_url_path=PANEL_URL, webcomponent_name="rustuya-panel",
            module_url=f"{STATIC_URL}/rustuya-panel.js?v={version}-{digest}",
            sidebar_title="Rustuya", sidebar_icon="mdi:tune-variant", require_admin=True)
            # no `config_panel_domain`: it makes the integration's Configure open this panel instead of the options
            # flow, which is where the panel is turned back off


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def async_remove(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
    fetch = hass.data.pop(_CLOUD_KEY, None)
    if fetch is not None and fetch.running:         # the entry goes away: its session must not outlive it
        hass.async_create_task(fetch.cancel())


def _runtime(hass: HomeAssistant) -> tuple[Any, Any] | None:
    """(entry, runtime) of the loaded entry with the panel on; None otherwise (the views then answer 404)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and entry.options.get(CONF_PANEL, False):
            runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if runtime is not None:
                return entry, runtime
    return None


async def _json_body(request: web.Request) -> Any:
    """The request's JSON body; None when there is none or it is not JSON."""
    try:
        return await request.json()
    except ValueError:
        return None


def _entry_view(handler: Callable[..., Awaitable[web.Response]]) -> Callable[..., Awaitable[web.Response]]:
    """A view method for administrators, while a loaded entry has the panel on (403 / 404 otherwise): called with
    that entry and its runtime after the request."""

    @functools.wraps(handler)
    async def view(self: _View, request: web.Request, **match: str) -> web.Response:
        if not request["hass_user"].is_admin:
            return self.json_message("administrators only", HTTPStatus.FORBIDDEN)
        found = _runtime(request.app["hass"])
        if found is None:
            return self.json_message("the Rustuya panel is off", HTTPStatus.NOT_FOUND)
        return await handler(self, request, *found, **match)

    return view


class _View(HomeAssistantView):
    requires_auth = True

    async def _files(self, request: web.Request, fn: Callable[..., dict[str, Any]], *args: Any,
                     extend: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> web.Response:
        """`fn(<config>/rustuya_converters, *args)` in the executor, its errors as 400 / 404."""
        from rustuya_local.manager_plugin.api import Invalid

        hass: HomeAssistant = request.app["hass"]
        try:
            result = await hass.async_add_executor_job(fn, Path(hass.config.path(CONVERTERS_DIR)), *args)
        except Invalid as e:
            return self.json_message(str(e), HTTPStatus.BAD_REQUEST)
        except FileNotFoundError as e:
            return self.json_message(f"no such file: {e}", HTTPStatus.NOT_FOUND)
        return self.json(extend(result) if extend else result)


class ConvertersView(_View):
    url = "/api/rustuya/converters"
    name = "api:rustuya:converters"

    @_entry_view
    async def get(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        from rustuya_local.manager_plugin.api import list_converters

        pack = {"enabled": entry.options.get(CONF_PACK, DEFAULT_OPTIONS[CONF_PACK]),
                "status": runtime.service.pack_status}
        return await self._files(request, list_converters, extend=lambda result: {**result, "pack": pack})


class ConverterView(_View):
    url = "/api/rustuya/converters/{name}"
    name = "api:rustuya:converter"

    @_entry_view
    async def get(self, request: web.Request, entry: Any, runtime: Any, name: str) -> web.Response:
        from rustuya_local.manager_plugin.api import read_converter

        return await self._files(request, read_converter, name)

    @_entry_view
    async def put(self, request: web.Request, entry: Any, runtime: Any, name: str) -> web.Response:
        from rustuya_local.manager_plugin.api import save_converter

        body = await _json_body(request)
        if body is None:
            return self.json_message('the body is JSON: {"content": "..."}', HTTPStatus.BAD_REQUEST)
        return await self._files(request, save_converter, name, body.get("content") if isinstance(body, dict) else None)

    @_entry_view
    async def delete(self, request: web.Request, entry: Any, runtime: Any, name: str) -> web.Response:
        from rustuya_local.manager_plugin.api import delete_converter

        return await self._files(request, delete_converter, name)


class PanelView(_View):
    """DELETE: turn the panel off from the panel itself (the `panel` option; the entry reloads and takes it away).
    The integration's Configure adds it back."""

    url = "/api/rustuya/panel"
    name = "api:rustuya:panel"

    @_entry_view
    async def delete(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        request.app["hass"].config_entries.async_update_entry(entry, options={**entry.options, CONF_PANEL: False})
        return self.json({"panel": False})


class OptionsView(_View):
    """GET / PUT the entry's options other than the panel (which stays in Configure, to turn the panel back on):
    remote control of hazardous devices, exposing unused data points, the override pack. A change is an options
    update, which reloads the entry as Configure's did."""

    url = "/api/rustuya/options"
    name = "api:rustuya:options"

    @_entry_view
    async def get(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        return self.json({k: bool(entry.options.get(k, default)) for k, default in DEFAULT_OPTIONS.items()})

    @_entry_view
    async def put(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        body = await _json_body(request)
        if (not isinstance(body, dict) or set(body) - set(DEFAULT_OPTIONS)
                or not all(isinstance(v, bool) for v in body.values())):
            return self.json_message(f"the options are {sorted(DEFAULT_OPTIONS)}, each true or false",
                                     HTTPStatus.BAD_REQUEST)
        options = {**entry.options, **body}
        if options == dict(entry.options):
            return self.json({"restarting": False})
        await _restart(request.app["hass"], entry, options=options)        # answered once it has restarted
        return self.json({"restarting": True})


def settings_of(data: Any) -> dict[str, Any]:
    """The entry's settings the panel edits (setup leaves them at their defaults); the embedded bridge's only with it."""
    keys = [CONF_BRIDGE_ROOT, CONF_IL_PREFIX, CONF_IL_SOURCE, CONF_DEVICES_PATH]
    if data.get(CONF_BRIDGE_MODE) == BRIDGE_EMBEDDED:
        keys += [CONF_BRIDGE_STATE_FILE, CONF_BRIDGE_LOG_LEVEL]
    return {k: data.get(k) for k in keys}


def validate_settings(hass: HomeAssistant, data: Any, body: Any) -> dict[str, Any]:
    """`body` over the current settings, checked; raises ValueError with what is wrong."""
    current = settings_of(data)
    if not isinstance(body, dict) or set(body) - set(current):
        raise ValueError(f"the settings are {sorted(current)}")
    new = {**current, **body}
    for key, value in new.items():
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"{key} is a non-empty text without surrounding spaces")
    for key in (CONF_BRIDGE_ROOT, CONF_IL_PREFIX, CONF_IL_SOURCE):
        if not broker.topic_ok(new[key]):
            raise ValueError(f"{key} is an MQTT topic without wildcards or empty levels")
    if "/" in new[CONF_IL_SOURCE] or new[CONF_IL_SOURCE].startswith("_"):
        raise ValueError(f"{CONF_IL_SOURCE} is one topic level, not starting with _")
    for key in (CONF_DEVICES_PATH, CONF_BRIDGE_STATE_FILE):
        if key in new:
            new[key] = hass.config.path(new[key])       # relative to the config directory; an absolute path as is
    if CONF_BRIDGE_LOG_LEVEL in new and new[CONF_BRIDGE_LOG_LEVEL] not in BRIDGE_LOG_LEVELS:
        raise ValueError(f"{CONF_BRIDGE_LOG_LEVEL} is one of {', '.join(BRIDGE_LOG_LEVELS)}")
    return new


class SettingsView(_View):
    """GET / PUT the settings setup leaves at their defaults: the bridge topic root, the IL prefix and source, the
    device file, the embedded bridge's state file and log level. A change restarts the integration (`_apply`); a new
    root for an external bridge is checked first, as setup does."""

    url = "/api/rustuya/settings"
    name = "api:rustuya:settings"

    @_entry_view
    async def get(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        return self.json({"mode": entry.data.get(CONF_BRIDGE_MODE), "settings": settings_of(entry.data),
                          "log_levels": list(BRIDGE_LOG_LEVELS)})

    @_entry_view
    async def put(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        hass = request.app["hass"]
        try:
            new = validate_settings(hass, entry.data, await request.json())
        except ValueError as e:
            return self.json_message(str(e), HTTPStatus.BAD_REQUEST)
        old = dict(entry.data)
        if new == settings_of(old):
            return self.json({"restarting": False})
        external = old.get(CONF_BRIDGE_MODE) != BRIDGE_EMBEDDED
        if external and new[CONF_BRIDGE_ROOT] != old[CONF_BRIDGE_ROOT]:
            from .config_flow import _probe_bridge

            error = await _probe_bridge(hass, {**old, **new})
            if error is not None:
                return self.json_message(
                    f"no rustuya-bridge answers on {new[CONF_BRIDGE_ROOT]}" if error == "bridge_not_found"
                    else "cannot connect to the broker", HTTPStatus.BAD_REQUEST)
        await _apply(hass, entry, old, {**old, **new})                       # answered once it has restarted
        return self.json({"restarting": True})


async def _apply(hass: HomeAssistant, entry: Any, old: dict[str, Any], data: dict[str, Any]) -> None:
    """Stop, clear what the old IL prefix / source (and an embedded bridge's old root) left retained, which would
    otherwise stay on the broker, then start with the new settings."""
    from . import async_clear_retained

    il_moved = (old.get(CONF_IL_PREFIX), old.get(CONF_IL_SOURCE)) != (data[CONF_IL_PREFIX], data[CONF_IL_SOURCE])
    bridge_moved = old.get(CONF_BRIDGE_MODE) == BRIDGE_EMBEDDED and old[CONF_BRIDGE_ROOT] != data[CONF_BRIDGE_ROOT]
    async def clear() -> None:
        if il_moved or bridge_moved:
            await async_clear_retained(hass, old, il=il_moved, bridge=bridge_moved)

    await _restart(hass, entry, data=data, between=clear)


async def _restart(hass: HomeAssistant, entry: Any, *, data: dict[str, Any] | None = None,
                   options: dict[str, Any] | None = None, between: Callable[[], Any] | None = None) -> None:
    """Stop the entry, run `between`, store the new data / options, start it again — all awaited, so the panel's save
    answers once the restart is over (the page then reloads onto the restarted entry, not onto one about to go).
    Stopped, the entry has no update listener, so storing does not start a second reload. An options-only restart
    keeps the IL presence online across it (`mark_restart`); new data may move the broker or the IL topics."""
    if data is None:
        from . import mark_restart

        mark_restart(hass, entry)
    await hass.config_entries.async_unload(entry.entry_id)
    if between is not None:
        await between()
    changes = {k: v for k, v in (("data", data), ("options", options)) if v is not None}
    hass.config_entries.async_update_entry(entry, **changes)
    await hass.config_entries.async_setup(entry.entry_id)


class PackView(_View):
    """POST: sync the override pack now rather than at the end of its day; `pack` in the list shows the result."""

    url = "/api/rustuya/pack"
    name = "api:rustuya:pack"

    @_entry_view
    async def post(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        return self.json({"started": runtime.service.sync_pack_now()})


def _online(service: Any) -> dict[str, bool]:
    """Whether the bridge has each device connected, as the running service follows it (live, unlike the manager
    session opened per request): the devices both in the device file and on the bridge."""
    hub = getattr(service, "hub", None)
    return {device_id: bool(driver.linked) for device_id, driver in hub.drivers.items()} if hub is not None else {}


def _wizard_state(session: Any) -> str:
    return str(getattr(session.state, "value", session.state))


def _outcome(state: str, message: str = "", error: str | None = None) -> dict[str, Any]:
    return {"state": state, "message": message, "qr": None, "error": error}


class CloudFetch:
    """The panel's fetch of the device list from Tuya Cloud: rustuya-manager's wizard in a session held open while it
    runs (a saved login is reused; without one it shows a QR to scan). The page polls `status()`; a page that stops
    polling (closed) or a fetch that runs on too long closes the session, since it holds the one-session lock the
    options flow and the bridge list share. Once done the new device file goes to the running entry."""

    IDLE_TIMEOUT = 30.0         # seconds without a poll before the session is closed
    MAX_TIME = 600.0            # a QR not scanned in 10 minutes is given up
    WATCH_INTERVAL = 0.5        # seconds between looks at the wizard's state

    def __init__(self, hass: HomeAssistant, entry: Any) -> None:
        self.hass, self.entry = hass, entry
        self.manager: Any = None
        self.final: dict[str, Any] | None = None       # the outcome, once the session is closed
        self._polled = self._started = 0.0
        self._task: asyncio.Task | None = None
        self._finishing = False

    @property
    def running(self) -> bool:
        return self.final is None

    async def start(self, user_code: str | None) -> None:
        from . import manager_session

        self.manager = await manager_session.open_for_entry(self.hass, self.entry.data)
        try:
            await self.manager.wizard.start(user_code or None, scan=False)
        except BaseException:
            await manager_session.close_manager(self.manager)
            raise
        self._polled = self._started = self.hass.loop.time()
        self._task = self.hass.async_create_background_task(self._watch(), "rustuya cloud fetch")

    def status(self) -> dict[str, Any]:
        self._polled = self.hass.loop.time()
        if self.final is not None:
            return self.final
        session = self.manager.wizard.session
        state = _wizard_state(session)
        if state in ("done", "error"):              # the outcome is reported once the session has closed (`_finish`)
            state = "finishing"
        return {"state": state, "message": session.message or "", "qr": session.qr_image_data_url,
                "error": session.error}

    async def cancel(self) -> None:
        if self.running and not self._finishing:
            await self._finish(_outcome("cancelled", "Cancelled"))

    async def _watch(self) -> None:
        while self.running and not self._finishing:
            await asyncio.sleep(self.WATCH_INTERVAL)
            session = self.manager.wizard.session
            state = _wizard_state(session)
            now = self.hass.loop.time()
            if state in ("done", "error"):
                await self._finish(_outcome(state, session.message or "", session.error))
            elif now - self._polled > self.IDLE_TIMEOUT or now - self._started > self.MAX_TIME:
                await self._finish(_outcome("cancelled", "Stopped: the page stopped asking, or it took too long"))

    async def _finish(self, final: dict[str, Any]) -> None:
        from . import async_refresh_devices, manager_session

        if self._finishing:                         # finishing already (a cancel racing the watcher)
            return
        self._finishing = True
        try:
            await manager_session.close_manager(self.manager)
            if final["state"] == "done":
                await async_refresh_devices(self.hass, self.entry)   # after the session: the new file to the entry
        finally:
            # reported only now: the page reloads the bridge list on `done`, which needs the session lock this held
            self.final = final


def _fetch_status(fetch: CloudFetch | None) -> dict[str, Any]:
    return fetch.status() if fetch is not None else {"state": "idle"}


class CloudView(_View):
    """The panel's cloud fetch (`CloudFetch`). GET: its status (`{"state": "idle"}` before any). POST
    `{"user_code": "..."}` (optional, only for a first login): start one. DELETE: cancel it."""

    url = "/api/rustuya/cloud"
    name = "api:rustuya:cloud"

    @_entry_view
    async def get(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        return self.json(_fetch_status(request.app["hass"].data.get(_CLOUD_KEY)))

    @_entry_view
    async def post(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        from . import manager_session

        if not manager_session.available():
            return self.json_message("rustuya-manager is not installed", HTTPStatus.NOT_IMPLEMENTED)
        hass = request.app["hass"]
        body = await _json_body(request)
        user_code = body.get("user_code") if isinstance(body, dict) else None
        if user_code is not None and not isinstance(user_code, str):
            return self.json_message('the body is {"user_code": "..."} (optional)', HTTPStatus.BAD_REQUEST)
        current: CloudFetch | None = hass.data.get(_CLOUD_KEY)
        if current is not None and current.running:
            return self.json_message("a fetch is already running", HTTPStatus.CONFLICT)
        fetch = CloudFetch(hass, entry)
        try:
            await fetch.start((user_code or "").strip() or None)
        except manager_session.Busy:
            return self.json_message("a Tuya Cloud fetch or device sync window is open; close it and try again",
                                     HTTPStatus.CONFLICT)
        except Exception as e:  # noqa: BLE001 -- broker down, bridge not answering: any of it is a 502
            return self.json_message(f"cannot start the fetch: {e}", HTTPStatus.BAD_GATEWAY)
        hass.data[_CLOUD_KEY] = fetch
        return self.json(fetch.status())

    @_entry_view
    async def delete(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        fetch: CloudFetch | None = request.app["hass"].data.get(_CLOUD_KEY)
        if fetch is not None:
            await fetch.cancel()
        return self.json(_fetch_status(fetch))


def _selection_ok(body: Any) -> bool:
    """`{"add": [ids], "update": [ids], "remove": [ids]}`, each optional."""
    from . import bridge_sync

    return isinstance(body, dict) and all(
        isinstance(ids := body.get(k, []), list) and all(isinstance(i, str) for i in ids)
        for k in (bridge_sync.ADD, bridge_sync.UPDATE, bridge_sync.REMOVE))


class BridgeView(_View):
    """The cloud list against what the bridge holds, through a rustuya-manager session like the options flow's
    `bridge_sync` step (and sharing its lock: one session at a time). GET: every device with its category. POST
    `{"add": [...], "update": [...], "remove": [...]}`: those commands (only for ids the diff has in that category),
    then the list again. `online` is the running service's link state by id."""

    url = "/api/rustuya/bridge"
    name = "api:rustuya:bridge"
    SETTLE = 1.5            # seconds for the bridge's replies to land before the list is read again after a POST

    @_entry_view
    async def get(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        return await self._session(request, entry, runtime, None)

    @_entry_view
    async def post(self, request: web.Request, entry: Any, runtime: Any) -> web.Response:
        body = await _json_body(request)
        if not _selection_ok(body):
            return self.json_message('the body is {"add": [ids], "update": [ids], "remove": [ids]}',
                                     HTTPStatus.BAD_REQUEST)
        return await self._session(request, entry, runtime, body)

    async def _session(self, request: web.Request, entry: Any, runtime: Any,
                       selection: dict[str, list[str]] | None) -> web.Response:
        from . import bridge_sync, manager_session

        if not manager_session.available():
            return self.json_message("rustuya-manager is not installed", HTTPStatus.NOT_IMPLEMENTED)
        try:
            manager = await manager_session.open_for_entry(request.app["hass"], entry.data)
        except manager_session.Busy:
            return self.json_message("a Tuya login or device sync window is open; close it and try again",
                                     HTTPStatus.CONFLICT)
        except Exception as e:  # noqa: BLE001 -- broker down, bridge not answering: any of it is a 502
            return self.json_message(f"cannot reach the bridge: {e}", HTTPStatus.BAD_GATEWAY)
        try:
            diff = await manager.sync()
            sent = 0
            if selection is not None:
                sent = await bridge_sync.apply(manager, diff, selection)      # gateways first, as the flows do
                if sent:
                    await asyncio.sleep(self.SETTLE)
                    diff = await manager.sync()
            state = getattr(manager, "state", None)
            return self.json({"devices": bridge_sync.listing(diff, getattr(state, "bridge", None)), "sent": sent,
                              "cloud_loaded": bool(getattr(state, "cloud", True)),
                              "online": _online(runtime.service)})
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
    from rustuya_local.bridge_client import LinkWatcher

    found = _runtime(hass)
    if found is None:
        connection.send_error(msg["id"], "not_found", "the Rustuya panel is off")
        return
    data = found[0].data
    transport = broker.transport(data, f"rustuya-panel-{secrets.token_hex(4)}")
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
