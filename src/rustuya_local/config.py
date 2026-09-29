"""Configuration of the standalone runner: where the bridge and the IL consumer's broker are, and which devices to drive.

    {
      "bridge":  {"host": "localhost", "port": 1883, "root": "rustuya", "username": null, "password": null},
      "il":      {"host": "localhost", "port": 1883, "prefix": "il", "source": "tuya"},
      "devices": "tuyadevices.json",
      "custom_converters": "custom_converters",
      "pack": true,
      "watch_interval": 5,
      "options": {"allow_hazardous": false, "expose_unused": false, "overrides": {}}
    }

`devices` is a path (relative to the config file), which is followed as rustuya-manager rewrites it every
`watch_interval` seconds (0 = read once), or the list itself. `custom_converters` is a directory of user overrides and
code converters (see `tuya2ildevice.host.load_overrides`), followed the same
way; `options.overrides` is merged over it. A device's settings written through IL (a cover's direction switches) are
kept in that directory too; `options.device_settings: false` stops offering them. `pack` (on by default) copies
tuya2ildevice's override pack, fixes published between releases, into that directory daily. Every key but `devices`
has a default. The bridge's own topic templates are read from its retained `{root}/bridge/config` at start.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from tuya2ildevice.host import MqttTransport, load_devices

from .service import Settings

KEYS = frozenset({"bridge", "il", "devices", "options", "watch_interval", "custom_converters", "pack"})
OPTIONS = frozenset({"allow_hazardous", "expose_unused", "overrides", "converters", "use_quirks", "device_settings"})
_DEFAULT = Settings()


@dataclass
class Broker:
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Broker:
        """The broker keys of a `bridge` / `il` block; its other keys (`root`, `prefix`, `source`) are the Config's."""
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    async def connect(self, client_id: str, will: Any = None) -> MqttTransport:
        transport = MqttTransport(self.host, self.port, client_id=client_id, username=self.username,
                                  password=self.password, will=will)
        await transport.connect()
        return transport


def _resolve(path: str, base: Path | None) -> Path:
    """`path` relative to the config file's directory; an absolute one as is."""
    p = Path(path)
    return p if p.is_absolute() or base is None else base / p


@dataclass
class Config:
    bridge: Broker = field(default_factory=Broker)
    il: Broker = field(default_factory=Broker)
    root: str = _DEFAULT.root
    prefix: str = _DEFAULT.prefix
    source: str = _DEFAULT.source
    devices: list[dict] = field(default_factory=list)
    devices_path: Path | None = None
    overrides_path: Path | None = None
    watch_interval: float = _DEFAULT.watch_interval
    pack: bool = True
    hub_options: dict[str, Any] = field(default_factory=dict)
    """Keyword arguments for `tuya2ildevice.Hub`: `allow_hazardous`, `expose_unused`, `device_settings`, `overrides`,
    `converters`."""

    @classmethod
    def from_dict(cls, data: dict, base: Path | None = None) -> Config:
        if unknown := set(data) - KEYS:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        options = dict(data.get("options", {}))
        if bad := set(options) - OPTIONS:
            raise ValueError(f"unknown options: {sorted(bad)}")
        bridge, il = data.get("bridge", {}), data.get("il", {})
        devices, devices_path = data.get("devices", []), None
        if isinstance(devices, str):
            devices_path = _resolve(devices, base)
            devices = load_devices(devices_path)
        converters = data.get("custom_converters")
        return cls(
            bridge=Broker.from_dict(bridge),
            il=Broker.from_dict(il),
            root=bridge.get("root", _DEFAULT.root),
            prefix=il.get("prefix", _DEFAULT.prefix),
            source=il.get("source", _DEFAULT.source),
            devices=list(devices),
            devices_path=devices_path,
            overrides_path=_resolve(converters, base) if converters else None,
            watch_interval=float(data.get("watch_interval", _DEFAULT.watch_interval)),
            pack=bool(data.get("pack", True)),
            hub_options=options,
        )

    def settings(self) -> Settings:
        return Settings(root=self.root, prefix=self.prefix, source=self.source, devices=list(self.devices),
                        devices_path=self.devices_path, overrides_path=self.overrides_path,
                        watch_interval=self.watch_interval, pack=self.pack, hub_options=dict(self.hub_options))

    @classmethod
    def from_file(cls, path: str | Path) -> Config:
        path = Path(path)
        return cls.from_dict(json.loads(path.read_text()), path.parent)
