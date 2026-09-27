"""One Manager session at a time (`manager_session`): they share an MQTT client id, and two connections with it knock
each other off the broker. A second session waits for the first to close, or gives up with `Busy`."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from custom_components.rustuya import manager_session


class Manager:
    """Stands in for `rustuya_manager.Manager`: records what is connected at once."""

    open: ClassVar[list[Manager]] = []
    fail_enter: ClassVar[bool] = False

    def __init__(self, **kw) -> None:
        self.kw = kw

    async def __aenter__(self):
        if Manager.fail_enter:
            raise ConnectionRefusedError("no broker")
        Manager.open.append(self)
        assert len(Manager.open) == 1, "two sessions connected at once"
        return self

    async def wait_ready(self) -> None:
        await asyncio.sleep(0)

    async def __aexit__(self, *exc) -> None:
        await asyncio.sleep(0.05)                           # disconnecting takes a moment
        if self in Manager.open:
            Manager.open.remove(self)


@pytest.fixture(autouse=True)
def fake_manager(monkeypatch):
    import rustuya_manager

    Manager.open, Manager.fail_enter = [], False
    monkeypatch.setattr(rustuya_manager, "Manager", Manager)


KW = {"broker": "mqtt://127.0.0.1:1883", "root": "rustuya", "devices_path": "x.json", "username": None,
      "password": None}


async def test_a_second_session_waits_for_the_first_to_close():
    first = await manager_session.open_manager(**KW)
    second = asyncio.ensure_future(manager_session.open_manager(**KW))
    await asyncio.sleep(0.05)
    assert not second.done()                                # waiting, not connected alongside
    closing = asyncio.ensure_future(manager_session.close_manager(first))   # as a flow closed in the UI does
    m = await second
    await closing
    assert Manager.open == [m]
    await manager_session.close_manager(m)
    await manager_session.close_manager(m)                  # a second close does not free someone else's lock
    third = await manager_session.open_manager(**KW)
    await manager_session.close_manager(third)


async def test_a_session_left_open_makes_the_next_one_give_up(monkeypatch):
    monkeypatch.setattr(manager_session, "WAIT", 0.1)
    first = await manager_session.open_manager(**KW)
    with pytest.raises(manager_session.Busy):
        await manager_session.open_manager(**KW)
    await manager_session.close_manager(first)
    await manager_session.close_manager(await manager_session.open_manager(**KW))


async def test_a_failed_connect_frees_the_lock():
    Manager.fail_enter = True
    with pytest.raises(ConnectionRefusedError):
        await manager_session.open_manager(**KW)
    Manager.fail_enter = False
    await manager_session.close_manager(await manager_session.open_manager(**KW))
