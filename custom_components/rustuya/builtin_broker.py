"""The MQTT broker this integration runs itself for a user who has none: pymqttd (rumqttd) as a child process, between
the embedded bridge and the service.

A fallback, chosen at setup ("no MQTT broker"): a broker of the user's own stays the normal way. It runs in its own
process, so Home Assistant's event loop being slow never holds up the bridge, and it exits with Home Assistant however
that ends. One per Home Assistant process, started when first needed and kept until Home Assistant closes (or the entry
is deleted): a reload keeps its port and what it retains. It listens on 127.0.0.1 with credentials made up for this
process, since other processes on the host can reach that address too.

If it exits on its own it is started again on the same port with the same credentials, so the bridge's and the
service's MQTT clients reconnect to it as they would to any broker that went away; what it retained is gone, as after a
Home Assistant restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE
from homeassistant.core import Event, HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_KEY = f"{DOMAIN}_builtin_broker"
HOST = "127.0.0.1"
USERNAME = "rustuya"
RESTART_DELAYS = (1, 5, 30)         # seconds before each restart after it exits on its own; then the last, repeatedly


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    username: str
    password: str


@dataclass
class _State:
    password: str = field(default_factory=lambda: secrets.token_hex(24))    # never a leading "-": pymqttd's CLI would take it for a flag
    port: int = 0                           # once started, the one it keeps
    broker: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    watchdog: asyncio.Task | None = None
    unsub_close: Any = None
    stopped: bool = False                   # `async_stop`: the watchdog does not start it again


def _state(hass: HomeAssistant) -> _State:
    return hass.data.setdefault(_KEY, _State())


async def async_ensure(hass: HomeAssistant) -> Endpoint:
    """Where the broker listens, started first if it is not running. `broker.import_runtime` must have run."""
    state = _state(hass)
    async with state.lock:
        if state.broker is None or not state.broker.running:
            await _start(hass, state)
        return Endpoint(HOST, state.port, USERNAME, state.password)


async def _start(hass: HomeAssistant, state: _State) -> None:
    import pymqttd  # loaded already: `broker.import_runtime`

    if state.broker is not None:                    # one that exited: release its pipes
        await state.broker.stop()
    broker = pymqttd.Broker(HOST, state.port, username=USERNAME, password=state.password)
    await broker.start()
    state.broker, state.port = broker, broker.port
    if state.unsub_close is None:
        state.unsub_close = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, _on_close(hass))
    if state.watchdog is None:
        state.watchdog = hass.async_create_background_task(_watch(hass, state), "rustuya built-in MQTT broker")
    _LOGGER.info("started the built-in MQTT broker on %s:%d", HOST, state.port)


async def _watch(hass: HomeAssistant, state: _State) -> None:
    """Start the broker again whenever it exits without being stopped."""
    failures = 0
    while not state.stopped:
        broker = state.broker
        status = await broker.wait()
        if state.stopped:
            return
        if state.broker is not broker:              # `async_ensure` started it again meanwhile
            continue
        delay = RESTART_DELAYS[min(failures, len(RESTART_DELAYS) - 1)]
        _LOGGER.error("the built-in MQTT broker exited (status %s); starting it again in %d s", status, delay)
        await asyncio.sleep(delay)
        async with state.lock:
            if state.stopped or state.broker is not broker:
                continue
            try:
                await _start(hass, state)
                failures = 0
            except Exception:
                failures += 1
                _LOGGER.exception("could not start the built-in MQTT broker again")


def _on_close(hass: HomeAssistant):
    async def close(_event: Event) -> None:
        state = _state(hass)
        state.unsub_close = None                    # a listen_once listener is gone once it fired
        await async_stop(hass)

    return close


async def async_stop(hass: HomeAssistant) -> None:
    """Stop the broker (Home Assistant closing, or the entry deleted); `async_ensure` would start a new one."""
    state = hass.data.pop(_KEY, None)
    if state is None:
        return
    state.stopped = True
    async with state.lock:
        broker, state.broker = state.broker, None
        if state.unsub_close is not None:
            state.unsub_close()
            state.unsub_close = None
        if state.watchdog is not None:
            state.watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.watchdog
            state.watchdog = None
        if broker is not None:
            await broker.stop()
            _LOGGER.info("stopped the built-in MQTT broker")
