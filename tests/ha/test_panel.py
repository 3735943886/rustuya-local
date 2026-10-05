"""The advanced panel (panel.py): registered with the `panel` option, and its file API on <config>/rustuya_converters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rustuya import panel
from custom_components.rustuya.const import CONF_PACK, CONF_PANEL, DOMAIN


@dataclass
class FakeService:
    pack_status: dict[str, Any] | None = None
    syncs: list[int] = field(default_factory=list)
    pack_on: bool = True
    hub: Any = None

    async def stop(self, offline: bool = True) -> None:
        pass

    def sync_pack_now(self) -> bool:
        self.syncs.append(1)
        return self.pack_on


@dataclass
class FakeDriver:
    linked: bool


@dataclass
class FakeHub:
    drivers: dict[str, FakeDriver]


@dataclass
class FakeRuntime:
    service: FakeService
    embedded_bridge: Any = None


@pytest.fixture
async def loaded(hass, tmp_path, monkeypatch):
    """A loaded entry with a fake runtime (no MQTT), config dir in tmp_path, and http up (the panel registry needs no
    running frontend, which would need the hass_frontend package). Removing the entry skips the broker cleanup
    (`async_remove_entry` connects to MQTT; test_init.py covers it against a real broker)."""
    import custom_components.rustuya as integration

    async def no_cleanup(_hass, _entry) -> None:
        return None

    monkeypatch.setattr(integration, "async_remove_entry", no_cleanup)
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "http", {})

    async def load(options: dict[str, Any]) -> tuple[MockConfigEntry, FakeService]:
        entry = MockConfigEntry(domain=DOMAIN, options=options, data={
            "broker_host": "h", "broker_port": 1883, "broker_username": "", "broker_password": "",
            "bridge_root": "rustuya", "devices_path": str(tmp_path / "tuyadevices.json")})
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.LOADED)
        service = FakeService(pack_status={"at": 1.0, "added": [], "updated": [], "removed": [], "kept": [],
                                           "failed": []})
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = FakeRuntime(service)
        await panel.async_setup(hass, entry)
        return entry, service

    return load


async def test_the_panel_follows_the_option(hass, loaded):
    await loaded({CONF_PANEL: False})
    assert panel.PANEL_URL not in hass.data.get(frontend.DATA_PANELS, {})
    hass.data[DOMAIN].clear()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)

    await loaded({CONF_PANEL: True})
    p = hass.data[frontend.DATA_PANELS][panel.PANEL_URL]
    assert p.require_admin and p.sidebar_title == "Rustuya"
    assert p.config_panel_domain is None          # Configure stays the options flow, where the panel is turned off
    # the file's hash in the URL: a changed file is never taken from a browser's cache (same release or not)
    js = (Path(panel.__file__).parent / "www" / "rustuya-panel.js").read_bytes()
    assert p.config["_panel_custom"]["module_url"].endswith("-" + hashlib.sha256(js).hexdigest()[:10])
    panel.async_remove(hass)
    assert panel.PANEL_URL not in hass.data[frontend.DATA_PANELS]
    panel.async_remove(hass)                                          # twice: no error


async def test_the_file_api(hass, loaded, hass_client, tmp_path):
    _, service = await loaded({CONF_PANEL: True, CONF_PACK: True})
    c = await hass_client()

    r = await c.get("/api/rustuya/converters")
    assert r.status == 200
    body = await r.json()
    assert body["files"] == [] and body["pack"] == {"enabled": True, "status": service.pack_status}

    r = await c.put("/api/rustuya/converters/10_mine.json", json={"content": '{"pid": {}}'})
    assert r.status == 200
    assert (tmp_path / "rustuya_converters" / "10_mine.json").read_text() == '{"pid": {}}'
    r = await c.get("/api/rustuya/converters")
    assert [(f["name"], f["origin"]) for f in (await r.json())["files"]] == [("10_mine.json", "user")]
    r = await c.get("/api/rustuya/converters/10_mine.json")
    assert (await r.json())["content"] == '{"pid": {}}'

    assert (await c.put("/api/rustuya/converters/bad.json", json={"content": "{nope"})).status == 400
    assert (await c.put("/api/rustuya/converters/a.txt", json={"content": ""})).status == 400
    assert (await c.put("/api/rustuya/converters/a.json", data="not json")).status == 400
    assert (await c.get("/api/rustuya/converters/zz.json")).status == 404

    assert (await c.delete("/api/rustuya/converters/10_mine.json")).status == 200
    assert not (tmp_path / "rustuya_converters" / "10_mine.json").exists()

    r = await c.post("/api/rustuya/pack")
    assert (await r.json()) == {"started": True} and service.syncs == [1]


async def test_the_api_is_gone_with_the_panel_off(hass, loaded, hass_client):
    await loaded({CONF_PANEL: False})
    c = await hass_client()
    assert (await c.get("/api/rustuya/converters")).status == 404
    assert (await c.post("/api/rustuya/pack")).status == 404


async def test_the_panel_turns_itself_off(hass, loaded, hass_client):
    entry, _ = await loaded({CONF_PANEL: True, CONF_PACK: True})
    c = await hass_client()
    r = await c.delete("/api/rustuya/panel")
    assert r.status == 200 and await r.json() == {"panel": False}
    assert entry.options == {CONF_PANEL: False, CONF_PACK: True}
    assert (await c.delete("/api/rustuya/panel")).status == 404        # off now: the API is gone with it


async def test_the_api_is_for_administrators(hass, loaded, hass_client, hass_read_only_access_token):
    await loaded({CONF_PANEL: True})
    c = await hass_client(hass_read_only_access_token)
    assert (await c.get("/api/rustuya/converters")).status == 403
    assert (await c.put("/api/rustuya/converters/a.py", json={"content": "X = 1"})).status == 403
    assert (await c.delete("/api/rustuya/panel")).status == 403


def _diff():
    from fake_manager import FakeDevice, FakeDiff

    gw = FakeDevice("gw", "Gateway")
    return FakeDiff(
        missing=[FakeDevice("sub1", "Button", type="SubDevice", cid="c1", parent_id="gw"), FakeDevice("lamp", "Lamp")],
        orphaned=[FakeDevice("old", "Old plug")],
        mismatched=[(FakeDevice("fan", "Fan", ip="10.0.0.2"), ["IP: 10.0.0.1 -> 10.0.0.2"])],
        synced=[gw])


async def test_the_bridge_list(hass, loaded, hass_client, monkeypatch):
    from fake_manager import FakeManager, install

    manager = FakeManager(sync_result=_diff())
    install(monkeypatch, manager)
    _, service = await loaded({CONF_PANEL: True})
    c = await hass_client()
    assert (await (await c.get("/api/rustuya/bridge")).json())["online"] == {}      # the service not started yet
    service.hub = FakeHub({"gw": FakeDriver(True), "fan": FakeDriver(False)})

    r = await c.get("/api/rustuya/bridge")
    assert r.status == 200
    body = await r.json()
    assert [(d["id"], d["category"]) for d in body["devices"]] == [
        ("sub1", "missing"), ("lamp", "missing"), ("old", "orphan"), ("fan", "mismatch"), ("gw", "synced")]
    by_id = {d["id"]: d for d in body["devices"]}
    assert by_id["sub1"]["cloud"]["parent_id"] == "gw" and by_id["sub1"]["bridge"] is None
    assert by_id["old"]["cloud"] is None and by_id["old"]["bridge"]["name"] == "Old plug"
    assert by_id["fan"]["reasons"] == ["IP: 10.0.0.1 -> 10.0.0.2"]
    assert body["online"] == {"gw": True, "fan": False}
    assert manager.kwargs["broker"] == "mqtt://h:1883" and manager.closed and manager.commands == []


async def test_bridge_commands(hass, loaded, hass_client, monkeypatch):
    from fake_manager import FakeManager, install

    from custom_components.rustuya.panel import BridgeView

    monkeypatch.setattr(BridgeView, "SETTLE", 0)
    manager = FakeManager(sync_result=_diff())
    install(monkeypatch, manager)
    await loaded({CONF_PANEL: True})
    c = await hass_client()

    r = await c.post("/api/rustuya/bridge", json={"add": ["sub1", "lamp", "old"], "update": ["fan"],
                                                  "remove": ["old", "lamp"]})
    assert r.status == 200 and (await r.json())["sent"] == 4           # "old" is not missing, "lamp" not on the bridge
    assert [(a, i) for a, i, _, _ in manager.commands] == [
        ("add", "lamp"), ("add", "sub1"), ("add", "fan")]             # the gateway-level device ahead of the sub
    assert manager.removed == ["old"]
    assert (await c.post("/api/rustuya/bridge", json={"add": "lamp"})).status == 400
    assert (await c.post("/api/rustuya/bridge", data="nope")).status == 400


async def test_the_bridge_list_when_it_cannot_be_had(hass, loaded, hass_client, monkeypatch):
    from custom_components.rustuya import manager_session

    await loaded({CONF_PANEL: True})
    c = await hass_client()
    monkeypatch.setattr(manager_session, "available", lambda: False)
    assert (await c.get("/api/rustuya/bridge")).status == 501

    async def busy(**kw):
        raise manager_session.Busy

    async def down(**kw):
        raise TimeoutError("no bridge status")

    monkeypatch.setattr(manager_session, "available", lambda: True)
    monkeypatch.setattr(manager_session, "open_manager", busy)
    assert (await c.get("/api/rustuya/bridge")).status == 409
    monkeypatch.setattr(manager_session, "open_manager", down)
    r = await c.get("/api/rustuya/bridge")
    assert r.status == 502 and "no bridge status" in (await r.json())["message"]


async def test_live_links_while_subscribed(hass, loaded, hass_ws_client, monkeypatch):
    import json

    import tuya2ildevice.host
    from tuya2ildevice.host.memory import InProcessTransport

    broker = InProcessTransport()
    made = []

    class FakeMqtt:
        def __init__(self, host, port, **kw):
            self.kw, self.closed = kw, False
            made.append(self)

        async def connect(self):
            pass

        async def close(self):
            self.closed = True

        def __getattr__(self, name):                   # subscribe / publish on the shared in-process broker
            return getattr(broker, name)

    monkeypatch.setattr(tuya2ildevice.host, "MqttTransport", FakeMqtt)
    await broker.publish("rustuya/error/lamp", json.dumps({"errorCode": 0}), retain=True)
    await loaded({CONF_PANEL: True})
    ws = await hass_ws_client(hass)

    await ws.send_json({"id": 1, "type": "rustuya/subscribe_links"})
    assert (await ws.receive_json())["success"]
    first = await ws.receive_json()
    assert first["event"] == {"links": {"lamp": {"online": True, "code": 0, "message": ""}}}
    assert made[0].kw["client_id"].startswith("rustuya-panel-")

    await broker.publish("rustuya/error/lamp", json.dumps({"errorCode": 905, "errorMsg": "Timeout"}))
    await broker.settle()
    assert (await ws.receive_json())["event"] == {"links": {"lamp": {"online": False, "code": 905,
                                                                      "message": "Timeout"}}}

    await ws.send_json({"id": 2, "type": "unsubscribe_events", "subscription": 1})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()
    assert made[0].closed


async def test_live_links_refuse_when_off_or_unreachable(hass, loaded, hass_ws_client, monkeypatch):
    import tuya2ildevice.host

    class Down:
        def __init__(self, *a, **kw):
            pass

        async def connect(self):
            raise OSError("connection refused")

        async def close(self):
            pass

    monkeypatch.setattr(tuya2ildevice.host, "MqttTransport", Down)
    await loaded({CONF_PANEL: True})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "rustuya/subscribe_links"})
    r = await ws.receive_json()
    assert not r["success"] and r["error"]["code"] == "cannot_connect"


async def test_settings_show_what_setup_left_at_its_defaults(hass, loaded, hass_client):
    entry, _ = await loaded({CONF_PANEL: True})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "bridge_mode": "external", "il_prefix": "il",
                                                        "il_source": "tuya"})
    c = await hass_client()
    r = await c.get("/api/rustuya/settings")
    assert r.status == 200
    assert await r.json() == {"mode": "external", "settings": {
        "bridge_root": "rustuya", "il_prefix": "il", "il_source": "tuya", "devices_path": entry.data["devices_path"]},
        "log_levels": ["error", "warn", "info", "debug"]}
    hass.config_entries.async_update_entry(entry, data={**entry.data, "bridge_mode": "embedded",
                                                        "bridge_state_file": "/s.json", "bridge_log_level": "warn"})
    settings = (await (await c.get("/api/rustuya/settings")).json())["settings"]
    assert settings["bridge_state_file"] == "/s.json" and settings["bridge_log_level"] == "warn"


@pytest.mark.parametrize("body", [
    {"il_prefix": "il/#"}, {"il_prefix": ""}, {"il_prefix": "a//b"}, {"il_source": "a/b"}, {"il_source": "_x"},
    {"bridge_root": " rustuya"}, {"bridge_log_level": "warn"}, {"broker_host": "x"}, ["il"],
])
async def test_settings_refuse_what_cannot_work(hass, loaded, hass_client, monkeypatch, body):
    entry, _ = await loaded({CONF_PANEL: True})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "bridge_mode": "external", "il_prefix": "il",
                                                        "il_source": "tuya"})
    applied = []
    monkeypatch.setattr(panel, "_apply", lambda *a: applied.append(a))
    c = await hass_client()
    r = await c.put("/api/rustuya/settings", json=body)
    assert r.status == 400 and not applied


async def test_a_settings_change_restarts_with_the_new_ones(hass, loaded, hass_client, monkeypatch):
    entry, _ = await loaded({CONF_PANEL: True})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "bridge_mode": "external", "il_prefix": "il",
                                                        "il_source": "tuya"})
    applied = []

    async def fake_apply(_hass, e, old, data):
        applied.append((e, old, data))

    monkeypatch.setattr(panel, "_apply", fake_apply)
    c = await hass_client()
    r = await c.put("/api/rustuya/settings", json={"il_prefix": "il"})               # the same: nothing to do
    assert await r.json() == {"restarting": False}
    r = await c.put("/api/rustuya/settings", json={"il_prefix": "home/il", "devices_path": "my/tuyadevices.json"})
    assert await r.json() == {"restarting": True}
    await hass.async_block_till_done()
    (e, old, data), = applied
    assert e is entry and old["il_prefix"] == "il"
    assert data["il_prefix"] == "home/il" and data["devices_path"] == hass.config.path("my/tuyadevices.json")
    assert data["broker_host"] == "h"                                                # the rest kept


async def test_a_new_root_for_an_external_bridge_is_checked(hass, loaded, hass_client, monkeypatch):
    entry, _ = await loaded({CONF_PANEL: True})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "bridge_mode": "external", "il_prefix": "il",
                                                        "il_source": "tuya"})
    applied = []
    monkeypatch.setattr(panel, "_apply", lambda *a: applied.append(a))
    from custom_components.rustuya import config_flow

    async def not_found(_hass, data):
        assert data["bridge_root"] == "rb"
        return "bridge_not_found"

    monkeypatch.setattr(config_flow, "_probe_bridge", not_found)
    c = await hass_client()
    r = await c.put("/api/rustuya/settings", json={"bridge_root": "rb"})
    assert r.status == 400 and "no rustuya-bridge answers on rb" in (await r.json())["message"] and not applied


@pytest.mark.parametrize(("mode", "change", "cleared"), [
    ("external", {"il_prefix": "home/il"}, {"il": True, "bridge": False}),
    ("external", {"bridge_root": "rb"}, None),                          # an external bridge's topics are not ours
    ("embedded", {"bridge_root": "rb"}, {"il": False, "bridge": True}),
    ("embedded", {"devices_path": "/d.json"}, None),
])
async def test_apply_clears_the_old_topics_between_stop_and_start(hass, monkeypatch, mode, change, cleared):
    import custom_components.rustuya as integration

    old = {"bridge_mode": mode, "bridge_root": "rustuya", "il_prefix": "il", "il_source": "tuya",
           "devices_path": "/t.json", "broker_host": "h", "broker_port": 1883}
    entry = MockConfigEntry(domain=DOMAIN, data=old)
    entry.add_to_hass(hass)
    calls = []

    async def unload(entry_id):
        calls.append(("unload", dict(entry.data)))
        return True

    async def setup(entry_id):
        calls.append(("setup", dict(entry.data)))
        return True

    async def clear(_hass, data, *, il, bridge):
        calls.append(("clear", dict(data), {"il": il, "bridge": bridge}))

    monkeypatch.setattr(hass.config_entries, "async_unload", unload)
    monkeypatch.setattr(hass.config_entries, "async_setup", setup)
    monkeypatch.setattr(integration, "async_clear_retained", clear)
    await panel._apply(hass, entry, dict(old), {**old, **change})
    expected = [("unload", old)]
    if cleared:
        expected.append(("clear", old, cleared))
    expected.append(("setup", {**old, **change}))
    assert calls == expected


async def test_the_options_other_than_the_panel_are_in_the_panel(hass, loaded, hass_client, monkeypatch):
    entry, _ = await loaded({CONF_PANEL: True, CONF_PACK: False})

    async def restart(_hass, e, *, data=None, options=None, between=None):      # stored, as the restart would
        _hass.config_entries.async_update_entry(e, options=options)

    monkeypatch.setattr(panel, "_restart", restart)
    c = await hass_client()
    r = await c.get("/api/rustuya/options")
    assert await r.json() == {"allow_hazardous": False, "expose_unused": False, "pack": False}
    r = await c.put("/api/rustuya/options", json={"pack": False})                  # the same: nothing to do
    assert await r.json() == {"restarting": False}
    r = await c.put("/api/rustuya/options", json={"allow_hazardous": True, "pack": True})
    assert await r.json() == {"restarting": True}
    assert entry.options == {CONF_PANEL: True, CONF_PACK: True, "allow_hazardous": True}


@pytest.mark.parametrize("body", [{"panel": False}, {"pack": "yes"}, {"other": True}, [True]])
async def test_the_options_api_takes_only_those_three(hass, loaded, hass_client, body):
    entry, _ = await loaded({CONF_PANEL: True})
    c = await hass_client()
    assert (await c.put("/api/rustuya/options", json=body)).status == 400
    assert entry.options == {CONF_PANEL: True}


async def _poll_until(c, states, timeout=5.0):
    import asyncio

    end = asyncio.get_running_loop().time() + timeout
    while True:
        r = await (await c.get("/api/rustuya/cloud")).json()
        if r["state"] in states:
            return r
        assert asyncio.get_running_loop().time() < end, r
        await asyncio.sleep(0.1)


async def test_fetching_from_tuya_cloud_in_the_panel(hass, loaded, hass_client, monkeypatch):
    from fake_manager import FakeManager, WizardState, install

    import custom_components.rustuya as integration

    entry, _ = await loaded({CONF_PANEL: True})
    handed = []

    async def refresh(_hass, e):
        handed.append((e, manager.closed))

    monkeypatch.setattr(integration, "async_refresh_devices", refresh)
    manager = FakeManager(wizard_script=[WizardState.AWAITING_SCAN, WizardState.DONE])
    install(monkeypatch, manager)
    c = await hass_client()
    assert await (await c.get("/api/rustuya/cloud")).json() == {"state": "idle", "user_code": None}

    r = await c.post("/api/rustuya/cloud", json={"user_code": " abc "})
    assert r.status == 200
    r = await r.json()
    assert r["state"] == "awaiting_scan" and r["qr"].startswith("data:image/png") and manager.wizard.started_with == "abc"
    assert (await c.post("/api/rustuya/cloud", json={})).status == 409          # one at a time

    manager.wizard.advance()                                                     # scanned, and fetched
    r = await _poll_until(c, {"done"})
    assert r["qr"] is None and manager.closed
    assert handed == [(entry, True)]                                             # to the entry, after the session


async def test_the_panel_form_starts_from_the_saved_login_code(hass, loaded, hass_client, tmp_path):
    """The code of the saved login (`tuyacreds.json` beside the device file) comes with the status while no fetch runs,
    read without a manager session."""
    await loaded({CONF_PANEL: True})
    c = await hass_client()
    (tmp_path / "tuyacreds.json").write_text('{"user_code": "saved1", "token_info": {}}')
    assert await (await c.get("/api/rustuya/cloud")).json() == {"state": "idle", "user_code": "saved1"}
    (tmp_path / "tuyacreds.json").write_text("not json")
    assert (await (await c.get("/api/rustuya/cloud")).json())["user_code"] is None


async def test_a_panel_fetch_can_be_cancelled_and_ends_when_nothing_polls(hass, loaded, hass_client, monkeypatch):
    from fake_manager import FakeManager, WizardState, install

    await loaded({CONF_PANEL: True})
    c = await hass_client()
    manager = FakeManager(wizard_script=[WizardState.AWAITING_SCAN])
    install(monkeypatch, manager)
    await c.post("/api/rustuya/cloud", json={})
    r = await (await c.delete("/api/rustuya/cloud")).json()
    assert r["state"] == "cancelled" and manager.closed

    monkeypatch.setattr(panel.CloudFetch, "IDLE_TIMEOUT", 0.3)
    manager = FakeManager(wizard_script=[WizardState.AWAITING_SCAN])
    install(monkeypatch, manager)
    assert (await c.post("/api/rustuya/cloud", json={})).status == 200          # the last one is over: a new one
    import asyncio

    await asyncio.sleep(1.2)                                                     # the page went away
    assert manager.closed
    assert (await (await c.get("/api/rustuya/cloud")).json())["state"] == "cancelled"



async def test_restart_stops_stores_and_starts_in_order(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={"a": 1}, options={"x": 1})
    entry.add_to_hass(hass)
    calls = []

    async def unload(entry_id):
        calls.append("unload")
        return True

    async def setup(entry_id):
        calls.append(("setup", dict(entry.data), dict(entry.options)))
        return True

    async def between():
        calls.append("between")

    monkeypatch.setattr(hass.config_entries, "async_unload", unload)
    monkeypatch.setattr(hass.config_entries, "async_setup", setup)
    await panel._restart(hass, entry, options={"x": 2}, between=between)
    assert calls == ["unload", "between", ("setup", {"a": 1}, {"x": 2})]


async def test_a_panel_only_change_adds_or_removes_it_without_a_restart(hass, monkeypatch):
    import custom_components.rustuya as integration

    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PANEL: False, CONF_PACK: True})
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = integration.RuntimeData(
        service=FakeService(), embedded_bridge=None, options=dict(entry.options))
    panel_calls, reloads = [], []

    async def panel_setup(_hass, e):
        panel_calls.append(e.options.get(CONF_PANEL))

    monkeypatch.setattr(panel, "async_setup", panel_setup)
    monkeypatch.setattr(hass.config_entries, "async_reload", lambda entry_id: reloads.append(entry_id) or _done())
    hass.config_entries.async_update_entry(entry, options={CONF_PANEL: True, CONF_PACK: True})
    await integration._async_reload(hass, entry)
    assert panel_calls == [True] and reloads == []
    hass.config_entries.async_update_entry(entry, options={CONF_PANEL: True, CONF_PACK: False})
    await integration._async_reload(hass, entry)
    assert reloads == [entry.entry_id]


async def _done():
    return True


@pytest.mark.parametrize(("disabled", "kept"), [(False, True), (True, False)])
async def test_a_reload_keeps_the_panel_and_disabling_removes_it(hass, loaded, disabled, kept):
    from homeassistant.config_entries import ConfigEntryDisabler

    import custom_components.rustuya as integration

    entry, _ = await loaded({CONF_PANEL: True})
    assert panel.PANEL_URL in hass.data[frontend.DATA_PANELS]
    if disabled:
        entry.disabled_by = ConfigEntryDisabler.USER
    await integration.async_unload_entry(hass, entry)
    assert (panel.PANEL_URL in hass.data.get(frontend.DATA_PANELS, {})) is kept


def test_the_panel_has_every_text_in_korean_too():
    """The panel's own texts (www/rustuya-panel.js, I18N): Korean has every English key, with the same placeholders,
    and every `t("key")` the page uses exists (a missing one would show the raw key)."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[2] / "custom_components/rustuya/www/rustuya-panel.js").read_text()
    block = src[src.index("const I18N = {"):src.index("let LANG")]
    en_block, ko_block = block.split("\n  ko: {")

    def entries(text):
        return {k: set(re.findall(r"\{(\w+)\}", v)) for k, v in re.findall(r'\b(\w+): "((?:[^"\\]|\\.)*)"', text)}

    en, ko = entries(en_block), entries(ko_block)
    assert len(en) > 100 and en.keys() == ko.keys()
    assert {k for k in en if en[k] != ko[k]} == set()
    used = set(re.findall(r'\bt\("(\w+)"', src))
    assert used <= en.keys()


@pytest.mark.parametrize(('device', 'expected_id', 'extra'), [
    ({'id': 'manual', 'name': 'Desk', 'key': 'secret', 'ip': '192.0.2.1'},
     'manual', {'key': 'secret', 'ip': '192.0.2.1'}),
    ({'type': 'SubDevice', 'cid': '12', 'parent_id': 'gw', 'name': 'Desk'},
     '12_Desk', {'cid': '12', 'parent_id': 'gw'}),
])
async def test_manual_bridge_registration(hass, loaded, hass_client, monkeypatch, device, expected_id, extra):
    from fake_manager import FakeManager, install

    monkeypatch.setattr(panel.BridgeView, 'SETTLE', 0)
    manager = FakeManager(sync_result=_diff())
    install(monkeypatch, manager)
    await loaded({CONF_PANEL: True})
    client = await hass_client()
    response = await client.put('/api/rustuya/bridge', json=device)
    assert response.status == 200
    assert (await response.json())['sent'] == 1
    assert manager.commands == [('add', expected_id, 'Desk', extra)]


@pytest.mark.parametrize('device', [None, [], {}, {'id': 123}, {'id': 'x', 'type': 'bad'},
                                   {'id': 'x', 'type': 'SubDevice'}, {'id': 'x', 'cid': '1'},
                                   {'id': 'x', 'unknown': 'value'}])
async def test_invalid_manual_registration(hass, loaded, hass_client, device):
    await loaded({CONF_PANEL: True})
    client = await hass_client()
    assert (await client.put('/api/rustuya/bridge', json=device)).status == 400


async def test_manual_registration_rejects_existing_device(hass, loaded, hass_client, monkeypatch):
    from fake_manager import FakeManager, install

    manager = FakeManager(sync_result=_diff())
    install(monkeypatch, manager)
    await loaded({CONF_PANEL: True})
    client = await hass_client()
    assert (await client.put('/api/rustuya/bridge', json={'id': 'old'})).status == 409
    assert manager.commands == []


@pytest.mark.parametrize('device_id', ['old', 'fan', 'gw'])
async def test_edit_registered_device(hass, loaded, hass_client, monkeypatch, device_id):
    from fake_manager import FakeManager, install

    monkeypatch.setattr(panel.BridgeView, 'SETTLE', 0)
    manager = FakeManager(sync_result=_diff())
    install(monkeypatch, manager)
    await loaded({CONF_PANEL: True})
    client = await hass_client()
    response = await client.patch('/api/rustuya/bridge', json={
        'id': device_id, 'name': 'Edited', 'key': 'new-key', 'ip': '192.0.2.2',
    })
    assert response.status == 200
    assert (await response.json())['sent'] == 1
    assert manager.commands == [('add', device_id, 'Edited', {'key': 'new-key', 'ip': '192.0.2.2'})]


@pytest.mark.parametrize(('body', 'status'), [
    ({'id': 'gone'}, 404),
    ({'id': 'lamp'}, 404),
    ({'ip': '192.0.2.2'}, 400),
    ({'id': 'old', 'type': 'SubDevice', 'cid': '12'}, 400),
    ({'id': 'old', 'unknown': 'x'}, 400),
])
async def test_edit_rejects_invalid_device(hass, loaded, hass_client, monkeypatch, body, status):
    from fake_manager import FakeManager, install

    manager = FakeManager(sync_result=_diff())
    install(monkeypatch, manager)
    await loaded({CONF_PANEL: True})
    client = await hass_client()
    assert (await client.patch('/api/rustuya/bridge', json=body)).status == status
    assert manager.commands == []


async def test_failed_restart_restores_previous_settings(hass, monkeypatch):
    from unittest.mock import AsyncMock

    entry = MockConfigEntry(domain=DOMAIN, data={"devices_path": "good.json"}, options={CONF_PANEL: True})
    entry.add_to_hass(hass)
    monkeypatch.setattr(hass.config_entries, "async_unload", AsyncMock(return_value=True))
    setup = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(hass.config_entries, "async_setup", setup)
    with pytest.raises(panel.RestartFailed, match="Previous settings restored"):
        await panel._restart(hass, entry, data={"devices_path": "bad.json"})
    assert entry.data["devices_path"] == "good.json"
    assert setup.await_count == 2


async def test_settings_remain_editable_after_startup_failure(hass, loaded, hass_client, monkeypatch):
    from unittest.mock import AsyncMock

    entry, _ = await loaded({CONF_PANEL: True})
    entry.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    hass.data[DOMAIN].pop(entry.entry_id)
    apply = AsyncMock()
    monkeypatch.setattr(panel, "_apply", apply)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "il_prefix": "il", "il_source": "tuya"})
    client = await hass_client()
    assert (await client.get("/api/rustuya/settings")).status == 200
    response = await client.put("/api/rustuya/settings", json={"devices_path": "fixed.json"})
    assert response.status == 200
    assert apply.await_count == 1


async def test_registration_waits_for_registry_refresh_before_closing(hass, loaded, hass_client, monkeypatch):
    from unittest.mock import AsyncMock

    from fake_manager import FakeManager, install

    monkeypatch.setattr(panel.BridgeView, "SETTLE", 0)
    manager = FakeManager(sync_result=_diff())
    manager.wait_registry_refresh = AsyncMock()
    install(monkeypatch, manager)
    await loaded({CONF_PANEL: True})
    client = await hass_client()
    response = await client.post("/api/rustuya/bridge", json={"add": ["sub1", "lamp"]})
    assert response.status == 200
    assert (await response.json())["sent"] == 2
    manager.wait_registry_refresh.assert_awaited_once()
    assert manager.closed
