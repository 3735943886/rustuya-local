"""rustuya-local as a rustuya-manager plugin: the same `Service` the daemon and the Home Assistant integration run,
supervised by the manager. It publishes IL; il-ha (or any IL consumer) turns the devices into entities.

Found through the `rustuya_manager.plugins` entry point. It imports nothing from rustuya_manager: `ctx` is used by
its documented contract (api_version >= 4). What it takes from the manager:

- the broker (host, port, credentials, TLS) and the bridge root of the manager's own bridge connection; the service
  opens its own connections with them (the manager's DP bus has no device online/offline, which IL needs);
- the device list: `ctx.devices()` (the cloud records the manager loaded), followed with `ctx.watch_devices`;
- `ctx.data_dir("rustuya-local")`: `settings.json` (optional) and `custom_converters/` (user overrides and code
  converters, followed live).

`settings.json`, every key optional:

    {"il": {"prefix": "il", "source": "tuya"},
     "options": {"allow_hazardous": false, "expose_unused": false, "use_quirks": true}}

The tab shows what the service is doing (the plugin's state namespace) and edits the settings and the
`custom_converters/` files through `/api/rustuya-local/...` (`api.py`). A settings change restarts the service in place;
a settings file the service cannot use is shown on the tab and waited on, not retried.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

NAME = "rustuya-local"
MIN_API = 4

# The manager loads pip-installed plugins by entry point and dropped-in ones by their top-level `register`, and does not
# dedup the two: installed both ways, this is called twice with the same ctx (one module, whichever copy won sys.path)
_REGISTERED: weakref.WeakSet[Any] = weakref.WeakSet()


def load_settings(data_dir: Path, bridge_root: str, devices: list[dict]):
    """The service settings from the plugin's data dir (defaults when `settings.json` is missing)."""
    from ..service import Settings
    from .api import validate_settings

    path = data_dir / "settings.json"
    raw: dict[str, Any] = json.loads(path.read_text()) if path.is_file() else {}
    try:
        checked = validate_settings(raw)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e
    conv = data_dir / "custom_converters"
    conv.mkdir(exist_ok=True)
    return Settings(root=bridge_root, prefix=checked["il"]["prefix"], source=checked["il"]["source"],
                    devices=devices, overrides_path=conv, hub_options=dict(checked["options"]))


def usable(records: dict[str, dict]) -> list[dict]:
    """Cloud records that can be driven (an id and a category), as the device file loader keeps them."""
    return [r for r in records.values() if isinstance(r, dict) and r.get("id") and r.get("category")]


class Plugin:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.namespace = ctx.state_namespace(NAME)
        self.service = None
        self.error: str | None = None
        self._restart = asyncio.Event()

    async def restart(self) -> None:
        """Apply changed settings: the running service stops and starts again with them."""
        self._restart.set()

    def _connect(self, kind: str):
        bc = self.ctx.bridge_client

        async def connect(will=None):
            from tuya2ildevice.host import MqttTransport
            t = MqttTransport(bc.host, bc.port, client_id=f"rustuya-local-{kind}", username=bc.username,
                              password=bc.password, will=will, tls=bool(getattr(bc, "tls", False)))
            await t.connect()
            return t
        return connect

    async def run(self) -> None:
        """The supervised service: started by the manager after bootstrap, cancelled on shutdown. A start that fails
        (the broker is down) raises, for the manager's backoff; settings it cannot use wait for a fix instead."""
        from ..service import Service

        data = self.ctx.data_dir(NAME)
        while True:
            self._restart.clear()
            try:
                settings = await asyncio.to_thread(load_settings, data, self.ctx.bridge_client.root,
                                                   usable(self.ctx.devices()))
            except (OSError, ValueError, TypeError) as e:
                self.error = f"settings: {e}"
                await self.publish_status()
                await self._restart.wait()
                continue
            bridge, il = self._connect("bridge"), self._connect("il")
            self.service = Service(settings, connect_bridge=bridge, connect_il=il)
            self.error = None
            await self.service.start()
            try:
                while not self._restart.is_set():
                    await self.publish_status()
                    try:
                        await asyncio.wait_for(self._restart.wait(), 5)
                    except TimeoutError:
                        pass
            finally:
                service, self.service = self.service, None
                await service.stop()
                await self.publish_status()

    async def on_devices(self, records: dict[str, dict]) -> None:
        if self.service is not None:
            self.service.refresh_devices(usable(records))
            await self.publish_status()

    def status(self) -> dict[str, Any]:
        s = self.service
        if s is None or s.hub is None:
            return {"running": False, "error": self.error}
        hub = s.hub
        devices = []
        for device_id, drv in sorted(hub.drivers.items()):
            desc = drv.descriptor
            devices.append({"id": device_id, "name": desc.get("label") or desc.get("model") or device_id,
                            "kind": desc.get("kind"), "online": bool(drv.linked),
                            "props": sum(1 for p in desc["props"] if p != "available")})
        return {"running": True, "error": self.error, "bridge_root": s.settings.root, "il_prefix": s.settings.prefix,
                "devices": devices}

    async def publish_status(self) -> None:
        await self.namespace.set(self.status())


def register(ctx: Any) -> None:
    if getattr(ctx, "api_version", 0) < MIN_API:
        _LOGGER.warning("%s needs rustuya-manager plugin api_version >= %d; not loaded", NAME, MIN_API)
        return
    if ctx in _REGISTERED:
        _LOGGER.warning("%s is installed twice (pip and the plugin directory); running it once", NAME)
        return
    _REGISTERED.add(ctx)
    plugin = Plugin(ctx)
    ctx.add_service(plugin.run)
    ctx.watch_devices(plugin.on_devices)
    try:
        from .api import router
        ctx.add_api_router(router(ctx.data_dir(NAME), plugin.restart))
    except ImportError:                                   # a manager without its web extra: no tab to edit from
        _LOGGER.info("%s: FastAPI is not installed; the tab shows status only", NAME)
    ctx.add_page(NAME, "Tuya (IL)", static_dir=str(Path(__file__).parent / "static"))
