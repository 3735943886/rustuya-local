"""`Service`, the assembly every shell runs, on in-memory brokers: devices the bridge holds reach IL, a user overrides
directory (JSON rule layers and declarative converters) is applied at start and followed, a failed start releases what it
connected, and stop is idempotent."""
import asyncio
import json

import pytest
from devices import curtain, lamp, status_reply

from rustuya_local.host import SETTINGS_FILE, InProcessTransport
from rustuya_local.service import AnotherProducer, Service, Settings

GLOW = {"props": {"glow": {"type": "text"}}, "on": "bright",
        "update": [{"emit": "glow", "value": {"op": "if", "condition": {"op": "get", "path": "codes.switch_led"},
                                               "then": {"op": "get", "path": "config.on"}, "else": "no"}}]}


class Transports:
    def __init__(self):
        self.bridge, self.il = InProcessTransport(), InProcessTransport()
        self.closed: list[str] = []
        self.wills: list = []
        for name, t in (("bridge", self.bridge), ("il", self.il)):
            t.close = self._closer(name)

    def _closer(self, name):
        async def closed():
            self.closed.append(name)
        return closed

    async def connect_bridge(self):
        return self.bridge

    async def connect_il(self, will):
        self.wills.append(will)
        return self.il


async def _answer_status(bus: InProcessTransport, ids):
    async def reply(msg):
        if json.loads(msg.payload).get("action") == "status":
            await bus.publish("rustuya/response/bridge", status_reply(ids), 1, False)
    await bus.subscribe("rustuya/command", reply)


async def settle(service, t):
    for _ in range(3):
        await service.runner.drain()
        await service.bridge_client.drain()
        await t.bridge.settle()
        await t.il.settle()


def _service(t, tmp_path, **kw):
    settings = Settings(devices=[lamp("lamp1"), lamp("lamp2")], watch_interval=0, **kw)
    return Service(settings, connect_bridge=t.connect_bridge, connect_il=t.connect_il)


async def test_devices_the_bridge_holds_reach_il_and_stop_goes_offline(tmp_path, monkeypatch):
    t = Transports()
    await _answer_status(t.bridge, ["lamp1"])
    service = _service(t, tmp_path)
    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    await service.start()
    await settle(service, t)
    assert t.wills == [("il/_producer/tuya", "offline", 1, True)]
    assert "il/lamp1/descriptor" in t.il.retained and "il/lamp2/descriptor" not in t.il.retained      # lamp2: not on the bridge
    await service.stop()
    await service.stop()                                                          # idempotent
    assert t.il.retained["il/_producer/tuya"].payload == "offline"
    assert t.closed == ["il", "bridge"]
    assert not service.hub._release_rules.alive


async def test_an_overrides_directory_is_applied_and_followed(tmp_path, monkeypatch):
    conv = tmp_path / "converters"
    conv.mkdir()
    (conv / "10.json").write_text(json.dumps({"overrides": {"p": {"converters": {"declarative": GLOW}}}}))
    (conv / "20_model.json").write_text(json.dumps({"overrides": {"lamp1": {"device": {"model": "Desk lamp"}}}}))
    t = Transports()
    await _answer_status(t.bridge, ["lamp1"])
    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    service = _service(t, tmp_path, overrides_path=conv,
                       hub_options={"overrides": {"lamp1": {"device": {"label": "Inline"}}}})
    await service.start()
    await settle(service, t)
    desc = json.loads(t.il.retained["il/lamp1/descriptor"].payload)
    assert "glow" in desc["props"] and desc["model"] == "Desk lamp" and desc["label"] == "Inline"

    await t.bridge.publish("rustuya/error/lamp1", '{"errorCode":0}', 0, True)
    await t.bridge.publish("rustuya/event/state/lamp1", '{"20":true}', 0, True)
    await settle(service, t)
    assert t.il.retained["il/lamp1/glow"].payload == "bright"

    (conv / "10.json").unlink()                                   # followed: the converter goes away
    assert service.override_watcher.check()
    await settle(service, t)
    assert "glow" not in json.loads(t.il.retained["il/lamp1/descriptor"].payload)["props"]
    await service.stop()


async def test_a_cover_setting_written_through_il_is_saved_and_applied(tmp_path, monkeypatch):
    """A cover's direction toggle is an IL property: writing it sends nothing to the device, saves the device's settings
    into the overrides directory and applies them at once. Without an overrides directory none is offered."""
    import asyncio

    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    conv = tmp_path / "conv"
    conv.mkdir()
    t = Transports()
    await _answer_status(t.bridge, ["cur1"])
    sent = []
    await t.bridge.subscribe("rustuya/command", lambda m: sent.append(json.loads(m.payload)))
    service = Service(Settings(devices=[curtain("cur1")], watch_interval=0, overrides_path=conv),
                      connect_bridge=t.connect_bridge, connect_il=t.connect_il)
    await service.start()
    await t.bridge.publish("rustuya/error/cur1", '{"errorCode":0}', 0, True)
    await t.bridge.publish("rustuya/event/state/cur1", '{"3":30}', 0, True)
    await settle(service, t)
    props = json.loads(t.il.retained["il/cur1/descriptor"].payload)["props"]
    assert {"cover_invert_position", "cover_invert_set_position", "cover_state_source"} <= set(props)
    assert "cover_infer_motion" not in props                   # replaced by the state-source selector in 0.3.16
    assert "cover_invert_control" not in props                  # the target position opens and closes it
    assert t.il.retained["il/cur1/position"].payload == "30"

    sent.clear()
    await t.il.publish("il/cur1/cover_invert_position/set", "true", 1)
    saved = conv / SETTINGS_FILE
    for _ in range(100):
        await settle(service, t)
        if saved.is_file():
            break
        await asyncio.sleep(0.02)
    assert json.loads(saved.read_text())["overrides"]["cur1"]["cover"]["invert_position"] is True
    assert not [c for c in sent if c.get("action") == "set"]    # a setting, not a command to the device
    await t.bridge.publish("rustuya/event/state/cur1", '{"3":20}', 0, True)
    await settle(service, t)
    assert t.il.retained["il/cur1/cover_invert_position"].payload == "true"
    assert t.il.retained["il/cur1/position"].payload == "80"
    await service.stop()

    t = Transports()
    await _answer_status(t.bridge, ["cur1"])
    service = Service(Settings(devices=[curtain("cur1")], watch_interval=0), connect_bridge=t.connect_bridge,
                      connect_il=t.connect_il)
    await service.start()
    await settle(service, t)
    assert not [p for p in json.loads(t.il.retained["il/cur1/descriptor"].payload)["props"] if p.startswith("cover_")]
    await service.stop()


async def test_a_failed_start_releases_what_it_connected(tmp_path):
    t = Transports()

    async def broken_il(will):
        raise ConnectionRefusedError("no broker")

    service = Service(Settings(devices=[lamp()], watch_interval=0), connect_bridge=t.connect_bridge,
                      connect_il=broken_il)
    with pytest.raises(ConnectionRefusedError):
        await service.start()
    assert t.closed == ["bridge"]
    assert not service.hub._release_rules.alive
    await service.stop()                                          # nothing to stop: no error


async def test_a_second_producer_is_refused_and_a_stale_presence_is_not(tmp_path, monkeypatch, caplog):
    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    t = Transports()
    await t.il.publish("il/_producer/tuya", "online", 1, True)          # left by one whose Last Will never came
    first = _service(t, tmp_path)
    await first.start()
    assert "no producer answers" in caplog.text

    second = _service(t, tmp_path)
    with pytest.raises(AnotherProducer):
        await second.start()
    assert second.runner is None and t.il.retained["il/_producer/tuya"].payload == "online"
    other = Transports()
    other.il = t.il                                                     # the same IL broker, another source: fine
    third = Service(Settings(devices=[lamp("lamp1")], watch_interval=0, source="tuya2"),
                    connect_bridge=other.connect_bridge, connect_il=other.connect_il)
    await third.start()
    await third.stop()
    await first.stop()


async def test_the_override_pack_is_synced_in_the_background(tmp_path, monkeypatch):
    import hashlib

    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    served, conv = tmp_path / "served", tmp_path / "conv"
    served.mkdir()
    conv.mkdir()
    (conv / '00_pack_old.json').write_text('{}')
    (conv / '00_pack_edited.json').write_text('{"overrides": {}}')
    (conv / '.tuya2ildevice_pack.json').write_text(json.dumps({'files': {
        '00_pack_old.json': hashlib.sha256(b'{}').hexdigest(),
        '00_pack_edited.json': hashlib.sha256(b'{}').hexdigest(),
    }}))
    fix = json.dumps({"overrides": {"lamp1": {"device": {"label": "Packed"}}}})
    (served / "00_pack_lamp.json").write_text(fix)
    (served / "manifest.json").write_text(json.dumps({"version": 1, "files": [
        {"name": "00_pack_lamp.json", "sha256": hashlib.sha256(fix.encode()).hexdigest()}]}))
    t = Transports()
    service = _service(t, tmp_path, overrides_path=conv, pack=True, pack_url=served.as_uri() + "/")
    assert await service.check_pack() == {"available": True}
    assert not (conv / "rules").exists() and service.pack_status is None
    await service.start()
    for _ in range(100):
        if service.pack_status:
            break
        await asyncio.sleep(0.02)
    assert service.pack_status["added"] == ["00_pack_lamp.json"] and (conv / "rules" / "builtin" / "00_pack_lamp.json").is_file()
    assert await service.check_pack() == {"available": False}
    assert not (conv / '00_pack_old.json').exists()
    assert (conv / '00_pack_edited.json').read_text() == '{"overrides": {}}'
    (conv / "rules" / "builtin" / "00_pack_lamp.json").unlink()
    assert await service.check_pack() == {"available": True}
    first, service.pack_status = service.pack_status, None
    assert service.sync_pack_now()                                      # not a day later: now
    for _ in range(100):
        if service.pack_status:
            break
        await asyncio.sleep(0.02)
    assert service.pack_status["added"] == ["00_pack_lamp.json"] and service.pack_status["at"] >= first["at"]
    await service.stop()
    assert not service.sync_pack_now()

    service = _service(t, tmp_path, overrides_path=conv, pack=True, pack_url=(tmp_path / "gone").as_uri() + "/")
    await service.start()                                               # no pack to be had: the service runs anyway
    for _ in range(100):
        if service.pack_status:
            break
        await asyncio.sleep(0.02)
    assert "error" in service.pack_status and (conv / "rules" / "builtin" / "00_pack_lamp.json").is_file()
    await service.stop()


async def test_a_restart_keeps_the_presence_and_every_device_available(tmp_path, monkeypatch):
    """`stop(offline=False)` then `start(resume=True)` (an options change in Home Assistant): the presence never says
    `offline` and no device says `available: false`, so nothing downstream sees them go away and come back."""
    t = Transports()
    await _answer_status(t.bridge, ["lamp1"])
    await t.bridge.publish("rustuya/error/lamp1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 1, True)
    await t.bridge.publish("rustuya/event/state/lamp1", '{"20":true,"22":1000,"23":0}', 1, True)
    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    first = _service(t, tmp_path)
    await first.start()
    await settle(first, t)
    assert t.il.retained["il/lamp1/available"].payload == "true"
    t.il.published.clear()

    await first.stop(offline=False)
    second = _service(t, tmp_path)
    await second.start(resume=True)
    await settle(second, t)
    try:
        assert not [p for p in t.il.published if p[0] == "il/_producer/tuya" and p[1] == "offline"]
        assert [p[1] for p in t.il.published if p[0] == "il/lamp1/available"] in ([], ["true"])
        assert t.il.retained["il/lamp1/brightness"].payload == "100"
    finally:
        await second.stop()
    assert t.il.retained["il/_producer/tuya"].payload == "offline"            # a real stop still goes offline


async def test_restart_removes_only_old_owned_devices():
    t = Transports()
    await t.bridge.publish("rustuya/bridge/config", "{}", 1, True)
    ids = ["lamp1", "lamp2"]
    await _answer_status(t.bridge, ids)
    # Another producer's data must survive reconciliation.
    await t.il.publish("il/other/descriptor", '{"source":"other"}', 1, True)
    first = Service(Settings(devices=[lamp(i) for i in ids]),
                    connect_bridge=t.connect_bridge, connect_il=t.connect_il)
    await first.start()
    await settle(first, t)
    await first.stop()
    # Simulate retained descriptors from a producer using the legacy layout.
    for device_id in ids:
        await t.il.publish(f"il/{device_id}", json.dumps({"id": device_id, "source": "tuya"}), 1, True)
    ids.remove("lamp2")
    second = Service(Settings(devices=[lamp("lamp1"), lamp("lamp2")]),
                     connect_bridge=t.connect_bridge, connect_il=t.connect_il)
    await second.start()
    await settle(second, t)
    assert "il/lamp1/descriptor" in t.il.retained and "il/other/descriptor" in t.il.retained
    assert "il/lamp1" in t.il.retained and "il/lamp2" in t.il.retained
    assert not any(topic == "il/lamp2/descriptor" or topic.startswith("il/lamp2/") for topic in t.il.retained)
    await second.stop()


async def test_stop_cleans_up_even_when_command_drain_fails():
    from unittest.mock import AsyncMock

    t = Transports()
    await t.bridge.publish("rustuya/bridge/config", "{}", 1, True)
    service = Service(Settings(), connect_bridge=t.connect_bridge, connect_il=t.connect_il)
    await service.start()
    await service.bridge_client.drain()
    service.bridge_client.drain = AsyncMock(side_effect=RuntimeError("publish failed"))
    with pytest.raises(RuntimeError, match="publish failed"):
        await service.stop()
    assert t.closed == ["il", "bridge"]
    assert t.il.retained["il/_producer/tuya"].payload == "offline"
    assert not service.hub._release_rules.alive
    await service.stop()


async def test_stop_cleans_up_when_cancelled_while_draining():
    t = Transports()
    await t.bridge.publish("rustuya/bridge/config", "{}", 1, True)
    service = Service(Settings(), connect_bridge=t.connect_bridge, connect_il=t.connect_il)
    await service.start()
    await service.bridge_client.drain()
    draining = asyncio.Event()

    async def blocked():
        draining.set()
        await asyncio.Event().wait()

    service.bridge_client.drain = blocked
    stopping = asyncio.create_task(service.stop())
    await draining.wait()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert t.closed == ["il", "bridge"]
    assert t.il.retained["il/_producer/tuya"].payload == "offline"
    assert not service.hub._release_rules.alive


async def test_stop_waits_for_an_override_pack_write_in_progress(tmp_path, monkeypatch):
    import threading

    from rustuya_local.host import pack

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    path = tmp_path / 'conv' / 'rules' / 'builtin' / '00_pack_test.json'

    def blocked_sync(directory, *, base_url=pack.BASE_URL, manifest=None):
        if manifest is not None:
            return pack.SyncResult()
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise TimeoutError('test did not release pack sync')
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
        return pack.SyncResult(added=[path.name])

    monkeypatch.setattr(pack, 'sync', blocked_sync)
    t = Transports()
    await t.bridge.publish('rustuya/bridge/config', '{}', 1, True)
    service = _service(t, tmp_path, overrides_path=tmp_path / 'conv', pack=True)
    await service.start()
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.05)
        assert not stopping.done() and not t.closed
    finally:
        release.set()
        if stopping is not None:
            await stopping
        else:
            await service.stop()
    assert path.read_text() == '{}' and t.closed == ['il', 'bridge']
    assert not service.sync_pack_now()


async def test_manual_pack_updates_work_without_automatic_updates(tmp_path, monkeypatch):
    from rustuya_local.host import pack

    calls = []

    def sync(directory, *, base_url=pack.BASE_URL, manifest=None):
        if manifest is None:
            calls.append(directory)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / '00_pack_manual.json').write_text('{}')
        return pack.SyncResult()

    monkeypatch.setattr(pack, 'sync', sync)
    t = Transports()
    await t.bridge.publish('rustuya/bridge/config', '{}', 1, True)
    service = _service(t, tmp_path, overrides_path=tmp_path / 'conv', pack=False, pack_interval=0.01)
    assert not service.sync_pack_now()
    await service.start()
    try:
        await asyncio.sleep(0.05)
        assert calls == [] and service.pack_status is None
        for count in (1, 2):
            service.pack_status = None
            assert service.sync_pack_now()
            async with asyncio.timeout(2):
                while service.pack_status is None:
                    await asyncio.sleep(0.01)
            assert len(calls) == count
            assert (tmp_path / 'conv/rules/builtin/00_pack_manual.json').read_text() == '{}'
            await asyncio.sleep(0.05)
            assert len(calls) == count
    finally:
        await service.stop()
    assert not service.sync_pack_now()
