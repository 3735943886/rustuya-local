"""Entry 1.1 -> 1.2: the default files move under `.storage/rustuya/`; a path the user chose stays."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rustuya.const import (
    CONF_BRIDGE_MODE,
    CONF_BRIDGE_ROOT,
    CONF_BRIDGE_STATE_FILE,
    CONF_DEVICES_PATH,
    DOMAIN,
)


def _entry(hass, devices_path: str, state_file: str) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=1, data={
        CONF_BRIDGE_MODE: "embedded", "broker_host": "h", "broker_port": 1883, "broker_username": "",
        "broker_password": "", CONF_BRIDGE_ROOT: "rustuya", CONF_DEVICES_PATH: devices_path,
        CONF_BRIDGE_STATE_FILE: state_file})
    entry.add_to_hass(hass)
    return entry


async def _migrate(hass, entry) -> None:
    with patch("custom_components.rustuya.async_setup_entry", return_value=True), \
            patch("custom_components.rustuya.async_unload_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)


def _storage(hass, tmp_path) -> Path:
    hass.config.config_dir = str(tmp_path)          # not the testing config shared by every test
    return tmp_path / ".storage"


async def test_default_files_move(hass, tmp_path):
    storage = _storage(hass, tmp_path)
    storage.mkdir()
    (storage / "rustuya_tuyadevices.json").write_text("[]")
    (storage / "rustuya_bridge_state.json").write_text("{}")
    (storage / "tuyacreds.json").write_text('{"user_code": "x"}')
    entry = _entry(hass, str(storage / "rustuya_tuyadevices.json"), str(storage / "rustuya_bridge_state.json"))

    await _migrate(hass, entry)

    new = storage / "rustuya"
    assert entry.minor_version == 2
    assert entry.data[CONF_DEVICES_PATH] == str(new / "tuyadevices.json")
    assert entry.data[CONF_BRIDGE_STATE_FILE] == str(new / "bridge_state.json")
    assert (new / "tuyadevices.json").read_text() == "[]"
    assert (new / "bridge_state.json").read_text() == "{}"
    assert (new / "tuyacreds.json").is_file()
    assert not (storage / "rustuya_tuyadevices.json").exists() and not (storage / "tuyacreds.json").exists()


async def test_default_path_without_a_file_yet(hass, tmp_path):
    storage = _storage(hass, tmp_path)
    entry = _entry(hass, str(storage / "rustuya_tuyadevices.json"), str(storage / "rustuya_bridge_state.json"))

    await _migrate(hass, entry)

    assert entry.data[CONF_DEVICES_PATH] == str(storage / "rustuya" / "tuyadevices.json")
    assert not (storage / "rustuya" / "tuyadevices.json").exists()


async def test_a_chosen_path_stays(hass, tmp_path):
    _storage(hass, tmp_path / "config")
    devices, state = tmp_path / "mine.json", tmp_path / "state.json"
    devices.write_text("[]")
    entry = _entry(hass, str(devices), str(state))

    await _migrate(hass, entry)

    assert entry.minor_version == 2
    assert entry.data[CONF_DEVICES_PATH] == str(devices) and entry.data[CONF_BRIDGE_STATE_FILE] == str(state)
    assert devices.is_file()
