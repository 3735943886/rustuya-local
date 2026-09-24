import json

import pytest

from rustuya_local.config import Config


def test_defaults_and_a_devices_path_relative_to_the_config(tmp_path):
    (tmp_path / "devs.json").write_text(json.dumps([{"id": "a", "category": "dj"}]))
    (tmp_path / "c.json").write_text(json.dumps({"devices": "devs.json", "bridge": {"host": "b", "port": 1884, "root": "r"},
                                                 "il": {"prefix": "x"}, "options": {"expose_unused": True}}))
    c = Config.from_file(tmp_path / "c.json")
    assert c.devices == [{"id": "a", "category": "dj"}] and c.devices_path == tmp_path / "devs.json" and (c.bridge.host, c.bridge.port, c.root) == ("b", 1884, "r")
    assert (c.il.host, c.prefix, c.source) == ("localhost", "x", "tuya") and c.hub_options == {"expose_unused": True}


def test_unknown_keys_are_errors_not_silently_ignored():
    with pytest.raises(ValueError, match="unknown config keys"):
        Config.from_dict({"devicez": []})
    with pytest.raises(ValueError, match="unknown options"):
        Config.from_dict({"options": {"expose": 1}})


def test_a_custom_converters_directory_relative_to_the_config(tmp_path):
    (tmp_path / "c.json").write_text(json.dumps({"custom_converters": "conv", "watch_interval": 0}))
    c = Config.from_file(tmp_path / "c.json")
    assert c.overrides_path == tmp_path / "conv"
    s = c.settings()
    assert s.overrides_path == tmp_path / "conv" and s.watch_interval == 0 and s.devices == []


def test_discovery_options():
    c = Config.from_dict({"discovery": {"node_id": "house", "broker": {"host": "ha", "port": 1884}}})
    assert c.discovery.node_id == "house" and c.discovery.prefix == "homeassistant"
    assert (c.discovery_broker.host, c.discovery_broker.port) == ("ha", 1884)
    assert c.settings().discovery is c.discovery
    assert Config.from_dict({}).discovery is None
    with pytest.raises(ValueError, match="unknown discovery keys"):
        Config.from_dict({"discovery": {"nodeid": "x"}})
