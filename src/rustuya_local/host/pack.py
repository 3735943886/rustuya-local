"""The override pack: fixes for non-standard devices published on tuya-interpreter's `master` branch (`rules/builtin/`) and copied into
the host's rule directory, so a fix reaches users without a release. `OverrideWatcher` then loads the copied files
like the user's own. The files are rule layers (JSON); fetching them is the host's, the engine only merges them.

`builtin/manifest.json` lists the files with their SHA-256 (built by `scripts/build_pack.py`):

    {"version": 1, "files": [{"name": "00_pack_abc.json", "sha256": "...", "requires": 1, "until": 3}]}

`requires` / `until` (optional) bound the `LEVEL` a file is for: a file that needs a newer host is not copied, and one
whose fix is built in (its `until` is at most `LEVEL`) is removed. `LEVEL` goes up when a release adds something pack files may use, or makes a pack file unnecessary.

Ownership is explicit. `sync()` records what it wrote, by name and hash, in `.tuya2ildevice_pack.json` in the directory
(dot files are not loaded) and only ever writes or removes those files:

- a file of the user's with a pack file's name is left alone (the pack file is not copied);
- a pack file the user has edited since it was copied becomes the user's: it is no longer updated or removed;
- a file that fails to download or verify is skipped, and the copy already there stays;
- `SETTINGS_FILE` (the settings written through IL) is never taken from the pack.

The manifest is trusted as fetched over TLS; each file must match its hash and be a JSON object. Everything here is
blocking (urllib): call it from a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..files import atomic_write
from .overrides import SETTINGS_FILE

_LOGGER = logging.getLogger(__name__)

LEVEL = 4                                         # 2: `cover` settings; 3: `delta`; 4: rule-layer files (tuya2ildevice 0.4, tuya-interpreter)
BASE_URL = "https://raw.githubusercontent.com/3735943886/tuya-interpreter/master/rules/builtin/"
MANIFEST = "manifest.json"
LEDGER = ".tuya2ildevice_pack.json"                # kept across the rename: existing installs own their files by it
MAX_BYTES = 1024 * 1024
TIMEOUT = 15
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.json$")
_HASH = re.compile(r"[A-Fa-f0-9]{64}")
_SCHEMES = ("https", "http", "file")              # file:// for tests and offline mirrors


class PackError(Exception):
    """The pack could not be fetched or a file of it could not be used; the message is fit for a user."""


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)        # the user's files with a pack file's name, or pack files they edited
    failed: list[str] = field(default_factory=list)      # "<name>: <why>"

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)

    def as_dict(self) -> dict[str, list[str]]:
        return {"added": self.added, "updated": self.updated, "removed": self.removed, "kept": self.kept,
                "failed": self.failed}


def _download(url: str) -> bytes:
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in _SCHEMES:
        raise PackError(f"unsupported URL scheme: {scheme or '(none)'}")
    req = urllib.request.Request(url, headers={"User-Agent": "rustuya-local-pack"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:                     # the scheme is checked above
            data = resp.read(MAX_BYTES + 1)
    except (OSError, ValueError) as e:
        raise PackError(f"cannot fetch {url}: {e}") from e
    if len(data) > MAX_BYTES:
        raise PackError(f"{url} is larger than {MAX_BYTES} bytes")
    return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_manifest(base_url: str = BASE_URL) -> dict[str, Any]:
    raw = _download(base_url + MANIFEST)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PackError(f"the manifest is not JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        raise PackError("the manifest has no `files` list")
    return doc


def read_ledger(directory: Path) -> dict[str, str]:
    """{name: sha256} of the pack files `sync` wrote into `directory`."""
    try:
        doc = json.loads((directory / LEDGER).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    files = doc.get("files") if isinstance(doc, dict) else None
    if not isinstance(files, dict):
        return {}
    return {name: digest.lower() for name, digest in files.items()
            if _pack_name(name) and isinstance(digest, str) and _HASH.fullmatch(digest)
            and _managed_path(directory, name)}


def _pack_name(name: Any) -> bool:
    return (isinstance(name, str) and _NAME.fullmatch(name) is not None
            and name not in (SETTINGS_FILE, MANIFEST))


def _managed_path(directory: Path, name: str) -> bool:
    """Only direct files inside the managed directory; never follow a user-created symlink."""
    path = directory / name
    return not path.is_symlink() and path.resolve().parent == directory.resolve()


def _check(name: str, data: bytes) -> None:
    """A file that could not load is not written: it must parse to a JSON object."""
    try:
        if not isinstance(json.loads(data.decode("utf-8")), dict):
            raise PackError(f"{name}: not a JSON object")
    except (UnicodeDecodeError, ValueError) as e:
        raise PackError(f"{name}: {e}") from e


def _wanted(doc: dict[str, Any]) -> dict[str, str]:
    """{name: sha256} of the manifest's files meant for this LEVEL."""
    out: dict[str, str] = {}
    for entry in doc["files"]:
        if not isinstance(entry, dict):
            continue
        name, sha = entry.get("name"), entry.get("sha256")
        if (not _pack_name(name) or not isinstance(sha, str) or not _HASH.fullmatch(sha)
                or any(key in entry and type(entry[key]) is not int for key in ("requires", "until"))):
            _LOGGER.warning("pack: manifest entry %r ignored", entry)
            continue
        if entry.get("requires", 0) > LEVEL or entry.get("until", LEVEL + 1) <= LEVEL:
            continue
        out[name] = sha.lower()
    return out


def check(directory: str | Path, *, base_url: str = BASE_URL,
          bundled: str | Path | None = None) -> bool:
    """Check for applicable changes without downloading rule files or writing anything.

    Bundled rules are already installed; user-owned copies are preserved just as in sync().
    """
    directory = Path(directory)
    wanted = _wanted(fetch_manifest(base_url))
    before = read_ledger(directory)
    for name, sha in wanted.items():
        if not _managed_path(directory, name):
            continue
        path = directory / name
        current = _sha(path.read_bytes()) if path.is_file() else None
        if current == sha or (current is not None and before.get(name) != current):
            continue
        if current is None and bundled is not None:
            installed = Path(bundled) / name
            if installed.is_file() and _sha(installed.read_bytes()) == sha:
                continue
        return True
    return any(name not in wanted and (directory / name).is_file()
               and _sha((directory / name).read_bytes()) == sha for name, sha in before.items())


def sync(directory: str | Path, *, base_url: str = BASE_URL, manifest: dict[str, Any] | None = None) -> SyncResult:
    """Make the pack files in `directory` those of the manifest (fetched from `base_url` unless given). Raises
    `PackError` only when the manifest cannot be had; a bad file is reported in the result and the rest go on."""
    directory = Path(directory)
    doc = manifest if manifest is not None else fetch_manifest(base_url)
    wanted = _wanted(doc)
    directory.mkdir(parents=True, exist_ok=True)
    before = read_ledger(directory)
    owned: dict[str, str] = {}
    out = SyncResult()

    for name, sha in wanted.items():
        if not _managed_path(directory, name):
            out.kept.append(name)
            continue
        path = directory / name
        current = _sha(path.read_bytes()) if path.is_file() else None
        if current == sha:
            owned[name] = sha
            continue
        if current is not None and before.get(name) != current:
            out.kept.append(name)                 # the user's own file, or our copy the user has since edited
            continue
        try:
            data = _download(base_url + name)
            if _sha(data) != sha:
                raise PackError(f"{name}: does not match the manifest's hash")
            _check(name, data)
            atomic_write(path, data)
        except (PackError, OSError) as e:
            out.failed.append(str(e) if str(e).startswith(name) else f"{name}: {e}")
            if name in before and current is not None:
                owned[name] = before[name]        # still ours, still the old copy
            continue
        (out.updated if current is not None else out.added).append(name)
        owned[name] = sha

    for name, sha in before.items():
        if name in wanted:
            continue
        if not _managed_path(directory, name):
            out.kept.append(name)
            continue
        path = directory / name
        if not path.is_file():
            continue
        if _sha(path.read_bytes()) != sha:
            out.kept.append(name)                 # edited by the user: theirs now
            continue
        try:
            path.unlink()
            out.removed.append(name)
        except OSError as e:
            out.failed.append(f"{name}: {e}")
            owned[name] = sha

    ledger = json.dumps({"version": 1, "files": dict(sorted(owned.items()))}, indent=2) + "\n"
    if owned or before:
        atomic_write(directory / LEDGER, ledger)
    for kind, names in (("added", out.added), ("updated", out.updated), ("removed", out.removed)):
        if names:
            _LOGGER.info("pack: %s %s", kind, ", ".join(names))
    for line in out.failed:
        _LOGGER.warning("pack: %s", line)
    return out
