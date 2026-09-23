"""Setup and options: bridge mode (external rustuya-bridge or embedded pyrustuyabridge), the broker, and onboarding
through rustuya-manager's `Manager` — the Tuya Cloud QR login wizard and registering devices on the bridge
(`architecture_device_management_via_manager`: this flow drives `Manager`, never rolls its own bridge protocol).

The wizard step is optional: a user who already has a `tuyadevices.json` (from running rustuya-manager separately)
can skip straight to the IL step and point at that file.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from . import bridge_sync, manager_session
from .const import (
    BRIDGE_EMBEDDED,
    BRIDGE_EXTERNAL,
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
    DEFAULT_BRIDGE_ROOT,
    DEFAULT_BRIDGE_STATE_FILE,
    DEFAULT_BROKER_PORT,
    DEFAULT_DEVICES_FILE,
    DEFAULT_IL_PREFIX,
    DEFAULT_IL_SOURCE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _broker_url(data: dict[str, Any]) -> str:
    return f"mqtt://{data[CONF_BROKER_HOST]}:{data[CONF_BROKER_PORT]}"


def _qr_schema(qr_url: str) -> vol.Schema:
    """A real `QrCodeSelector` (the same one HA core's own Tuya integration uses for this exact login flow),
    not a markdown image data URL: the frontend renders the QR client-side from `qr_url` itself, which HA's
    description-text markdown does not reliably do for a `data:` image. `scanned` carries no real input --
    submitting it (with anything or nothing) is just how the user tells this flow to check again."""
    return vol.Schema({vol.Optional("scanned"): selector.QrCodeSelector(
        config=selector.QrCodeSelectorConfig(
            data=qr_url, scale=5, error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE))})


def _broker_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema({
        vol.Required(CONF_BROKER_HOST, default=d.get(CONF_BROKER_HOST, "localhost")): str,
        vol.Required(CONF_BROKER_PORT, default=d.get(CONF_BROKER_PORT, DEFAULT_BROKER_PORT)): int,
        vol.Optional(CONF_BROKER_USERNAME, default=d.get(CONF_BROKER_USERNAME, "")): str,
        vol.Optional(CONF_BROKER_PASSWORD, default=d.get(CONF_BROKER_PASSWORD, "")): str,
        vol.Required(CONF_BRIDGE_ROOT, default=d.get(CONF_BRIDGE_ROOT, DEFAULT_BRIDGE_ROOT)): str,
    })


class RustuyaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._manager: Any = None
        self._wizard_task: asyncio.Task | None = None

    # ---- bridge mode ---------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="user", menu_options=["external", "embedded"])

    async def async_step_external(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input, **{CONF_BRIDGE_MODE: BRIDGE_EXTERNAL})
            return await self.async_step_devices()
        return self.async_show_form(step_id="external", data_schema=_broker_schema())

    async def async_step_embedded(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            import importlib.util
            if importlib.util.find_spec("pyrustuyabridge") is None:
                errors["base"] = "pyrustuyabridge_missing"
            else:
                self._data.update(user_input, **{CONF_BRIDGE_MODE: BRIDGE_EMBEDDED})
                return await self.async_step_devices()
        schema = _broker_schema().extend({
            vol.Required(CONF_BRIDGE_STATE_FILE, default=self.hass.config.path(DEFAULT_BRIDGE_STATE_FILE)): str,
            vol.Required(CONF_BRIDGE_LOG_LEVEL, default="warn"): vol.In(["error", "warn", "info", "debug"]),
        })
        return self.async_show_form(step_id="embedded", data_schema=schema, errors=errors)

    # ---- devices: onboard through Manager, or point at an existing file -------------

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data[CONF_DEVICES_PATH] = user_input[CONF_DEVICES_PATH]
            if user_input["onboard"] and manager_session.available():
                return await self.async_step_cloud_wizard_start()
            return await self.async_step_il()
        default_path = self.hass.config.path(DEFAULT_DEVICES_FILE)
        schema = vol.Schema({
            vol.Required(CONF_DEVICES_PATH, default=default_path): str,
            vol.Required("onboard", default=manager_session.available()): bool,
        })
        placeholders = {} if manager_session.available() else {"warning": "rustuya-manager is not installed"}
        return self.async_show_form(step_id="devices", data_schema=schema, description_placeholders=placeholders)

    # ---- Tuya Cloud QR login wizard (rustuya_manager.Manager.wizard) ----------------

    async def _async_open_manager(self):
        if self._manager is None:
            self._manager = await manager_session.open_manager(
                broker=_broker_url(self._data), root=self._data[CONF_BRIDGE_ROOT],
                devices_path=self._data[CONF_DEVICES_PATH],
                username=self._data.get(CONF_BROKER_USERNAME) or None,
                password=self._data.get(CONF_BROKER_PASSWORD) or None,
            )
        return self._manager

    async def _async_close_manager(self) -> None:
        if self._manager is not None:
            await manager_session.close_manager(self._manager)
            self._manager = None

    async def async_step_cloud_wizard_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        manager = await self._async_open_manager()
        if user_input is not None:
            await manager.wizard.start(user_input.get("user_code") or None, scan=False)
            return await self.async_step_cloud_wizard_progress()
        saved_code = await manager.wizard.read_saved_user_code()
        return self.async_show_form(
            step_id="cloud_wizard_start",
            data_schema=vol.Schema({vol.Optional("user_code", default=saved_code or ""): str}),
        )

    async def async_step_cloud_wizard_progress(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from rustuya_manager.wizard import WizardState

        session = self._manager.wizard.session
        if session.state == WizardState.DONE:
            return self.async_show_progress_done(next_step_id="sync_devices")
        if session.state == WizardState.ERROR:
            return self.async_show_progress_done(next_step_id="cloud_wizard_error")
        if session.qr_url:
            return self.async_show_progress_done(next_step_id="cloud_wizard_scan")
        return self.async_show_progress(
            step_id="cloud_wizard_progress", progress_action="cloud_wizard_working",
            description_placeholders={"message": session.message},
            progress_task=self.hass.async_create_task(self._async_wait_wizard_tick()),
        )

    async def async_step_cloud_wizard_scan(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """`async_show_progress` only takes plain description text -- no selector, so a QR needs an actual form
        step; go back through `cloud_wizard_progress` on submit (`scanned` carries no real data) to reuse its
        state check instead of duplicating it here."""
        if user_input is not None:
            return await self.async_step_cloud_wizard_progress()
        return self.async_show_form(step_id="cloud_wizard_scan", data_schema=_qr_schema(self._manager.wizard.session.qr_url))

    async def _async_wait_wizard_tick(self) -> None:
        """`async_show_progress` needs a task to await; the wizard already runs in the background
        (`WizardManager._run`), so this just waits for its state to change (or a short cap) so the
        frontend's re-poll of `cloud_wizard_progress` promptly redraws instead of only on its own timer."""
        from rustuya_manager.wizard import WizardState

        session = self._manager.wizard.session
        start_state = session.state
        for _ in range(50):
            if session.state != start_state or session.state in (WizardState.DONE, WizardState.ERROR):
                return
            await asyncio.sleep(0.1)

    async def async_step_cloud_wizard_error(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        session = self._manager.wizard.session
        if user_input is not None:
            if user_input["retry"]:
                return await self.async_step_cloud_wizard_start()
            await self._async_close_manager()
            return await self.async_step_il()
        return self.async_show_form(
            step_id="cloud_wizard_error", data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
            description_placeholders={"error": session.error or "unknown error"},
        )

    # ---- register missing devices on the bridge (Manager.sync / add_device) ---------

    async def async_step_sync_devices(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        manager = self._manager
        diff = await manager.sync()
        if user_input is not None:
            try:
                await bridge_sync.apply(manager, diff, user_input)
            except RuntimeError as e:
                return self.async_show_form(
                    step_id="sync_devices", data_schema=bridge_sync.schema(diff), errors={"base": "publish_failed"},
                    description_placeholders={**bridge_sync.placeholders(diff), "error": str(e)})
            await self._async_close_manager()
            return await self.async_step_il()
        if not bridge_sync.has_changes(diff):
            await self._async_close_manager()
            return await self.async_step_il()
        return self.async_show_form(step_id="sync_devices", data_schema=bridge_sync.schema(diff),
                                    description_placeholders={**bridge_sync.placeholders(diff), "error": ""})

    # ---- IL side and finish ----------------------------------------------------------

    async def async_step_il(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Rustuya", data=self._data, options={
                CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False,
            })
        schema = vol.Schema({
            vol.Required(CONF_IL_PREFIX, default=DEFAULT_IL_PREFIX): str,
            vol.Required(CONF_IL_SOURCE, default=DEFAULT_IL_SOURCE): str,
        })
        return self.async_show_form(step_id="il", data_schema=schema)

    @callback
    def async_remove(self) -> None:
        """Every path a flow leaves progress on calls this (HA's FlowHandler, a sync `@callback` despite the name);
        close a Manager left open by an abandoned wizard so its MQTT connection does not leak (mirrors the legacy
        flow's own guarantee)."""
        if self._manager is not None:
            self.hass.async_create_task(self._async_close_manager())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> RustuyaOptionsFlow:
        return RustuyaOptionsFlow(config_entry)



class RustuyaOptionsFlow(config_entries.OptionsFlow):
    """Maintenance after setup: tuning, and the same Manager-backed onboarding steps to add or remove devices
    later without re-adding the integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # `self.config_entry` is a read-only property on `OptionsFlow` in current Home Assistant (not settable, and
        # not available until after __init__); `config_entry` is accepted here only to match the signature
        # `async_get_options_flow` is called with, unused otherwise.
        self._manager: Any = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = ["tuning"]
        if manager_session.available():
            options = ["tuning", "cloud_wizard", "bridge_sync"]
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_tuning(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_ALLOW_HAZARDOUS, default=current.get(CONF_ALLOW_HAZARDOUS, False)): bool,
            vol.Required(CONF_EXPOSE_UNUSED, default=current.get(CONF_EXPOSE_UNUSED, False)): bool,
        })
        return self.async_show_form(step_id="tuning", data_schema=schema)

    async def _async_manager(self):
        if self._manager is None:
            data = self.config_entry.data
            self._manager = await manager_session.open_manager(
                broker=_broker_url(data), root=data[CONF_BRIDGE_ROOT], devices_path=data[CONF_DEVICES_PATH],
                username=data.get(CONF_BROKER_USERNAME) or None, password=data.get(CONF_BROKER_PASSWORD) or None,
            )
        return self._manager

    async def async_step_cloud_wizard(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        manager = await self._async_manager()
        if user_input is not None:
            await manager.wizard.start(user_input.get("user_code") or None, scan=False)
            return await self.async_step_cloud_wizard_progress()
        saved_code = await manager.wizard.read_saved_user_code()
        return self.async_show_form(step_id="cloud_wizard",
                                    data_schema=vol.Schema({vol.Optional("user_code", default=saved_code or ""): str}))

    async def async_step_cloud_wizard_progress(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from rustuya_manager.wizard import WizardState

        session = self._manager.wizard.session
        if session.state == WizardState.DONE:
            return self.async_show_progress_done(next_step_id="bridge_sync")
        if session.state == WizardState.ERROR:
            await self._async_close()
            return self.async_abort(reason="wizard_failed", description_placeholders={"error": session.error or ""})
        if session.qr_url:
            return self.async_show_progress_done(next_step_id="cloud_wizard_scan")
        return self.async_show_progress(step_id="cloud_wizard_progress", progress_action="cloud_wizard_working",
                                        description_placeholders={"message": session.message},
                                        progress_task=self.hass.async_create_task(self._tick()))

    async def async_step_cloud_wizard_scan(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return await self.async_step_cloud_wizard_progress()
        return self.async_show_form(step_id="cloud_wizard_scan", data_schema=_qr_schema(self._manager.wizard.session.qr_url))

    async def _tick(self) -> None:
        from rustuya_manager.wizard import WizardState

        session = self._manager.wizard.session
        start_state = session.state
        for _ in range(50):
            if session.state != start_state or session.state in (WizardState.DONE, WizardState.ERROR):
                return
            await asyncio.sleep(0.1)

    async def async_step_bridge_sync(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Every device, in sync or not: add the missing, update the mismatched, remove anything on the bridge
        (orphans included). Nothing is pre-selected."""
        manager = await self._async_manager()
        diff = await manager.sync()
        if user_input is not None:
            try:
                await bridge_sync.apply(manager, diff, user_input)
            except RuntimeError as e:
                return self.async_show_form(
                    step_id="bridge_sync", data_schema=bridge_sync.schema(diff), errors={"base": "publish_failed"},
                    description_placeholders={**bridge_sync.placeholders(diff), "error": str(e)})
            await self._async_close()
            # nothing watches tuyadevices.json (it only changes through the cloud login here): tell the running Hub
            from . import async_refresh_devices

            await async_refresh_devices(self.hass, self.config_entry)
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        if not bridge_sync.has_devices(diff):
            await self._async_close()
            return self.async_abort(reason="no_devices")
        return self.async_show_form(step_id="bridge_sync", data_schema=bridge_sync.schema(diff),
                                    description_placeholders={**bridge_sync.placeholders(diff), "error": ""})

    async def _async_close(self) -> None:
        if self._manager is not None:
            await manager_session.close_manager(self._manager)
            self._manager = None

    @callback
    def async_remove(self) -> None:
        if self._manager is not None:
            self.hass.async_create_task(self._async_close())
