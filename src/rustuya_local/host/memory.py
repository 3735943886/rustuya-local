"""An in-process broker with MQTT's topic filters and retained messages, for tests and for hosts that keep both sides in
one process. `+` and `#` filters; a retained message is replayed to a new subscriber with `retain=True`, a live one is
delivered with `retain=False`; an empty retained payload clears the topic."""
from __future__ import annotations

import asyncio

from .transport import Callback, Message, Unsubscribe, deliver


def matches(filter_: str, topic: str) -> bool:
    f, t = filter_.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


class InProcessTransport:
    def __init__(self) -> None:
        self._subs: list[tuple[str, Callback]] = []
        self.retained: dict[str, Message] = {}
        self.published: list[tuple[str, str | bytes, int, bool]] = []
        """Every publish, in order."""
        self._tasks: set[asyncio.Task] = set()

    async def subscribe(self, topic: str, callback: Callback, qos: int = 1) -> Unsubscribe:
        entry = (topic, callback)
        self._subs.append(entry)
        for msg in [m for t, m in self.retained.items() if matches(topic, t)]:
            deliver(callback, msg, self._tasks)

        def unsubscribe() -> None:
            if entry in self._subs:
                self._subs.remove(entry)

        return unsubscribe

    async def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        self.published.append((topic, payload, qos, retain))
        if retain:
            if payload in ("", b""):
                self.retained.pop(topic, None)
            else:
                self.retained[topic] = Message(topic, payload, True)
        live = Message(topic, payload, False)
        for filter_, callback in list(self._subs):
            if matches(filter_, topic):
                deliver(callback, live, self._tasks)

    async def settle(self) -> None:
        """Wait for the asynchronous callbacks started so far."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks))
            await asyncio.sleep(0)               # let the done callbacks empty the set
