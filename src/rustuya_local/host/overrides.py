"""User rule files: a directory (or one `.json` file) of partial rule objects, loaded and followed as it changes.

Each `*.json` is a rule layer (product/device fixes under `overrides`), merged in filename order over the rules
tuya2ildevice ships and validated by its engine. `SETTINGS_FILE` (``zz_settings.json``) holds the settings written
through IL, a block per device (`OverrideWatcher.save_settings`); it can be edited like any other file.

Files starting with `.` or `_` are skipped. Nothing here raises for a bad file: a file that does
not load is left out and reported in `OverrideSet.warnings`, and the rest still loads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tuya2ildevice import OverrideError, merge_all

from rustuya_local.host.rules import load_rules_tolerant, upgrade_overrides

from .runner import Runner

_LOGGER = logging.getLogger(__name__)
SETTINGS_FILE = "zz_settings.json"               # ordered with user files


@dataclass
class OverrideSet:
    rules: dict | None = None
    overrides: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(p for p in path.iterdir()
                  if p.is_file() and p.suffix == ".json" and p.name[0] not in "._")


def load_overrides(path: str | Path) -> OverrideSet:
    """Read a directory (or one `.json` file) of rule files over `TUYA_ENGINE_RULES`. Never raises for a bad file."""
    locations = [p for p in os.environ.get("TUYA_ENGINE_RULES", "").split(os.pathsep) if p]
    if Path(path).exists():
        locations.append(str(path))
    result = load_rules_tolerant(locations)
    return OverrideSet(result["rules"], result["rules"].get("overrides", {}), list(result["warnings"]))


class OverrideWatcher:
    """Follow an overrides directory: when a file is added, changed or removed, load it all again and reload the
    runner's Hub with it (`base`, e.g. overrides from the host's own configuration, is merged on top). A file the
    engine refuses is left out and reported (`load_overrides`); overrides the Hub refuses as a whole (`OverrideError`,
    a bad `base`) are reported and the ones in effect stay."""

    def __init__(self, path: str | Path, runner: Runner, interval: float = 5.0, base: dict | None = None) -> None:
        self.path = Path(path)
        self.runner = runner
        self.interval = interval
        self.base = base or {}
        self._stamp: Any = None
        self._task: asyncio.Task | None = None

    def load(self) -> OverrideSet:
        loaded = load_overrides(self.path)
        for w in loaded.warnings:
            _LOGGER.warning("overrides: %s", w)
        loaded.overrides = merge_all([loaded.overrides, self.base])
        return loaded

    def start(self) -> None:
        """Watch from now on (the state at this point counts as already applied)."""
        self._stamp = self._stat()
        self.watch()

    async def load_initial(self) -> OverrideSet:
        """Load what is there now, off the event loop, and remember it as applied: pass the result to the Hub you
        create, then `watch()`."""
        def work() -> OverrideSet:
            self._stamp = self._stat()
            return self.load()
        return await asyncio.to_thread(work)

    def watch(self) -> None:
        """Start following the directory from the state last loaded."""
        if self._task is None:
            self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def _stat(self) -> Any:
        stamp = []
        for p in _files(self.path):
            try:
                st = p.stat()
            except OSError:
                continue
            stamp.append((p.name, st.st_mtime_ns, st.st_size))
        return tuple(stamp)

    def check(self) -> bool:
        """Reload once if anything changed since the last look; True if it did."""
        stamp = self._stat()
        if stamp == self._stamp:
            return False
        self._stamp = stamp
        self._apply(self.load())
        return True

    def _apply(self, loaded: OverrideSet) -> None:
        if self.runner is None:
            return
        try:
            self.runner.reload(loaded.overrides, rules=loaded.rules)
        except OverrideError as e:
            _LOGGER.warning("overrides in %s not applied: %s", self.path, e)

    async def save_settings(self, device_id: str, block: dict) -> None:
        """Keep `block` as the device's entry in `SETTINGS_FILE` (an empty block removes it), written atomically, then
        load the overrides again and reload the Hub at once (not at the next look). Never raises: a failure is
        logged. With a single `.json` file for the overrides there is nowhere to write, and nothing is."""
        if self.path.is_file() or (self.path.suffix == ".json" and not self.path.is_dir()):
            _LOGGER.warning("overrides are the one file %s: the settings of %s are not saved", self.path, device_id)
            return
        try:
            await asyncio.to_thread(self._write_settings, device_id, block)
        except Exception:
            _LOGGER.exception("could not save the settings of %s in %s", device_id, self.path / SETTINGS_FILE)
            return
        try:
            def work() -> tuple[Any, OverrideSet]:
                return self._stat(), self.load()
            self._stamp, loaded = await asyncio.to_thread(work)
            self._apply(loaded)
        except Exception:
            _LOGGER.exception("could not apply the settings of %s", device_id)

    def _write_settings(self, device_id: str, block: dict) -> None:
        path = self.path / SETTINGS_FILE
        self.path.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if path.is_file():
            data = json.loads(path.read_text("utf-8"))       # a file that does not parse is left for its owner to fix
            if not isinstance(data, dict):
                raise ValueError(f"{path}: expected a rule object")
        entries = data.setdefault('overrides', {})
        if not isinstance(entries, dict):
            raise TypeError(f'{path}: overrides must be an object')
        entries = data['overrides'] = upgrade_overrides(entries)    # keys an older version saved, in today's form
        if block:
            entries[device_id] = block
        else:
            entries.pop(device_id, None)
        tmp = path.with_name(f".{path.name}.tmp")             # a dot file: never loaded
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", "utf-8")
        os.replace(tmp, path)

    async def _loop(self) -> None:
        # the file work (stat, read) runs in a worker thread: a host such as Home Assistant
        # must not block its event loop on it; only the reload itself runs on the loop
        while True:
            await asyncio.sleep(self.interval)
            stamp = await asyncio.to_thread(self._stat)
            if stamp == self._stamp:
                continue
            self._stamp = stamp
            self._apply(await asyncio.to_thread(self.load))
