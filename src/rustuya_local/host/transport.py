"""What the host needs from a message transport: subscribe to a topic filter, publish a message.

A structural type: anything with these two methods works (paho below, the in-process broker in `memory.py`, a test
double). Nothing here knows about Tuya or IL.
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes | str
    retain: bool = False
    """True for a retained message replayed on subscribe, False for one published live."""


Callback = Callable[[Message], "Awaitable[None] | None"]
Unsubscribe = Callable[[], None]


def deliver(callback: Callback, msg: Message, tasks: set[asyncio.Task]) -> None:
    """Call `callback(msg)`; an awaitable it returns runs as a task, held in `tasks` until done (the loop keeps only a
    weak reference to a task)."""
    result = callback(msg)
    if inspect.isawaitable(result):
        task = asyncio.ensure_future(result)
        tasks.add(task)
        task.add_done_callback(tasks.discard)


class Transport(Protocol):
    async def subscribe(self, topic: str, callback: Callback, qos: int = 1) -> Unsubscribe: ...

    async def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None: ...
