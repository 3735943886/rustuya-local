"""Small lifecycle helpers for background tasks and file work."""

import asyncio
import contextlib
from collections.abc import Callable


async def cancel_task(task: asyncio.Task | None) -> None:
    """Cancel an owned task and wait until its cleanup has finished."""
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def run_in_thread[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Finish file work before propagating cancellation; cancelling cannot stop its thread."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Do not release a caller's lock or close its resources while the worker still uses them.
        await asyncio.gather(task, return_exceptions=True)
        raise
