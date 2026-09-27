"""The advanced panel: a sidebar page for `<config>/rustuya_converters`, turned on by the `panel` option.

The panel is registered while the entry is loaded with the option on, and removed on unload (an options change reloads
the entry, so it follows the option). The API views and the static path cannot be unregistered in Home Assistant, so
they are registered once and answer 404 whenever no loaded entry has the panel on. The files are the same ones the
manager plugin tab edits (`rustuya_local.manager_plugin.api`); `.py` converters run in Home Assistant's process, so
every view is for administrators only.
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CONF_PACK, CONF_PANEL, CONVERTERS_DIR, DOMAIN

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
        for view in (ConvertersView, ConverterView, PackView):
            hass.http.register_view(view())
    if entry.options.get(CONF_PANEL, False) and PANEL_URL not in hass.data.get(frontend.DATA_PANELS, {}):
        version = (await async_get_integration(hass, DOMAIN)).version     # a new release is not served from cache
        await panel_custom.async_register_panel(
            hass, frontend_url_path=PANEL_URL, webcomponent_name="rustuya-panel",
            module_url=f"{STATIC_URL}/rustuya-panel.js?v={version}",
            sidebar_title="Rustuya", sidebar_icon="mdi:tune-variant", require_admin=True,
            config_panel_domain=DOMAIN)


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


class PackView(_View):
    """POST: sync the override pack now rather than at the end of its day; `pack` in the list shows the result."""

    url = "/api/rustuya/pack"
    name = "api:rustuya:pack"

    async def post(self, request: web.Request) -> web.Response:
        found = self._loaded(request)
        if isinstance(found, web.Response):
            return found
        return self.json({"started": found[1].service.sync_pack_now()})
