"""Operating on the retained discovery configs by hand (`rustuya-local discovery status|clear|restore`).

The whole discovery state is the retained configs under `<discovery_prefix>/+/<node_id>/+/config`, so a backup is a
map of them, a status is that map against what the retained IL descriptors should produce, and clearing or restoring
is publishing retained payloads. The planning here is pure; `collect` is the one part that reads a broker.

A running service is authoritative for what it publishes: clear configs by hand after turning its discovery off (or
while it is stopped), or a later descriptor change brings them back.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from tuya2ildevice.host.transport import Message, Transport

from .publisher import _text, expected_configs
from .render import Options


@dataclass
class Snapshot:
    descriptors: dict[str, str] = field(default_factory=dict)       # IL descriptor topic -> payload
    configs: dict[str, str] = field(default_factory=dict)           # owned config topic -> payload


async def collect(il: Transport, ha: Transport, options: Options, wait: float = 2.0) -> Snapshot:
    """The retained descriptors and owned configs, as the brokers replay them within `wait` seconds."""
    snap = Snapshot()

    def on_desc(msg: Message) -> None:
        if not msg.topic.rsplit("/", 1)[-1].startswith("_") and _text(msg.payload).strip():
            snap.descriptors[msg.topic] = _text(msg.payload)

    def on_cfg(msg: Message) -> None:
        if _text(msg.payload).strip():
            snap.configs[msg.topic] = _text(msg.payload)
        else:
            snap.configs.pop(msg.topic, None)

    unsubs = [await il.subscribe(f"{options.il_prefix}/+", on_desc),
              await ha.subscribe(f"{options.discovery_prefix}/+/{options.node_id}/+/config", on_cfg)]
    await asyncio.sleep(wait)
    for unsub in unsubs:
        unsub()
    return snap


@dataclass
class Status:
    ok: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)       # expected, not retained
    differs: list[str] = field(default_factory=list)       # retained, but not what is expected
    stale: list[str] = field(default_factory=list)         # retained, no device produces it

    def summary(self) -> str:
        return (f"{len(self.ok)} ok, {len(self.missing)} missing, {len(self.differs)} differ, "
                f"{len(self.stale)} stale")


def status(snap: Snapshot, options: Options) -> Status:
    expected = expected_configs(snap.descriptors, options)
    out = Status()
    for topic, cfg in sorted(expected.items()):
        if topic not in snap.configs:
            out.missing.append(topic)
        else:
            try:
                same = json.loads(snap.configs[topic]) == cfg
            except ValueError:
                same = False
            (out.ok if same else out.differs).append(topic)
    out.stale = sorted(snap.configs.keys() - expected.keys())
    return out


def clear_plan(snap: Snapshot, options: Options, stale_only: bool = False) -> list[str]:
    """The owned configs to clear: all of them, or only the ones no device produces."""
    return status(snap, options).stale if stale_only else sorted(snap.configs)


def restore_plan(snap: Snapshot, saved: dict[str, str]) -> list[tuple[str, str]]:
    """(topic, payload) to publish retained so the owned configs are exactly `saved` again: the saved ones back,
    the ones added since cleared."""
    out = [(t, p) for t, p in sorted(saved.items())]
    out += [(t, "") for t in sorted(snap.configs.keys() - saved.keys())]
    return out


def save_backup(directory: Path, configs: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"discovery-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    path.write_text(json.dumps(configs, indent=1, sort_keys=True))
    return path


def latest_backup(directory: Path) -> Path | None:
    files = sorted(directory.glob("discovery-*.json")) if directory.is_dir() else []
    return files[-1] if files else None


def load_backup(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"{path} is not a discovery backup")
    return data
