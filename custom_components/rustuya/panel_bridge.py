"""Panel device management through the running service, without another MQTT registry requester."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class PanelBridge:
    def __init__(self, hass: Any, service: Any, devices_path: str) -> None:
        self.hass = hass
        self.client = getattr(service, "bridge_client", None)
        self.path = Path(devices_path)
        self.state = SimpleNamespace(cloud={}, bridge={})

    async def ready(self) -> None:
        if self.client is None:
            raise RuntimeError("Rustuya service is not ready")
        await self.sync()

    async def sync(self):
        # loaded already: setup ran `broker.import_runtime` first
        from rustuya_manager.diff import diff
        from rustuya_manager.models import Device

        def cloud():
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text("utf-8"))
            records = data if isinstance(data, list) else data.values()
            return {d["id"]: Device.from_dict(d) for d in records if "id" in d}

        self.state.cloud = await self.hass.async_add_executor_job(cloud)
        records = await self.client.registry()
        self.state.bridge = {id: Device.from_dict({**record, "id": id}) for id, record in records.items()}
        return diff(self.state.cloud, self.state.bridge)

    async def publish_command(self, action: str, **kw) -> None:
        await self.client.publish_command(action, **kw)

    async def remove_device(self, target_id: str) -> None:
        await self.publish_command("remove", target_id=target_id)

    async def wait_registry_refresh(self) -> None:
        await self.client.registry(refresh=True)

    async def __aexit__(self, *exc) -> None:
        pass
