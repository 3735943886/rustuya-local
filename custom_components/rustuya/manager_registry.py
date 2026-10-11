"""Per-session compatibility guard for manager versions accepting incomplete status pages.

The response topic is broadcast: the service and a short-lived Manager can receive each other's
pages. Only a contiguous snapshot starting at zero may become the panel/config flow's registry.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any


async def ready_registry(manager: Any, timeout: float) -> None:
    client = getattr(manager, "client", None)
    if client is None or not hasattr(client, "_handle_status_page"):
        await manager.wait_ready()
        return

    request = client._request_status
    handle = client._handle_status_page
    offset = 0
    total = None
    ids: set[str] = set()
    complete = False

    async def request_status() -> None:
        nonlocal offset, total, complete
        if (not client._status_active or client._status_started is None
                or time.monotonic() - client._status_started >= client._STATUS_CYCLE_TIMEOUT):
            offset, total, complete = 0, None, False
            ids.clear()
            client._status_accum = None
        await request()

    async def handle_page(parsed: dict[str, Any]) -> None:
        nonlocal offset, total, complete
        if not client._status_active:
            return
        devices = parsed.get("devices")
        start = parsed.get("offset", 0)
        returned = parsed.get("returned", len(devices) if isinstance(devices, dict) else 0)
        count = parsed.get("device_count")
        more = parsed.get("has_more", False)
        if (not isinstance(devices, dict) or type(start) is not int or start != offset
                or type(returned) is not int or returned != len(devices)
                or type(more) is not bool or (more and not returned)
                or ids.intersection(devices)):
            return
        if count is not None and (type(count) is not int or count < start + returned
                                  or more != (start + returned < count)):
            return
        if offset and count != total:
            return
        total = count
        offset += returned
        ids.update(devices)
        complete = not more
        await handle(parsed)

    client._request_status = request_status
    client._handle_status_page = handle_page
    await client.wait_bootstrap(timeout=timeout)
    if not client._bootstrap_done.is_set():
        raise TimeoutError("bridge subscriptions are not ready")
    # Bootstrap may have received pages before the guard was installed. Start a fresh
    # offset-zero cycle after subscription readiness instead of trusting that snapshot.
    client._status_started = None
    await client._request_status()
    await asyncio.wait_for(client._status_idle.wait(), timeout)
    if not complete:
        raise TimeoutError("incomplete bridge registry")
