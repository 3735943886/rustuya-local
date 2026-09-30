"""A short-lived `rustuya_manager.Manager` session for the config/options flow: the Tuya Cloud QR wizard, and
registering/removing devices on the bridge. Opened for the few seconds a flow step needs it, closed right after
(`architecture_device_management_via_manager`: the always-on connection is `MqttTransport`/`Runner`, not this).

Importing `rustuya_manager` here, not at module load of `config_flow.py`, keeps a broken/absent install from
breaking config flow discovery; the flow surfaces a clear abort instead (`_open`'s caller checks `available()`).

One session at a time: they share one MQTT client id, and a second connection with it makes the broker drop the first,
so the two would knock each other off in a reconnect loop. A session holds a lock from `open_manager` to
`close_manager`; the next one waits for it (a flow closed in the UI releases it once its Manager has disconnected), and
gives up with `Busy` after `WAIT` seconds, while another flow still has its session open.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

_LOGGER = logging.getLogger(__name__)
WAIT = 15.0
_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()
_HOLDERS: dict[Any, asyncio.Lock] = {}


class Busy(Exception):
    """Another config or options flow has a Manager session open."""


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _LOCKS:
        _LOCKS[loop] = asyncio.Lock()
    return _LOCKS[loop]


def available() -> bool:
    try:
        import rustuya_manager  # noqa: F401
    except ImportError:
        return False
    return True


async def open_manager(*, broker: str, root: str, devices_path: str, username: str | None, password: str | None,
                       bridge_state: str | None = None, bridge_log_level: str | None = None):
    """`bridge_state`: run a bridge for the session (rustuya-manager's embedded one, on that state file) when none is
    on `root` — the embedded mode before its entry runs one (setup, or an entry that failed to start), so what the
    session registers reaches a bridge and is in the state file the entry's bridge starts from."""
    from rustuya_manager import Manager

    lock = _lock()
    try:
        await asyncio.wait_for(lock.acquire(), WAIT)
    except TimeoutError as e:
        raise Busy from e
    try:
        manager = Manager(cloud_path=devices_path, broker=broker, root=root, client_id="rustuya-config",
                          mqtt_user=username, mqtt_pass=password, embed_bridge=bridge_state is not None,
                          bridge_state=bridge_state, log_level=bridge_log_level)
        await manager.__aenter__()
    except BaseException:
        lock.release()
        raise
    _HOLDERS[manager] = lock
    try:
        # `sync()`'s diff is only meaningful once the bridge's device list has arrived; without this every cloud
        # device looks "missing" (an empty bridge side), which is how a whole device list ended up offered for
        # registration.
        await manager.wait_ready()
    except BaseException:
        await close_manager(manager)
        raise
    return manager


async def open_for_entry(hass: Any, data: dict[str, Any], *, run_bridge: bool = False):
    """`open_manager` for an entry's `data`. `run_bridge`: with the embedded bridge, the session runs it (on the entry's
    state file) — for when the entry does not (setup, or an entry that failed to start)."""
    import os

    from . import broker
    from .const import (
        BRIDGE_EMBEDDED,
        CONF_BRIDGE_LOG_LEVEL,
        CONF_BRIDGE_MODE,
        CONF_BRIDGE_ROOT,
        CONF_BRIDGE_STATE_FILE,
        CONF_DEVICES_PATH,
        DEFAULT_BRIDGE_LOG_LEVEL,
        DEFAULT_BRIDGE_STATE_FILE,
    )

    # the device file's directory: the Tuya login (`tuyacreds.json`) is saved beside it by a library that does not
    # create it, and fails (only logging it) when it is missing — then every login asks again
    dirs = [os.path.dirname(data[CONF_DEVICES_PATH])]
    bridge: dict[str, Any] = {}
    if run_bridge and data.get(CONF_BRIDGE_MODE) == BRIDGE_EMBEDDED:
        state_file = data.get(CONF_BRIDGE_STATE_FILE) or hass.config.path(DEFAULT_BRIDGE_STATE_FILE)
        dirs.append(os.path.dirname(state_file))
        bridge = {"bridge_state": state_file,
                  "bridge_log_level": data.get(CONF_BRIDGE_LOG_LEVEL, DEFAULT_BRIDGE_LOG_LEVEL)}
    await hass.async_add_executor_job(lambda: [os.makedirs(d, exist_ok=True) for d in dirs if d])
    username, password = broker.credentials(data)
    return await open_manager(broker=broker.url(data), root=data[CONF_BRIDGE_ROOT],
                              devices_path=data[CONF_DEVICES_PATH], username=username, password=password, **bridge)


async def saved_user_code(hass: Any, data: dict[str, Any]) -> str | None:
    """The user code of the saved Tuya login (`tuyacreds.json` beside the device file, where a session's wizard keeps
    it), read without a session: no broker connection and no lock, so the panel can fill it in before a fetch starts."""
    import os

    from .const import CONF_DEVICES_PATH

    if not available():
        return None

    def wizard_class() -> Any:                      # the import pulls in tuyawizard and qrcode: not on the event loop
        from rustuya_manager.wizard import WizardManager

        return WizardManager

    creds_path = os.path.join(os.path.dirname(data[CONF_DEVICES_PATH]), "tuyacreds.json")
    return await (await hass.async_add_executor_job(wizard_class))(creds_path=creds_path).read_saved_user_code()


async def close_manager(manager: Any) -> None:
    if manager is None:
        return
    try:
        # bounded: a close that hangs would otherwise hold the lock, and every later flow would give up as busy
        await asyncio.wait_for(manager.__aexit__(None, None, None), WAIT)
    except TimeoutError:
        _LOGGER.warning("the rustuya-manager session did not close within %.0f s; released anyway", WAIT)
    finally:
        lock = _HOLDERS.pop(manager, None)          # released once per session, by the session that took it
        if lock is not None:
            lock.release()
