"""The plugin tab's backend: the settings file and the custom_converters directory, edited from the manager's web UI.

Everything here is plain file work on the plugin's data dir (`settings.json`, `custom_converters/`) so it can be tested
without a web server; `router()` wraps it for FastAPI. A saved setting restarts the service (`on_settings`); a saved
converter file needs nothing, the running service follows the directory. The converters directory runs `.py` files
in-process, like the manager's own plugin directory: this is an admin surface.
"""
# no `from __future__ import annotations`: FastAPI resolves the handlers' annotations, and `Body` is imported locally

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

from ..validation import topic_ok

DEFAULTS: dict[str, Any] = {"il": {"prefix": "il", "source": "tuya"},
                            "options": {"allow_hazardous": False, "expose_unused": False, "use_quirks": True,
                                        "pack": True}}
SETTINGS_FILE = "settings.json"
CONVERTERS_DIR = "custom_converters"
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.(json|py)$")


class Invalid(ValueError):
    pass


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---- settings ---------------------------------------------------------------------------------------------------

def read_raw_settings(data_dir: Path) -> Any:
    """The settings file as it is (`{}` without one)."""
    path = data_dir / SETTINGS_FILE
    return json.loads(path.read_text()) if path.is_file() else {}


def _over_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    return {section: {**defaults, **(raw.get(section) or {})} for section, defaults in DEFAULTS.items()}


def read_settings(data_dir: Path) -> dict[str, Any]:
    """The settings as the service uses them: the file's values over the defaults."""
    return _over_defaults(read_raw_settings(data_dir))


def validate_settings(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - set(DEFAULTS):
        raise Invalid("settings take `il` and `options`")
    if any(not isinstance(body[k], dict) for k in body):
        raise Invalid("settings sections must be objects")
    merged = _over_defaults(body)
    il, options = merged["il"], merged["options"]
    if set(il) - {"prefix", "source"}:
        raise Invalid("`il` takes `prefix` and `source`")
    prefix, source = il["prefix"], il["source"]
    if not topic_ok(prefix):
        raise Invalid("`il.prefix` is one or more topic levels (`il`, `il/tuya`), without wildcards or spaces")
    if not topic_ok(source, source=True):
        raise Invalid("`il.source` is one topic level, without wildcards or spaces")
    if set(options) - set(DEFAULTS["options"]) or not all(isinstance(v, bool) for v in options.values()):
        raise Invalid(f"`options` takes the booleans {sorted(DEFAULTS['options'])}")
    return {"il": il, "options": options}


def save_settings(data_dir: Path, body: Any) -> dict[str, Any]:
    settings = validate_settings(body)
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(data_dir / SETTINGS_FILE, json.dumps(settings, indent=2) + "\n")
    return settings


# ---- custom_converters ------------------------------------------------------------------------------------------

def _file(directory: Path, name: str) -> Path:
    if not _FILE.match(name):
        raise Invalid("a converter file is `<name>.json` or `<name>.py` (letters, digits, `_`, `.`, `-`)")
    return directory / name


def check(directory: Path) -> list[str]:
    """What the service would report loading the directory now."""
    from tuya2ildevice.host import load_overrides

    return list(load_overrides(directory).warnings) if directory.is_dir() else []


def _origin(path: Path, ledger: dict[str, str]) -> str:
    """`user`, `pack` (the override pack's copy, updated by its sync) or `pack_edited` (a pack copy the user has
    changed: the next sync leaves it alone for good, so it is the user's from then on)."""
    sha = ledger.get(path.name)
    if sha is None:
        return "user"
    return "pack" if hashlib.sha256(path.read_bytes()).hexdigest() == sha else "pack_edited"


def list_converters(directory: Path) -> dict[str, Any]:
    from tuya2ildevice.host.pack import read_ledger

    files = sorted(p for p in directory.iterdir() if p.is_file() and _FILE.match(p.name)) if directory.is_dir() else []
    ledger = read_ledger(directory) if files else {}
    return {"files": [{"name": p.name, "size": p.stat().st_size, "origin": _origin(p, ledger)} for p in files],
            "warnings": check(directory)}


def read_converter(directory: Path, name: str) -> dict[str, Any]:
    path = _file(directory, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    return {"name": name, "content": path.read_text(encoding="utf-8")}


def save_converter(directory: Path, name: str, content: Any) -> dict[str, Any]:
    path = _file(directory, name)
    if not isinstance(content, str):
        raise Invalid("`content` is the file's text")
    if name.endswith(".json"):
        try:
            json.loads(content)
        except ValueError as e:
            raise Invalid(f"not JSON: {e}") from e
    directory.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, content)
    return {"name": name, "warnings": check(directory)}


def delete_converter(directory: Path, name: str) -> dict[str, Any]:
    path = _file(directory, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    path.unlink()
    return {"name": name, "warnings": check(directory)}


# ---- FastAPI ----------------------------------------------------------------------------------------------------

def router(data_dir: Path, on_settings: Callable[[], Awaitable[None]]):
    """`/api/rustuya-local/...` for the tab. `on_settings` runs after a settings change (the plugin restarts)."""
    import asyncio

    from fastapi import APIRouter, Body, HTTPException

    r = APIRouter(prefix="/api/rustuya-local")
    conv = data_dir / CONVERTERS_DIR

    async def call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except Invalid as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=f"no such file: {e}") from e

    @r.get("/settings")
    async def get_settings() -> dict[str, Any]:
        return await call(read_settings, data_dir)

    @r.put("/settings")
    async def put_settings(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        saved = await call(save_settings, data_dir, body)
        await on_settings()
        return saved

    @r.get("/converters")
    async def get_converters() -> dict[str, Any]:
        return await call(list_converters, conv)

    @r.get("/converters/{name}")
    async def get_converter(name: str) -> dict[str, Any]:
        return await call(read_converter, conv, name)

    @r.put("/converters/{name}")
    async def put_converter(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        return await call(save_converter, conv, name, body.get("content"))

    @r.delete("/converters/{name}")
    async def del_converter(name: str) -> dict[str, Any]:
        return await call(delete_converter, conv, name)

    return r
