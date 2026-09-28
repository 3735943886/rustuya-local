"""The config and options flow's own state machine, against a `FakeManager` (no real Tuya Cloud, broker or bridge —
those are exercised in test_init.py against real infrastructure instead)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fake_manager import FakeDevice, FakeDiff, FakeManager, WizardState, install
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rustuya.const import (
    CONF_ALLOW_HAZARDOUS,
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_DEVICES_PATH,
    CONF_EXPOSE_UNUSED,
    CONF_IL_PREFIX,
    CONF_PACK,
    CONF_PANEL,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def _no_real_setup():
    """A completed flow's `async_create_entry` makes Home Assistant set the entry up for real — this file is about
    the flow's own state machine, not a live MQTT connection (that belongs to test_init.py). The external step's
    bridge check is taken as passing for the same reason (test_bridge_probe.py runs it against a real broker)."""
    with (patch("custom_components.rustuya.async_setup_entry", return_value=True),
          patch("custom_components.rustuya.config_flow._probe_bridge", return_value=None)):
        yield


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def _wizard_flow(hass, manager, devices_path):
    """A `RustuyaConfigFlow` with `_data` already as it would be after menu -> external -> devices (that
    plain navigation is covered on its own by test_skipping_onboarding, through the real `hass.config_entries.flow`);
    `._manager` is the fake directly, so no config entry ever gets involved. Driven by calling its `async_step_*`
    methods directly rather than through `hass.config_entries.flow`: `async_show_progress`'s `progress_task` gets a
    Home Assistant-registered done callback that re-invokes the step on its own once it completes, which only a real
    flow going through the FlowManager has wired up — calling the methods directly sidesteps that entirely, so
    `FakeWizard.advance()` (no clock of its own) is the only thing moving the state machine, deterministically."""
    from custom_components.rustuya.config_flow import RustuyaConfigFlow
    from custom_components.rustuya.const import (
        CONF_BRIDGE_MODE,
        CONF_BRIDGE_ROOT,
        CONF_DEVICES_PATH,
    )

    flow = RustuyaConfigFlow()
    flow.hass = hass
    flow._manager = manager
    flow._data = {CONF_BRIDGE_MODE: "external", "broker_host": "h", "broker_port": 1883, "broker_username": "",
                  "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: devices_path}
    return flow


async def _drain_progress(flow, manager, max_steps=10):
    for _ in range(max_steps):
        r = await flow.async_step_cloud_wizard_progress()
        if r["type"] == "progress":
            r["progress_task"].cancel()   # driven by hand here; nothing needs Home Assistant's own tracking of it
            manager.wizard.advance()
            continue
        if r["type"] != "progress_done":
            return r
        if r["step_id"] == "cloud_wizard_scan":
            # the QR form itself is covered on its own by test_the_qr_step_shows_a_real_qr_selector; here just
            # simulate the scan being detected server-side (the wizard's own background poll, not a button click)
            manager.wizard.advance()
            continue
        return await getattr(flow, f"async_step_{r['step_id']}")()
    raise AssertionError("progress never finished")


async def test_the_user_menu_offers_external_and_embedded(hass):
    result = await _start(hass)
    assert result["type"] == "menu" and set(result["menu_options"]) == {"external", "embedded"}


BROKER = {"broker_host": "h", "broker_port": 1883, "broker_username": "", "broker_password": "", "il_prefix": "home/il"}


@pytest.mark.parametrize("manager_installed", [True, False])
async def test_setup_asks_only_for_the_broker_and_uses_the_defaults(hass, manager_installed):
    from custom_components.rustuya.const import CONF_IL_SOURCE

    with patch("custom_components.rustuya.manager_session.available", return_value=manager_installed):
        result = await _start(hass)
        r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "external"})
        assert {str(k) for k in r["data_schema"].schema} == set(BROKER)
        r = await hass.config_entries.flow.async_configure(r["flow_id"], BROKER)
        if manager_installed:                        # the cloud login is offered, and can be left for later
            assert r["type"] == "menu" and r["menu_options"] == ["cloud_wizard_start", "finish"]
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "finish"})
    assert r["type"] == "create_entry"
    assert r["data"] == {**BROKER, CONF_BRIDGE_MODE: "external", CONF_BRIDGE_ROOT: "rustuya",
                         CONF_DEVICES_PATH: hass.config.path(".storage/rustuya/tuyadevices.json"),
                         CONF_IL_PREFIX: "home/il", CONF_IL_SOURCE: "tuya"}
    assert r["options"] == {CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: True}


async def test_embedded_setup_asks_only_for_the_broker(hass, monkeypatch):
    import importlib.util

    from custom_components.rustuya.const import (
        CONF_BRIDGE_LOG_LEVEL,
        CONF_BRIDGE_STATE_FILE,
    )

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: object() if name == "pyrustuyabridge" else real_find_spec(name))
    with patch("custom_components.rustuya.manager_session.available", return_value=False):
        result = await _start(hass)
        r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "embedded"})
        assert {str(k) for k in r["data_schema"].schema} == set(BROKER)
        r = await hass.config_entries.flow.async_configure(r["flow_id"], BROKER)
    assert r["type"] == "create_entry" and r["data"][CONF_BRIDGE_MODE] == "embedded"
    assert r["data"][CONF_BRIDGE_STATE_FILE] == hass.config.path(".storage/rustuya/bridge_state.json")
    assert r["data"][CONF_BRIDGE_LOG_LEVEL] == "warn"


async def test_an_unreachable_broker_keeps_the_form_open(hass):
    typed = {"broker_host": "mqtt.lan", "broker_port": 1884, "broker_username": "u", "broker_password": "p",
             "il_prefix": "il"}
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "external"})
    with patch("custom_components.rustuya.config_flow._probe_bridge", return_value="cannot_connect"):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], typed)
    assert r["type"] == "form" and r["step_id"] == "external" and r["errors"] == {"base": "cannot_connect"}
    assert {str(k): k.default() for k in r["data_schema"].schema} == typed      # what was typed is kept


async def test_a_bridge_not_on_the_default_root_asks_for_its_root(hass):
    typed = {"broker_host": "mqtt.lan", "broker_port": 1884, "broker_username": "u", "broker_password": "p",
             "il_prefix": "il"}
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "external"})
    with patch("custom_components.rustuya.config_flow._probe_bridge", return_value="bridge_not_found") as probe:
        r = await hass.config_entries.flow.async_configure(r["flow_id"], typed)
    assert probe.call_args.args[1][CONF_BRIDGE_ROOT] == "rustuya"
    assert r["errors"] == {"base": "bridge_not_found"}
    assert {str(k): k.default() for k in r["data_schema"].schema} == {**typed, CONF_BRIDGE_ROOT: "rustuya"}
    with (patch("custom_components.rustuya.config_flow._probe_bridge", return_value=None) as probe,
          patch("custom_components.rustuya.manager_session.available", return_value=False)):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {**typed, CONF_BRIDGE_ROOT: "rb"})
    assert probe.call_args.args[1][CONF_BRIDGE_ROOT] == "rb"
    assert r["type"] == "create_entry" and r["data"][CONF_BRIDGE_ROOT] == "rb"


@pytest.mark.parametrize("mode", ["external", "embedded"])
@pytest.mark.parametrize("prefix", ["il/#", "a//b", "+", " "])
async def test_an_il_prefix_that_is_no_topic_is_refused(hass, mode, prefix):
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": mode})
    with patch("custom_components.rustuya.config_flow._probe_bridge", return_value=None) as probe:
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {**BROKER, "il_prefix": prefix})
    assert r["type"] == "form" and r["errors"] == {"il_prefix": "invalid_prefix"} and not probe.called


async def _to_login(hass, monkeypatch, manager):
    install(monkeypatch, manager)
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "external"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], BROKER)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "cloud_wizard_start"})
    assert r["type"] == "form" and r["step_id"] == "cloud_wizard_start"
    return r


async def test_closing_the_login_window_finishes_the_setup_without_it(hass, monkeypatch):
    manager = FakeManager()
    r = await _to_login(hass, monkeypatch, manager)
    hass.config_entries.flow.async_abort(r["flow_id"])             # what closing the window does
    await hass.async_block_till_done()
    entry, = hass.config_entries.async_entries(DOMAIN)
    assert entry.data[CONF_IL_PREFIX] == "home/il" and entry.data[CONF_BRIDGE_MODE] == "external"
    assert entry.options == {CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False, CONF_PACK: True}
    assert manager.closed


async def test_finishing_the_login_adds_no_second_entry(hass, tmp_path, monkeypatch):
    """A login that ends normally creates the entry itself; the `async_remove` that follows every finished flow must
    not add another by an import. Driven by calling the steps (like `_wizard_flow`), so any entry is the import's."""
    manager = FakeManager(wizard_script=[WizardState.REQUESTING_QR, WizardState.ERROR])
    install(monkeypatch, manager)
    flow = await _wizard_flow(hass, manager, str(tmp_path / "tuyadevices.json"))
    r = await flow.async_step_cloud_wizard_start({"user_code": ""})
    r["progress_task"].cancel()
    r = await _drain_progress(flow, manager)
    r = await flow.async_step_cloud_wizard_error({"retry": False})                  # skip it after a failure
    assert r["type"] == "create_entry"
    flow.async_remove()
    await hass.async_block_till_done()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_closing_before_the_login_adds_nothing(hass):
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "external"})
    hass.config_entries.flow.async_abort(r["flow_id"])
    await hass.async_block_till_done()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_embedded_mode_without_pyrustuyabridge_shows_an_error(hass, monkeypatch):
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: None if name == "pyrustuyabridge" else real_find_spec(name))
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "embedded"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], BROKER)
    assert r["type"] == "form" and r["errors"] == {"base": "pyrustuyabridge_missing"}


async def test_a_successful_login_registers_only_the_selected_devices_then_finishes(hass, tmp_path, monkeypatch):
    manager = FakeManager(sync_result=FakeDiff(missing=[FakeDevice("a", "Plug"), FakeDevice("b", "Lamp")]))
    install(monkeypatch, manager)
    flow = await _wizard_flow(hass, manager, str(tmp_path / "tuyadevices.json"))
    r = await flow.async_step_cloud_wizard_start({"user_code": ""})
    assert r["type"] == "progress"
    r["progress_task"].cancel()
    r = await _drain_progress(flow, manager)
    assert r["step_id"] == "sync_devices"
    assert all(k.default() == [] for k in r["data_schema"].schema)     # nothing pre-selected
    r = await flow.async_step_sync_devices({"add": ["a"]})
    assert r["type"] == "create_entry" and manager.added == ["a"] and manager.closed


async def test_no_missing_devices_finishes_straight_away(hass, tmp_path, monkeypatch):
    manager = FakeManager(sync_result=FakeDiff())
    install(monkeypatch, manager)
    flow = await _wizard_flow(hass, manager, str(tmp_path / "tuyadevices.json"))
    r = await flow.async_step_cloud_wizard_start({"user_code": ""})
    r["progress_task"].cancel()
    r = await _drain_progress(flow, manager)
    assert r["type"] == "create_entry" and manager.closed


async def test_a_failed_login_can_be_retried_or_skipped(hass, tmp_path, monkeypatch):
    manager = FakeManager(wizard_script=[WizardState.REQUESTING_QR, WizardState.ERROR])
    install(monkeypatch, manager)
    flow = await _wizard_flow(hass, manager, str(tmp_path / "tuyadevices.json"))
    r = await flow.async_step_cloud_wizard_start({"user_code": ""})
    r["progress_task"].cancel()
    r = await _drain_progress(flow, manager)
    assert r["step_id"] == "cloud_wizard_error"
    r = await flow.async_step_cloud_wizard_error({"retry": False})
    assert r["type"] == "create_entry" and manager.closed


async def test_the_qr_step_shows_a_real_qr_selector(hass):
    """A direct call of the steps (not through `hass.config_entries.flow`, whose `show_progress` auto-continuation
    would just race a fake wizard that stays at `AWAITING_SCAN` forever): once the session has a QR, does
    `cloud_wizard_progress` hand off to a `cloud_wizard_scan` form carrying a real `QrCodeSelector` (the same
    mechanism HA core's own Tuya integration uses) rather than a markdown image data URL, which HA's frontend does
    not reliably render inside a progress step's description."""
    from custom_components.rustuya.config_flow import RustuyaConfigFlow

    manager = FakeManager()
    manager.wizard.session.state = WizardState.AWAITING_SCAN
    manager.wizard.session.qr_url = "tuyaSmart--qrLogin?token=fake"
    manager.wizard.session.message = "Scan me"

    flow = RustuyaConfigFlow()
    flow.hass = hass
    flow._manager = manager
    import sys
    import types

    fake_module = types.ModuleType("rustuya_manager.wizard")
    fake_module.WizardState = WizardState
    sys.modules["rustuya_manager.wizard"] = fake_module
    try:
        result = await flow.async_step_cloud_wizard_progress()
        assert result["type"] == "progress_done" and result["step_id"] == "cloud_wizard_scan"
        form = await flow.async_step_cloud_wizard_scan()
    finally:
        del sys.modules["rustuya_manager.wizard"]

    assert form["type"] == "form" and form["step_id"] == "cloud_wizard_scan"
    (qr_selector,) = form["data_schema"].schema.values()
    assert qr_selector.config["data"] == "tuyaSmart--qrLogin?token=fake"


# ---- options flow ---------------------------------------------------------------------


def _entry(hass, tmp_path):
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_BRIDGE_MODE: "external", "broker_host": "h", "broker_port": 1883,
        "broker_username": "", "broker_password": "", CONF_BRIDGE_ROOT: "rustuya",
        CONF_DEVICES_PATH: str(tmp_path / "tuyadevices.json"), CONF_IL_PREFIX: "il", "il_source": "tuya",
    }, options={CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False})
    entry.add_to_hass(hass)
    return entry


async def test_the_panel_is_one_click_from_configure(hass, tmp_path):
    """Add turns it on (keeping the other options) and links to it; then Open links (Hide is in the panel itself)."""
    entry = _entry(hass, tmp_path)
    hass.config_entries.async_update_entry(entry, options={CONF_ALLOW_HAZARDOUS: True, CONF_EXPOSE_UNUSED: True,
                                                           CONF_PACK: False})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"][0] == "panel_show"
    r = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "panel_show"})
    assert r["type"] == "abort" and r["reason"] == "panel_shown" and r["description_placeholders"] == {"url": "/rustuya"}
    assert entry.options == {CONF_ALLOW_HAZARDOUS: True, CONF_EXPOSE_UNUSED: True, CONF_PACK: False, CONF_PANEL: True}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"][0] == "panel_open" and "panel_hide" not in result["menu_options"]
    r = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "panel_open"})
    assert r["reason"] == "panel_open" and entry.options[CONF_PANEL] is True


async def test_a_session_still_open_elsewhere_aborts_with_a_hint(hass, tmp_path, monkeypatch):
    from custom_components.rustuya import manager_session

    async def busy(**kw):
        raise manager_session.Busy

    monkeypatch.setattr(manager_session, "available", lambda: True)
    monkeypatch.setattr(manager_session, "open_manager", busy)
    entry = _entry(hass, tmp_path)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "bridge_sync"})
    assert r["type"] == "abort" and r["reason"] == "manager_busy"


async def test_options_menu_hides_manager_steps_when_manager_is_unavailable(hass, tmp_path, monkeypatch):
    from custom_components.rustuya import manager_session

    monkeypatch.setattr(manager_session, "available", lambda: False)
    entry = _entry(hass, tmp_path)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["panel_show"]


async def _open_sync(hass, tmp_path, monkeypatch, diff):
    manager = FakeManager(sync_result=diff)
    install(monkeypatch, manager)
    entry = _entry(hass, tmp_path)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "bridge_sync"})
    return manager, r


async def test_removing_an_orphaned_device_from_the_bridge(hass, tmp_path, monkeypatch):
    manager, r = await _open_sync(hass, tmp_path, monkeypatch,
                                  FakeDiff(orphaned=[FakeDevice("gone", "Old plug"), FakeDevice("keep", "Kept")]))
    assert r["step_id"] == "bridge_sync"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"remove": ["gone"]})
    assert r["type"] == "create_entry" and manager.removed == ["gone"] and manager.closed


async def test_finishing_the_sync_hands_the_new_device_file_to_the_service(hass, tmp_path, monkeypatch):
    import json

    from custom_components.rustuya import RuntimeData
    from custom_components.rustuya.const import DOMAIN

    _manager, r = await _open_sync(hass, tmp_path, monkeypatch, FakeDiff(synced=[FakeDevice("ok")]))
    from devices import lamp

    record = lamp("new1")
    (tmp_path / "tuyadevices.json").write_text(json.dumps([record]))
    seen = []

    class _Service:
        def refresh_devices(self, records):
            seen.append(records)

    entry_id = next(iter(hass.config_entries.async_entries(DOMAIN))).entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = RuntimeData(service=_Service(), embedded_bridge=None)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    assert r["type"] == "create_entry"
    assert [[d["id"] for d in records] for records in seen] == [["new1"]]   # the file as it is now


async def test_nothing_selected_changes_nothing(hass, tmp_path, monkeypatch):
    diff = FakeDiff(missing=[FakeDevice("new")], orphaned=[FakeDevice("gone")],
                    mismatched=[(FakeDevice("moved"), ["IP: 1.1.1.1 -> 2.2.2.2"])])
    manager, r = await _open_sync(hass, tmp_path, monkeypatch, diff)
    assert r["description_placeholders"]["missing"] == "1" and r["description_placeholders"]["orphaned"] == "1"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    assert r["type"] == "create_entry" and manager.commands == [] and manager.removed == []


async def test_add_sends_the_clouds_credentials_and_update_pushes_them_again(hass, tmp_path, monkeypatch):
    new = FakeDevice("new", "Lamp", key="k" * 16, ip="192.168.1.9", version="3.4")
    moved = FakeDevice("moved", "Plug", key="j" * 16, ip="192.168.1.7", version="Auto")
    manager, r = await _open_sync(
        hass, tmp_path, monkeypatch,
        FakeDiff(missing=[new, FakeDevice("skipped")], mismatched=[(moved, ["IP: 1.1.1.1 -> 192.168.1.7"])]))
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"add": ["new"], "update": ["moved"]})
    assert r["type"] == "create_entry"
    assert manager.commands == [
        ("add", "new", "Lamp", {"key": "k" * 16, "ip": "192.168.1.9", "version": "3.4"}),
        ("add", "moved", "Plug", {"key": "j" * 16, "ip": "192.168.1.7"}),      # "Auto" version is not pushed
    ]


async def test_an_id_that_is_not_in_the_diff_is_ignored(hass, tmp_path, monkeypatch):
    manager, _r = await _open_sync(hass, tmp_path, monkeypatch, FakeDiff(orphaned=[FakeDevice("gone")]))
    from custom_components.rustuya import bridge_sync

    sent = await bridge_sync.apply(manager, manager._sync_result, {"remove": ["not-listed"], "add": ["gone"]})
    assert sent == 0 and manager.removed == [] and manager.commands == []


async def test_a_failed_publish_keeps_the_form_open_with_the_error(hass, tmp_path, monkeypatch):
    manager, r = await _open_sync(hass, tmp_path, monkeypatch, FakeDiff(missing=[FakeDevice("new")]))
    manager.publish_error = RuntimeError("MQTT broker not connected")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"add": ["new"]})
    assert r["type"] == "form" and r["errors"] == {"base": "publish_failed"}
    assert "MQTT broker not connected" in r["description_placeholders"]["error"] and not manager.closed


async def test_a_fully_synced_setup_is_still_shown_and_a_synced_device_can_be_removed(hass, tmp_path, monkeypatch):
    manager, r = await _open_sync(hass, tmp_path, monkeypatch,
                                  FakeDiff(synced=[FakeDevice("ok1", "Plug"), FakeDevice("ok2", "Lamp")]))
    assert r["type"] == "form" and r["step_id"] == "bridge_sync"
    assert r["description_placeholders"]["synced"] == "2"
    assert [str(k) for k in r["data_schema"].schema] == ["remove"]          # nothing to add or update
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"remove": ["ok1"]})
    assert r["type"] == "create_entry" and manager.removed == ["ok1"] and manager.closed


async def test_every_bridge_device_is_removable_and_each_is_tagged_with_its_state(hass, tmp_path, monkeypatch):
    diff = FakeDiff(synced=[FakeDevice("s")], orphaned=[FakeDevice("o")],
                    mismatched=[(FakeDevice("m"), ["IP: 1.1.1.1 -> 2.2.2.2"])], missing=[FakeDevice("n")])
    _manager, r = await _open_sync(hass, tmp_path, monkeypatch, diff)
    fields = {str(k): v for k, v in r["data_schema"].schema.items()}
    assert set(fields) == {"add", "update", "remove"}
    assert set(fields["add"].options) == {"n"} and set(fields["update"].options) == {"m"}
    assert set(fields["remove"].options) == {"s", "o", "m"}                  # not "n": the bridge does not hold it
    assert fields["remove"].options["o"].startswith("[orphan]") and fields["remove"].options["s"].startswith("[synced]")
    assert "IP: 1.1.1.1 -> 2.2.2.2" in fields["remove"].options["m"]


async def test_no_devices_at_all_aborts(hass, tmp_path, monkeypatch):
    manager, r = await _open_sync(hass, tmp_path, monkeypatch, FakeDiff())
    assert r["type"] == "abort" and r["reason"] == "no_devices" and manager.closed


async def test_add_extra_for_a_sub_device_carries_cid_and_parent(hass):
    from custom_components.rustuya.bridge_sync import add_extra

    sub = FakeDevice("s", type="SubDevice", cid="c1", parent_id="p1")
    assert add_extra(sub) == {"cid": "c1", "parent_id": "p1"}
    assert add_extra(FakeDevice("w")) == {}


async def test_a_second_instance_is_refused(hass, tmp_path):
    _entry(hass, tmp_path)
    r = await _start(hass)
    assert r["type"] == "abort" and r["reason"] == "single_instance_allowed"


async def test_a_gateway_is_added_before_its_sub_devices(hass):
    from custom_components.rustuya import bridge_sync

    manager = FakeManager()
    diff = FakeDiff(missing=[FakeDevice("sub1", "Button", type="SubDevice", cid="c1", parent_id="gw"),
                             FakeDevice("gw", "Gateway"), FakeDevice("lamp", "Lamp")])
    await bridge_sync.apply(manager, diff, {"add": ["sub1", "gw", "lamp"]})
    assert manager.added == ["gw", "lamp", "sub1"]


@pytest.mark.parametrize(("mode", "loaded", "embeds"), [
    ("embedded", False, True),              # setup, or an entry that failed to start: no bridge runs yet
    ("embedded", True, False),              # the entry's own bridge runs
    ("external", False, False),
])
async def test_a_session_runs_the_embedded_bridge_only_while_the_entry_does_not(hass, tmp_path, monkeypatch, mode,
                                                                                 loaded, embeds):
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.rustuya import config_flow
    from custom_components.rustuya.const import CONF_BRIDGE_STATE_FILE

    manager = FakeManager()
    install(monkeypatch, manager)
    data = {CONF_BRIDGE_MODE: mode, "broker_host": "h", "broker_port": 1883, CONF_BRIDGE_ROOT: "rustuya",
            CONF_DEVICES_PATH: str(tmp_path / "tuyadevices.json"),
            CONF_BRIDGE_STATE_FILE: str(tmp_path / "state" / "bridge_state.json")}
    await config_flow._open(hass, data, bridge_running=loaded)
    assert ("bridge_state" in manager.kwargs) is embeds
    if embeds:
        assert manager.kwargs["bridge_state"] == str(tmp_path / "state" / "bridge_state.json")
        assert (tmp_path / "state").is_dir()                # the bridge can write its state file there

    entry = _entry(hass, tmp_path)
    hass.config_entries.async_update_entry(entry, data={**entry.data, **data})
    entry.mock_state(hass, ConfigEntryState.LOADED if loaded else ConfigEntryState.SETUP_ERROR)
    manager.kwargs = {}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "cloud_wizard"})
    assert ("bridge_state" in manager.kwargs) is embeds


async def test_closing_the_login_closes_the_session_before_the_entry_is_added(hass, monkeypatch):
    order = []
    manager = FakeManager()

    async def closing(*exc):
        order.append("closed")
        manager.closed = True

    manager.__aexit__ = closing
    r = await _to_login(hass, monkeypatch, manager)
    real_init = hass.config_entries.flow.async_init

    async def init(*a, **kw):
        order.append("import")
        return await real_init(*a, **kw)

    monkeypatch.setattr(hass.config_entries.flow, "async_init", init)
    hass.config_entries.flow.async_abort(r["flow_id"])
    await hass.async_block_till_done()
    assert order == ["closed", "import"]
    assert hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize("ending", ["close", "no_devices"])
async def test_a_login_in_configure_is_handed_to_the_entry_however_the_flow_ends(hass, tmp_path, monkeypatch, ending):
    """After a login the device step may be closed, or have nothing to show: the new device file still reaches the
    entry (`async_refresh_devices`), after the session closed."""
    import custom_components.rustuya as integration

    handed = []

    async def refresh(_hass, entry):
        handed.append(manager.closed)

    monkeypatch.setattr(integration, "async_refresh_devices", refresh)
    manager = FakeManager(sync_result=FakeDiff() if ending == "no_devices" else FakeDiff(missing=[FakeDevice("a")]),
                          wizard_script=[WizardState.DONE])
    install(monkeypatch, manager)
    entry = _entry(hass, tmp_path)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "cloud_wizard"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"user_code": ""})
    if r["type"] == "progress_done":                                          # on to the devices
        r = await hass.config_entries.options.async_configure(r["flow_id"])
    if ending == "no_devices":
        assert r["type"] == "abort" and r["reason"] == "no_devices"
    else:
        assert r["type"] == "form" and r["step_id"] == "bridge_sync"
        hass.config_entries.options.async_abort(r["flow_id"])                # closing the window
    await hass.async_block_till_done()
    assert handed == [True]


@pytest.mark.parametrize("flow_kind", ["config", "options"])
async def test_submitting_the_qr_after_the_login_finished_shows_the_devices(hass, tmp_path, monkeypatch, flow_kind):
    """The QR form's (empty) submit when the login has already finished: Home Assistant hands that input on through
    `progress_done` to the device step, which must show its list, not take it as "nothing selected" and finish."""
    manager = FakeManager(sync_result=FakeDiff(missing=[FakeDevice("a", "Plug")]),
                          wizard_script=[WizardState.AWAITING_SCAN, WizardState.DONE])
    install(monkeypatch, manager)
    if flow_kind == "config":
        r = await _to_login(hass, monkeypatch, manager)
        flows, device_step = hass.config_entries.flow, "sync_devices"
    else:
        entry = _entry(hass, tmp_path)
        flows, device_step = hass.config_entries.options, "bridge_sync"
        r = await flows.async_init(entry.entry_id)
        r = await flows.async_configure(r["flow_id"], {"next_step_id": "cloud_wizard"})
    r = await flows.async_configure(r["flow_id"], {"user_code": ""})
    while r["type"] in ("progress", "progress_done"):
        r = await flows.async_configure(r["flow_id"])
    assert r["type"] == "form" and r["step_id"] == "cloud_wizard_scan"
    manager.wizard.advance()                                        # scanned: the login finishes
    r = await flows.async_configure(r["flow_id"], {})
    assert r["type"] == "form" and r["step_id"] == device_step
    assert manager.added == []


async def test_a_login_that_fails_at_once_shows_the_retry_form(hass, monkeypatch):
    """The wizard errs straight away (a bad user code, no network): the user-code submit is handed on to the error
    step, which must show its retry form rather than read a `retry` that is not there."""
    manager = FakeManager(wizard_script=[WizardState.ERROR])
    r = await _to_login(hass, monkeypatch, manager)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"user_code": "bad"})
    assert r["type"] == "form" and r["step_id"] == "cloud_wizard_error"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"retry": True})    # and again
    assert r["type"] == "form" and r["step_id"] == "cloud_wizard_start"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"user_code": "bad"})
    assert r["type"] == "form" and r["step_id"] == "cloud_wizard_error"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"retry": False})
    assert r["type"] == "create_entry"


async def test_the_login_directory_exists_before_the_session(hass, tmp_path, monkeypatch):
    """The Tuya login is saved beside the device file by a library that does not create the directory: without it
    the save fails and every login asks again."""
    from custom_components.rustuya import config_flow

    manager = FakeManager()
    install(monkeypatch, manager)
    devices = tmp_path / "not" / "yet" / "tuyadevices.json"
    await config_flow._open(hass, {CONF_BRIDGE_MODE: "external", "broker_host": "h", "broker_port": 1883,
                                   CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: str(devices)}, bridge_running=True)
    assert devices.parent.is_dir()


def test_every_translation_has_the_strings_keys_and_placeholders():
    """A translation missing a key shows it blank (or as the raw key); one missing a placeholder the code passes, or
    naming one it does not, breaks the message."""
    import json
    import pathlib
    import re

    base = pathlib.Path(__file__).resolve().parents[2] / "custom_components/rustuya"

    def leaves(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from leaves(v, (*path, k))
        else:
            yield path, set(re.findall(r"\{(\w+)\}", node))

    strings = dict(leaves(json.loads((base / "strings.json").read_text())))
    for file in sorted((base / "translations").glob("*.json")):
        translated = dict(leaves(json.loads(file.read_text())))
        assert translated.keys() == strings.keys(), file.name
        assert {p: v for p, v in translated.items() if v != strings[p]} == {}, file.name
