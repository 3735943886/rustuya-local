"""Configuration of the standalone runner: where the bridge and the IL consumer's broker are, and which devices to drive.

    {
      "bridge":  {"host": "localhost", "port": 1883, "root": "rustuya", "username": null, "password": null},
      "il":      {"host": "localhost", "port": 1883, "prefix": "il", "source": "tuya"},
      "devices": "tuyadevices.json",
      "custom_converters": "custom_converters",
      "discovery": {"prefix": "homeassistant", "node_id": "rustuya_local", "broker": null},
      "watch_interval": 5,
      "options": {"allow_hazardous": false, "expose_unused": false, "overrides": {}}
    }

`devices` is a path (relative to the config file), which is followed as rustuya-manager rewrites it every
`watch_interval` seconds (0 = read once), or the list itself. `custom_converters` is a directory of user overrides and
code converters (see `tuya2ildevice.host.load_overrides`; rustuya-homeassistant v1 files work too), followed the same
way; `options.overrides` is merged over it. `discovery`, when present, also publishes Home Assistant MQTT discovery for
the devices (entities without any custom integration; see `rustuya_local.discovery`), on `discovery.broker` or else
the IL broker. Leave it out when il-ha shows the devices, or they would appear twice. Every key but `devices` has a default. The bridge's own topic templates are
read from its retained `{root}/bridge/config` at start.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tuya2ildevice.host import load_devices

from .service import Discovery, Settings


@dataclass
class Broker:
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None


@dataclass
class Config:
    bridge: Broker = field(default_factory=Broker)
    il: Broker = field(default_factory=Broker)
    root: str = "rustuya"
    prefix: str = "il"
    source: str = "tuya"
    devices: list[dict] = field(default_factory=list)
    devices_path: Path | None = None
    overrides_path: Path | None = None
    discovery: Discovery | None = None
    discovery_broker: Broker | None = None
    watch_interval: float = 5.0
    hub_options: dict[str, Any] = field(default_factory=dict)
    """Keyword arguments for `tuya2ildevice.Hub`: `allow_hazardous`, `expose_unused`, `overrides`, `converters`."""

    @classmethod
    def from_dict(cls, data: dict, base: Path | None = None) -> "Config":
        unknown = set(data) - {"bridge", "il", "devices", "options", "watch_interval", "custom_converters", "discovery"}
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        bridge, il = dict(data.get("bridge", {})), dict(data.get("il", {}))
        devices, devices_path = data.get("devices", []), None
        if isinstance(devices, str):
            path = Path(devices)
            devices_path = path if path.is_absolute() or base is None else base / path
            devices = load_devices(devices_path)
        overrides_path = None
        if data.get("custom_converters"):
            path = Path(data["custom_converters"])
            overrides_path = path if path.is_absolute() or base is None else base / path
        discovery, discovery_broker = None, None
        if data.get("discovery") is not None:
            d = dict(data["discovery"])
            bad = set(d) - {"prefix", "node_id", "relay_prefix", "sweep_after", "broker"}
            if bad:
                raise ValueError(f"unknown discovery keys: {sorted(bad)}")
            if d.get("broker"):
                discovery_broker = Broker(**{k: v for k, v in d["broker"].items() if k in Broker.__dataclass_fields__})
            discovery = Discovery(**{k: v for k, v in d.items() if k != "broker"})
        options = dict(data.get("options", {}))
        bad = set(options) - {"allow_hazardous", "expose_unused", "overrides", "converters", "use_quirks"}
        if bad:
            raise ValueError(f"unknown options: {sorted(bad)}")
        return cls(
            bridge=Broker(**{k: v for k, v in bridge.items() if k in Broker.__dataclass_fields__}),
            il=Broker(**{k: v for k, v in il.items() if k in Broker.__dataclass_fields__}),
            root=bridge.get("root", "rustuya"),
            prefix=il.get("prefix", "il"),
            source=il.get("source", "tuya"),
            devices=list(devices),
            devices_path=devices_path,
            overrides_path=overrides_path,
            discovery=discovery,
            discovery_broker=discovery_broker,
            watch_interval=float(data.get("watch_interval", 5)),
            hub_options=options,
        )

    def settings(self) -> Settings:
        return Settings(root=self.root, prefix=self.prefix, source=self.source, devices=list(self.devices),
                        devices_path=self.devices_path, overrides_path=self.overrides_path,
                        watch_interval=self.watch_interval, hub_options=dict(self.hub_options),
                        discovery=self.discovery)

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        path = Path(path)
        return cls.from_dict(json.loads(path.read_text()), path.parent)
