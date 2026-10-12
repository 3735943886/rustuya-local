"""The device list: `tuyadevices.json`, which rustuya-manager keeps (its QR-login wizard writes it, atomically). Each
entry is a Tuya cloud record with the schema tuya-interpreter needs (`category`, `product_id`, `function`,
`status_range`, `local_strategy`). rustuya-local reads it and follows it as it changes; it never writes it."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..async_tools import cancel_task, run_in_thread

_LOGGER = logging.getLogger(__name__)


class DeviceRegistry(Protocol):
    def sync_devices(self, records: list[dict]) -> object: ...


@dataclass(frozen=True)
class _DeviceUpdate:
    stamp: tuple[int, int] | None
    records: list[dict] | None


def parse_devices(raw: str | bytes) -> tuple[list[dict], list[str]]:
    """(usable records, ids skipped). The file is a list of records or a dict of them; a record without an `id` or a
    `category` cannot be turned into an IL device (there is nothing to classify it by)."""
    data = json.loads(raw)
    entries = list(data.values()) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("expected a list or a dict of device records")  # noqa: TRY004  (callers handle a bad file as ValueError)
    usable, skipped = [], []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        if not entry.get("category"):
            skipped.append(str(entry["id"]))
            continue
        usable.append(entry)
    return usable, skipped


def load_devices(path: str | Path) -> list[dict]:
    usable, skipped = parse_devices(Path(path).read_bytes())
    if skipped:
        _LOGGER.warning("%d device(s) have no category in %s and are not driven: %s", len(skipped), path, skipped)
    return usable


class DeviceWatcher:
    """Follow the device file: a new record is added to the runner, a changed one replaced, a removed one removed.
    A file that cannot be read or parsed right now (half written by something else) leaves things as they are."""

    def __init__(self, path: str | Path, runner: DeviceRegistry, interval: float = 5.0) -> None:
        self.path = Path(path)
        self.runner = runner
        self.interval = interval
        self._stamp: tuple[int, int] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._stamp = self._stat()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        await cancel_task(task)

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def check(self) -> bool:
        """Sync once if the file changed since the last look; True if it did."""
        return self._apply(self._read_changes())

    def _read_changes(self) -> _DeviceUpdate | None:
        stamp = self._stat()
        if stamp is None or stamp == self._stamp:
            return None
        try:
            records = load_devices(self.path)
        except (OSError, ValueError):
            _LOGGER.warning("could not read %s; keeping the devices as they are", self.path)
            return _DeviceUpdate(None, None)        # look again next time
        return _DeviceUpdate(stamp, records)

    def _apply(self, update: _DeviceUpdate | None) -> bool:
        if update is None:
            return False
        if update.records is not None:
            self.runner.sync_devices(update.records)
        self._stamp = update.stamp
        return True

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                self._apply(await run_in_thread(self._read_changes))
            except Exception:
                _LOGGER.exception("could not sync devices from %s; will retry", self.path)
