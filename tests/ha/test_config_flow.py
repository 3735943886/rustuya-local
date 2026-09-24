"""The config and options flow's own state machine, against a `FakeManager` (no real Tuya Cloud, broker or bridge —
those are exercised in test_init.py against real infrastructure instead)."""

from __future__ import annotations

import asyncio
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
    DOMAIN,
)


@pytest.fixture(autouse=True)
def _no_real_setup():
    """A completed flow's `async_create_entry` makes Home Assistant set the entry up for real — this file is about
    the flow's own state machine, not a live MQTT connection (that belongs to test_init.py)."""
    with patch("custom_components.rustuya.async_setup_entry", return_value=True):
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
    from custom_components.rustuya.const import CONF_BRIDGE_MODE, CONF_BRIDGE_ROOT, CONF_DEVICES_PATH

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


async def test_skipping_onboarding_goes_straight_to_il_and_creates_the_entry(hass, tmp_path):
    devices_path = str(tmp_path / "tuyadevices.json")
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "external"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "broker_host": "h", "broker_port": 1883, "broker_username": "", "broker_password": "", "bridge_root": "rustuya",
    })
    assert r["step_id"] == "devices"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"devices_path": devices_path, "onboard": False})
    assert r["step_id"] == "il"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"il_prefix": "il", "il_source": "tuya"})
    assert r["type"] == "create_entry"
    assert r["data"][CONF_DEVICES_PATH] == devices_path and r["data"][CONF_BRIDGE_ROOT] == "rustuya"
    assert r["options"] == {CONF_ALLOW_HAZARDOUS: False, CONF_EXPOSE_UNUSED: False}


async def test_embedded_mode_without_pyrustuyabridge_shows_an_error(hass, monkeypatch):
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: None if name == "pyrustuyabridge" else real_find_spec(name))
    result = await _start(hass)
    r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "embedded"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "broker_host": "h", "broker_port": 1883, "broker_username": "", "broker_password": "", "bridge_root": "rustuya",
        "bridge_state_file": "/tmp/x.json", "bridge_log_level": "warn",
    })
    assert r["type"] == "form" and r["errors"] == {"base": "pyrustuyabridge_missing"}


async def test_a_successful_login_registers_only_the_selected_devices_then_reaches_il(hass, tmp_path, monkeypatch):
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
    assert r["step_id"] == "il" and manager.added == ["a"] and manager.closed
    r = await flow.async_step_il({"il_prefix": "il", "il_source": "tuya"})
    assert r["type"] == "create_entry"


async def test_no_missing_devices_skips_straight_to_il(hass, tmp_path, monkeypatch):
    manager = FakeManager(sync_result=FakeDiff())
    install(monkeypatch, manager)
    flow = await _wizard_flow(hass, manager, str(tmp_path / "tuyadevices.json"))
    r = await flow.async_step_cloud_wizard_start({"user_code": ""})
    r["progress_task"].cancel()
    r = await _drain_progress(flow, manager)
    assert r["step_id"] == "il" and manager.closed


async def test_a_failed_login_can_be_retried_or_skipped(hass, tmp_path, monkeypatch):
    manager = FakeManager(wizard_script=[WizardState.REQUESTING_QR, WizardState.ERROR])
    install(monkeypatch, manager)
    flow = await _wizard_flow(hass, manager, str(tmp_path / "tuyadevices.json"))
    r = await flow.async_step_cloud_wizard_start({"user_code": ""})
    r["progress_task"].cancel()
    r = await _drain_progress(flow, manager)
    assert r["step_id"] == "cloud_wizard_error"
    r = await flow.async_step_cloud_wizard_error({"retry": False})
    assert r["step_id"] == "il" and manager.closed


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


async def test_tuning_updates_options(hass, tmp_path):
    entry = _entry(hass, tmp_path)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "tuning"})
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {CONF_ALLOW_HAZARDOUS: True, CONF_EXPOSE_UNUSED: True})
    assert r["type"] == "create_entry"
    assert r["data"] == {CONF_ALLOW_HAZARDOUS: True, CONF_EXPOSE_UNUSED: True}


async def test_options_menu_hides_manager_steps_when_manager_is_unavailable(hass, tmp_path, monkeypatch):
    from custom_components.rustuya import manager_session

    monkeypatch.setattr(manager_session, "available", lambda: False)
    entry = _entry(hass, tmp_path)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["tuning"]


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

    manager, r = await _open_sync(hass, tmp_path, monkeypatch, FakeDiff(synced=[FakeDevice("ok")]))
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
    manager, r = await _open_sync(hass, tmp_path, monkeypatch, FakeDiff(orphaned=[FakeDevice("gone")]))
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
    manager, r = await _open_sync(hass, tmp_path, monkeypatch, diff)
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
