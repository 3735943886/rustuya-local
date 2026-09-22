"""A short-lived `rustuya_manager.Manager` session for the config/options flow: the Tuya Cloud QR wizard, and
registering/removing devices on the bridge. Opened for the few seconds a flow step needs it, closed right after
(`architecture_device_management_via_manager`: the always-on connection is `MqttTransport`/`Runner`, not this).

Importing `rustuya_manager` here, not at module load of `config_flow.py`, keeps a broken/absent install from
breaking config flow discovery; the flow surfaces a clear abort instead (`_open`'s caller checks `available()`).
"""

from __future__ import annotations

from typing import Any


def available() -> bool:
    try:
        import rustuya_manager  # noqa: F401
    except ImportError:
        return False
    return True


async def open_manager(*, broker: str, root: str, devices_path: str, username: str | None, password: str | None):
    from rustuya_manager import Manager

    manager = Manager(cloud_path=devices_path, broker=broker, root=root, client_id="rustuya-config",
                      mqtt_user=username, mqtt_pass=password)
    await manager.__aenter__()
    return manager


async def close_manager(manager: Any) -> None:
    if manager is not None:
        await manager.__aexit__(None, None, None)
