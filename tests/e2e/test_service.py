"""`Service`, the assembly every shell runs, on in-memory brokers: devices the bridge holds reach IL, a user overrides
directory (JSON, a v1 file and a code converter) is applied at start and followed, a failed start releases what it
connected, and stop is idempotent."""
import json

import pytest
from devices import lamp, status_reply
from rustuya_local.service import Service, Settings
from tuya2ildevice.host import InProcessTransport

CONVERTER_PY = """
from tuya2ildevice import Converter

class Glow(Converter):
    def __init__(self, cfg):
        self.on = cfg.get("on", "yes")

    def props(self):
        return {"glow": {"type": "text"}}

    def update(self, now, codes, changed, active):
        return {"glow": self.on if codes.get("switch_led") else "no"}

CONVERTERS = {"glow": Glow}
"""


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
    await bus.subscribe("rustuya/command", lambda m: __import__("asyncio").ensure_future(reply(m)))


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
    assert "il/lamp1" in t.il.retained and "il/lamp2" not in t.il.retained      # lamp2: not on the bridge
    await service.stop()
    await service.stop()                                                          # idempotent
    assert t.il.retained["il/_producer/tuya"].payload == "offline"
    assert t.closed == ["il", "bridge"]


async def test_an_overrides_directory_is_applied_and_followed(tmp_path, monkeypatch):
    conv = tmp_path / "converters"
    conv.mkdir()
    (conv / "glow.py").write_text(CONVERTER_PY)
    (conv / "10.json").write_text(json.dumps({"p": {"converters": {"glow": {"on": "bright"}}}}))
    (conv / "20_v1.json").write_text(json.dumps({"lamp1": {"model": "Desk lamp"}}))       # rustuya-homeassistant v1
    t = Transports()
    await _answer_status(t.bridge, ["lamp1"])
    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    service = _service(t, tmp_path, overrides_path=conv,
                       hub_options={"overrides": {"lamp1": {"device": {"label": "Inline"}}}})
    await service.start()
    await settle(service, t)
    desc = json.loads(t.il.retained["il/lamp1"].payload)
    assert "glow" in desc["props"] and desc["model"] == "Desk lamp" and desc["label"] == "Inline"

    await t.bridge.publish("rustuya/error/lamp1", '{"errorCode":0}', 0, True)
    await t.bridge.publish("rustuya/event/state/lamp1", '{"20":true}', 0, True)
    await settle(service, t)
    assert t.il.retained["il/lamp1/glow"].payload == "bright"

    (conv / "10.json").unlink()                                   # followed: the converter goes away
    assert service.override_watcher.check()
    await settle(service, t)
    assert "glow" not in json.loads(t.il.retained["il/lamp1"].payload)["props"]
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
    await service.stop()                                          # nothing to stop: no error


async def test_discovery_runs_with_the_service_and_sweeps(tmp_path, monkeypatch):
    from rustuya_local.service import Discovery
    t = Transports()
    await t.il.publish("homeassistant/switch/rustuya_local/old_x/config", '{"name": "old"}', 1, True)
    await _answer_status(t.bridge, ["lamp1"])
    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    service = _service(t, tmp_path, discovery=Discovery(sweep_after=-1))
    await service.start()
    await settle(service, t)
    await service.discovery.drain()
    assert "homeassistant/light/rustuya_local/lamp1_light/config" in t.il.retained
    cfg = json.loads(t.il.retained["homeassistant/light/rustuya_local/lamp1_light/config"].payload)
    assert cfg["availability"][0]["topic"] == "il/_producer/tuya"
    assert service.discovery.sweep() == ["homeassistant/switch/rustuya_local/old_x/config"]
    await service.stop()


async def test_another_producer_online_is_warned_about(tmp_path, monkeypatch, caplog):
    t = Transports()
    await t.il.publish("il/_producer/tuya", "online", 1, True)
    import rustuya_local.bridge_client as bc
    orig = bc.BridgeClient.start
    monkeypatch.setattr(bc.BridgeClient, "start", lambda self, timeout=0.05: orig(self, timeout))
    service = _service(t, tmp_path)
    await service.start()
    assert "already online" in caplog.text
    await service.stop()
