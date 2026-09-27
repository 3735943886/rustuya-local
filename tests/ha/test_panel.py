"""The advanced panel (panel.py): registered with the `panel` option, and its file API on <config>/rustuya_converters."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    async def stop(self) -> None:
        pass

    def sync_pack_now(self) -> bool:
        self.syncs.append(1)
        return self.pack_on


@dataclass
class FakeRuntime:
    service: FakeService


@pytest.fixture
async def loaded(hass, tmp_path):
    """A loaded entry with a fake runtime (no MQTT), config dir in tmp_path, and http up (the panel registry needs no
    running frontend, which would need the hass_frontend package)."""
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


async def test_the_api_is_for_administrators(hass, loaded, hass_client, hass_read_only_access_token):
    await loaded({CONF_PANEL: True})
    c = await hass_client(hass_read_only_access_token)
    assert (await c.get("/api/rustuya/converters")).status == 403
    assert (await c.put("/api/rustuya/converters/a.py", json={"content": "X = 1"})).status == 403


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
    await loaded({CONF_PANEL: True})
    c = await hass_client()

    r = await c.get("/api/rustuya/bridge")
    assert r.status == 200
    body = await r.json()
    assert [(d["id"], d["category"]) for d in body["devices"]] == [
        ("sub1", "missing"), ("lamp", "missing"), ("old", "orphan"), ("fan", "mismatch"), ("gw", "synced")]
    by_id = {d["id"]: d for d in body["devices"]}
    assert by_id["sub1"]["cloud"]["parent_id"] == "gw" and by_id["sub1"]["bridge"] is None
    assert by_id["old"]["cloud"] is None and by_id["old"]["bridge"]["name"] == "Old plug"
    assert by_id["fan"]["reasons"] == ["IP: 10.0.0.1 -> 10.0.0.2"]
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
