"""Setup and options: bridge mode (external rustuya-bridge or embedded pyrustuyabridge), the broker, and onboarding
through rustuya-manager's `Manager` — the Tuya Cloud QR login wizard and registering devices on the bridge
(`architecture_device_management_via_manager`: this flow drives `Manager`, never rolls its own bridge protocol).

Setup asks only for the bridge mode, the broker and the IL prefix, then offers the (optional) cloud login; closing the
window anywhere in the login finishes the setup without it (`async_remove`). Everything else starts at its default —
the bridge topic root, the IL source, the device file, the embedded bridge's state file and log level — and is changed
later in the panel's Settings card. The one exception: an external bridge the check does not find on the default root
gets the root field on the form, since it may simply run on another one.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import secrets
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.helpers import selector

from . import bridge_sync, broker, manager_session
from .const import (
    BRIDGE_EMBEDDED,
    BRIDGE_EXTERNAL,
    CONF_BRIDGE_LOG_LEVEL,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_BRIDGE_STATE_FILE,
    CONF_BROKER_HOST,
    CONF_BROKER_PASSWORD,
    CONF_BROKER_PORT,
    CONF_BROKER_USERNAME,
    CONF_DEVICES_PATH,
    CONF_IL_PREFIX,
    CONF_IL_SOURCE,
    CONF_PANEL,
    DEFAULT_BRIDGE_LOG_LEVEL,
    DEFAULT_BRIDGE_ROOT,
    DEFAULT_BRIDGE_STATE_FILE,
    DEFAULT_BROKER_HOST,
    DEFAULT_BROKER_PORT,
    DEFAULT_DEVICES_FILE,
    DEFAULT_IL_PREFIX,
    DEFAULT_IL_SOURCE,
    DEFAULT_OPTIONS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
PROBE_TIMEOUT = 5.0      # seconds each for the broker to accept us and the bridge's retained config to arrive
TICK = 0.1               # seconds between looks at the login's state while its progress shows
TICKS = 50               # looks before the progress step is redrawn anyway


def _qr_schema(qr_url: str) -> vol.Schema:
    """A real `QrCodeSelector` (the same one HA core's own Tuya integration uses for this exact login flow),
    not a markdown image data URL: the frontend renders the QR client-side from `qr_url` itself, which HA's
    description-text markdown does not reliably do for a `data:` image. `scanned` carries no real input --
    submitting it (with anything or nothing) is just how the user tells this flow to check again."""
    return vol.Schema({vol.Optional("scanned"): selector.QrCodeSelector(
        config=selector.QrCodeSelectorConfig(
            data=qr_url, scale=5, error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE))})


def _broker_schema(defaults: dict[str, Any] | None = None, *, root: bool = False) -> vol.Schema:
    d = defaults or {}
    fields = {
        vol.Required(CONF_BROKER_HOST, default=d.get(CONF_BROKER_HOST, DEFAULT_BROKER_HOST)): str,
        vol.Required(CONF_BROKER_PORT, default=d.get(CONF_BROKER_PORT, DEFAULT_BROKER_PORT)): int,
        vol.Optional(CONF_BROKER_USERNAME, default=d.get(CONF_BROKER_USERNAME, "")): str,
        vol.Optional(CONF_BROKER_PASSWORD, default=d.get(CONF_BROKER_PASSWORD, "")): str,
        vol.Required(CONF_IL_PREFIX, default=d.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX)): str,
    }
    if root:
        fields[vol.Required(CONF_BRIDGE_ROOT, default=d.get(CONF_BRIDGE_ROOT, DEFAULT_BRIDGE_ROOT))] = str
    return vol.Schema(fields)


async def _probe_bridge(hass, data: dict[str, Any]) -> str | None:
    """Is an external rustuya-bridge actually running on this broker and root? A running bridge keeps its retained
    `{root}/bridge/config` published (and clears it when it goes away), so that arriving is the sign. `None` when it
    is there, otherwise the error key: `cannot_connect` (the broker is unreachable or refuses the credentials) or
    `bridge_not_found` (the broker answers but no bridge is on that root)."""
    await broker.import_runtime(hass)
    from rustuya_local.bridge_client import payload_text

    transport = broker.transport(data, f"rustuya-probe-{secrets.token_hex(4)}")
    try:
        await transport.connect(timeout=PROBE_TIMEOUT)
    except Exception as e:  # noqa: BLE001 -- refused, unresolvable, timed out: all mean the broker is not usable
        _LOGGER.debug("the broker at %s did not accept a connection: %r", broker.url(data), e)
        with contextlib.suppress(Exception):
            await transport.close()
        return "cannot_connect"
    seen = asyncio.Event()

    def on_config(msg: Any) -> None:
        if payload_text(msg.payload).strip():          # an empty retained config is a bridge that went offline
            seen.set()

    try:
        await transport.subscribe(f"{data[CONF_BRIDGE_ROOT]}/bridge/config", on_config)
        await asyncio.wait_for(seen.wait(), PROBE_TIMEOUT)
    except TimeoutError:
        return "bridge_not_found"
    finally:
        with contextlib.suppress(Exception):
            await transport.close()
    return None


async def _open(hass, data: dict[str, Any], *, bridge_running: bool):
    """A Manager session for an entry's `data`, or the flow aborts: another flow's session is still open
    (`manager_session.Busy`). With the embedded bridge and no entry running it (`bridge_running` false: setup, or an
    entry that failed to start), the session runs the bridge itself on the same state file, so a device registered
    here reaches a bridge and is there when the entry's own bridge starts."""
    try:
        return await manager_session.open_for_entry(hass, data, run_bridge=not bridge_running)
    except manager_session.Busy as e:
        raise AbortFlow("manager_busy") from e


class _ManagerSteps:
    """What the config flow (setup) and the options flow share of the steps on a Manager session: the Tuya Cloud
    login's user-code form, its QR step and progress, and the device sync form. Each flow opens its session in
    `_async_manager()` and names the step the sync form is shown on."""

    _manager: Any = None
    _scan_shown = False
    _sync_shown = False

    async def _async_manager(self) -> Any:
        raise NotImplementedError

    async def _async_close(self) -> None:
        if self._manager is not None:
            await manager_session.close_manager(self._manager)
            self._manager = None

    def _new_login(self) -> None:
        """A new login: the steps after it take a submit only once they have shown their form again."""
        self._scan_shown = self._sync_shown = False

    async def _async_login_form(self, step_id: str, user_input: dict[str, Any] | None) -> FlowResult:
        """The login's user-code form (the saved code filled in), or once submitted the login started."""
        manager = await self._async_manager()
        if user_input is not None:
            self._new_login()
            await manager.wizard.start(user_input.get("user_code") or None, scan=False)
            return await self.async_step_cloud_wizard_progress()
        saved_code = await manager.wizard.read_saved_user_code()
        return self.async_show_form(step_id=step_id,
                                    data_schema=vol.Schema({vol.Optional("user_code", default=saved_code or ""): str}))

    def _login_running(self, session: Any) -> FlowResult:
        """A login neither done nor failed: its QR to scan once there is one, its progress until then."""
        if session.qr_url:
            return self.async_show_progress_done(next_step_id="cloud_wizard_scan")
        return self.async_show_progress(step_id="cloud_wizard_progress", progress_action="cloud_wizard_working",
                                        description_placeholders={"message": session.message},
                                        progress_task=self.hass.async_create_task(self._async_wizard_tick()))

    async def async_step_cloud_wizard_scan(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """`async_show_progress` only takes plain description text -- no selector, so a QR needs an actual form
        step; go back through `cloud_wizard_progress` on submit (`scanned` carries no real data) to reuse its
        state check instead of duplicating it here."""
        if user_input is not None and self._scan_shown:       # a submit of the QR form, not a forwarded input
            return await self.async_step_cloud_wizard_progress()
        self._scan_shown = True
        return self.async_show_form(step_id="cloud_wizard_scan",
                                    data_schema=_qr_schema(self._manager.wizard.session.qr_url))

    async def _async_wizard_tick(self) -> None:
        """`async_show_progress` needs a task to await; the wizard already runs in the background
        (`WizardManager._run`), so this just waits for its state to change (or a short cap) so the
        frontend's re-poll of `cloud_wizard_progress` promptly redraws instead of only on its own timer."""
        from rustuya_manager.wizard import WizardState

        session = self._manager.wizard.session
        start_state = session.state
        for _ in range(TICKS):
            if session.state != start_state or session.state in (WizardState.DONE, WizardState.ERROR):
                return
            await asyncio.sleep(TICK)

    def _sync_form(self, step_id: str, diff: Any, error: str | None = None) -> FlowResult:
        self._sync_shown = True
        return self.async_show_form(step_id=step_id, data_schema=bridge_sync.schema(diff),
                                    errors={"base": "publish_failed"} if error is not None else None,
                                    description_placeholders={**bridge_sync.placeholders(diff), "error": error or ""})

    def _sync_submitted(self, user_input: dict[str, Any] | None) -> bool:
        """A submission of the sync form, not the input a `progress_done` hands on (the QR step's empty submit, which
        is no selection)."""
        return user_input is not None and self._sync_shown

    async def _async_send_selection(self, step_id: str, diff: Any, user_input: dict[str, Any]) -> FlowResult | None:
        """The selection sent to the bridge and the session closed: None. The form again, with the error, when a
        publish failed."""
        try:
            await bridge_sync.apply(self._manager, diff, user_input)
        except RuntimeError as e:
            return self._sync_form(step_id, diff, str(e))
        await self._async_close()
        return None


class RustuyaConfigFlow(_ManagerSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2       # 1.2: the default files moved under `.storage/rustuya/` (`async_migrate_entry`)

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._in_login = False
        self._error_shown = False

    # ---- bridge mode ---------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="user", menu_options=["external", "embedded"])

    def _defaults(self, mode: str) -> dict[str, Any]:
        """What setup no longer asks: each at its default, changed later in the panel's Settings card."""
        data = {CONF_BRIDGE_MODE: mode, CONF_BRIDGE_ROOT: DEFAULT_BRIDGE_ROOT,
                CONF_DEVICES_PATH: self.hass.config.path(DEFAULT_DEVICES_FILE),
                CONF_IL_PREFIX: DEFAULT_IL_PREFIX, CONF_IL_SOURCE: DEFAULT_IL_SOURCE}
        if mode == BRIDGE_EMBEDDED:
            data.update({CONF_BRIDGE_STATE_FILE: self.hass.config.path(DEFAULT_BRIDGE_STATE_FILE),
                         CONF_BRIDGE_LOG_LEVEL: DEFAULT_BRIDGE_LOG_LEVEL})
        return data

    async def async_step_external(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        ask_root = False
        if user_input is not None:
            user_input = {**user_input, CONF_IL_PREFIX: user_input[CONF_IL_PREFIX].strip()}
            if not broker.topic_ok(user_input[CONF_IL_PREFIX]):
                errors[CONF_IL_PREFIX] = "invalid_prefix"
                ask_root = CONF_BRIDGE_ROOT in user_input
            else:
                data = {**self._defaults(BRIDGE_EXTERNAL), **user_input}
                error = await _probe_bridge(self.hass, data)
                if error is None:
                    self._data.update(data)
                    return await self.async_step_onboard()
                errors["base"] = error
                # not on the default root: it may run on another one, so the form asks for it from now on
                ask_root = error == "bridge_not_found" or CONF_BRIDGE_ROOT in user_input
        # what was typed stays on the form after an error, rather than falling back to the defaults
        return self.async_show_form(step_id="external", data_schema=_broker_schema(user_input, root=ask_root),
                                    errors=errors)

    async def async_step_embedded(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**user_input, CONF_IL_PREFIX: user_input[CONF_IL_PREFIX].strip()}
            if not broker.topic_ok(user_input[CONF_IL_PREFIX]):
                errors[CONF_IL_PREFIX] = "invalid_prefix"
            elif importlib.util.find_spec("pyrustuyabridge") is None:
                errors["base"] = "pyrustuyabridge_missing"
            else:
                self._data.update({**self._defaults(BRIDGE_EMBEDDED), **user_input})
                return await self.async_step_onboard()
        return self.async_show_form(step_id="embedded", data_schema=_broker_schema(user_input), errors=errors)

    # ---- onboarding: the Tuya Cloud login through Manager, or not now ---------------

    async def async_step_onboard(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Log in now, or finish: the login is also in Configure later, and a device file rustuya-manager already
        keeps elsewhere is pointed at from the panel's Settings card."""
        if not manager_session.available():
            return await self.async_step_finish()
        return self.async_show_menu(step_id="onboard", menu_options=["cloud_wizard_start", "finish"])

    # ---- Tuya Cloud QR login wizard (rustuya_manager.Manager.wizard) ----------------

    async def _async_manager(self) -> Any:
        if self._manager is None:
            self._manager = await _open(self.hass, self._data, bridge_running=False)
        return self._manager

    def _new_login(self) -> None:
        super()._new_login()
        self._error_shown = False

    async def async_step_cloud_wizard_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        await self._async_manager()
        self._in_login = True           # from here, closing the window finishes the setup without the login
        return await self._async_login_form("cloud_wizard_start", user_input)

    async def async_step_cloud_wizard_progress(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from rustuya_manager.wizard import WizardState

        session = self._manager.wizard.session
        if session.state == WizardState.DONE:
            return self.async_show_progress_done(next_step_id="sync_devices")
        if session.state == WizardState.ERROR:
            return self.async_show_progress_done(next_step_id="cloud_wizard_error")
        return self._login_running(session)

    async def async_step_cloud_wizard_error(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        session = self._manager.wizard.session
        # only the retry form's own submit: a login that fails at once reaches this through `progress_done` with the
        # user-code form's input, which has no `retry`
        if user_input is not None and self._error_shown:
            if user_input["retry"]:
                return await self.async_step_cloud_wizard_start()
            await self._async_close()
            return await self.async_step_finish()
        self._error_shown = True
        return self.async_show_form(
            step_id="cloud_wizard_error", data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
            description_placeholders={"error": session.error or "unknown error"},
        )

    # ---- register missing devices on the bridge (Manager.sync / add_device) ---------

    async def async_step_sync_devices(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        diff = await self._manager.sync()
        if self._sync_submitted(user_input):
            return await self._async_send_selection("sync_devices", diff, user_input) or await self.async_step_finish()
        if not bridge_sync.has_changes(diff):
            await self._async_close()
            return await self.async_step_finish()
        return self._sync_form("sync_devices", diff)

    # ---- finish --------------------------------------------------------------------

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        self._in_login = False
        return self.async_create_entry(title="Rustuya", data=self._data, options=dict(DEFAULT_OPTIONS))

    async def async_step_import(self, data: dict[str, Any]) -> FlowResult:
        """The setup a closed login window left: the broker and the rest were all given, only the login is skipped."""
        self._data = dict(data)
        return await self.async_step_finish()

    @callback
    def async_remove(self) -> None:
        """Every path a flow leaves progress on calls this (HA's FlowHandler, a sync `@callback` despite the name):
        close a Manager left open by an abandoned wizard so its MQTT connection does not leak; and when the window was
        closed during the login, finish the setup without it (an `import` flow with what was given), since the login
        is optional and can be done later from Configure."""
        finish = self._in_login
        self._in_login = False

        async def close_then_finish() -> None:
            # the session first (with the embedded bridge it runs one): the entry's own bridge must not start while it
            # is still up, on the same root and state file
            await self._async_close()
            if finish:
                await self.hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=dict(self._data))

        if self._manager is not None or finish:
            self.hass.async_create_task(close_then_finish())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> RustuyaOptionsFlow:
        return RustuyaOptionsFlow(config_entry)


class RustuyaOptionsFlow(_ManagerSteps, config_entries.OptionsFlow):
    """Maintenance after setup: tuning, and the same Manager-backed onboarding steps to add or remove devices
    later without re-adding the integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # `self.config_entry` is a read-only property on `OptionsFlow` in current Home Assistant (not settable, and
        # not available until after __init__); `config_entry` is accepted here only to match the signature
        # `async_get_options_flow` is called with, unused otherwise.
        self._logged_in = False     # a login wrote a new device file: hand it to the entry however this flow ends

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        # the panel's own Hide panel removes it; here only adding it, or its link once added
        panel = ["panel_open"] if self.config_entry.options.get(CONF_PANEL) else ["panel_show"]
        options = [*panel, "cloud_wizard", "bridge_sync"] if manager_session.available() else panel
        return self.async_show_menu(step_id="init", menu_options=options)

    # ---- the panel: one click, then a link to it (a flow cannot navigate the page itself) --------------------------

    def _panel_link(self, reason: str) -> FlowResult:
        from .panel import PANEL_URL

        return self.async_abort(reason=reason, description_placeholders={"url": f"/{PANEL_URL}"})

    async def async_step_panel_show(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Enable the panel; the update listener applies the change without a restart. The other options
        (hazardous control, unused data points, the pack) are in its Options card."""
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_PANEL: True})
        return self._panel_link("panel_shown")

    async def async_step_panel_open(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._panel_link("panel_open")

    async def _async_manager(self) -> Any:
        if self._manager is None:
            entry = self.config_entry
            self._manager = await _open(self.hass, dict(entry.data),
                                        bridge_running=entry.state is config_entries.ConfigEntryState.LOADED)
        return self._manager

    async def async_step_cloud_wizard(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._async_login_form("cloud_wizard", user_input)

    async def async_step_cloud_wizard_progress(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from rustuya_manager.wizard import WizardState

        session = self._manager.wizard.session
        if session.state == WizardState.DONE:
            self._logged_in = True
            return self.async_show_progress_done(next_step_id="bridge_sync")
        if session.state == WizardState.ERROR:
            await self._async_close()
            return self.async_abort(reason="wizard_failed", description_placeholders={"error": session.error or ""})
        return self._login_running(session)

    async def async_step_bridge_sync(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Every device, in sync or not: add the missing, update the mismatched, remove anything on the bridge
        (orphans included). Nothing is pre-selected."""
        manager = await self._async_manager()
        diff = await manager.sync()
        if self._sync_submitted(user_input):
            if (failed := await self._async_send_selection("bridge_sync", diff, user_input)) is not None:
                return failed
            # nothing watches tuyadevices.json (it only changes through the cloud login here): tell the running Hub,
            # or start the entry if it is not running
            self._logged_in = False
            await self._async_hand_over()
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        if not bridge_sync.has_devices(diff):
            await self._async_close()
            return self.async_abort(reason="no_devices")
        return self._sync_form("bridge_sync", diff)

    async def _async_hand_over(self) -> None:
        from . import async_refresh_devices

        await async_refresh_devices(self.hass, self.config_entry)

    @callback
    def async_remove(self) -> None:
        """Closed (or finished): the session closes, and a login that wrote a new device file is handed to the entry
        even when the device step was never submitted (closed, or nothing to sync) — after the session, since with the
        embedded bridge it may run one."""
        logged_in, self._logged_in = self._logged_in, False

        async def close_then_hand_over() -> None:
            await self._async_close()
            if logged_in:
                await self._async_hand_over()

        if self._manager is not None or logged_in:
            self.hass.async_create_task(close_then_hand_over())
