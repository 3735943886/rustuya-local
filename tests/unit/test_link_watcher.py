"""LinkWatcher: each device's connection from the bridge's messages, on topics resolved from its config."""
import json

from rustuya_local.bridge_client import LinkWatcher, error_text
from rustuya_local.host.memory import InProcessTransport


async def test_links_from_the_default_layout():
    t = InProcessTransport()
    await t.publish("rustuya/error/lamp", json.dumps({"errorCode": 0, "errorMsg": "Connection Successful"}), retain=True)
    await t.publish("rustuya/error/fan", json.dumps({"errorCode": 906, "errorMsg": "Offline", "reason": "ip_mismatch",
                                                     "configured": "10.0.0.1"}), retain=True)
    changes = []
    w = LinkWatcher(t, "rustuya", lambda i, link: changes.append((i, link["online"])))
    await w.start(timeout=0.05)                              # no bridge/config: the default layout
    await t.settle()
    assert w.links["lamp"] == {"online": True, "code": 0, "message": ""}
    assert w.links["fan"] == {"online": False, "code": 906, "message": "Offline (reason=ip_mismatch, configured=10.0.0.1)"}

    await t.publish("rustuya/error/lamp", json.dumps({"errorCode": 905, "errorMsg": "Timeout"}))
    await t.publish("rustuya/event/state/fan", json.dumps({"1": True}))          # replayed data must not override the explicit offline error
    await t.publish("rustuya/error/bridge", json.dumps({"errorCode": 1}))       # the bridge itself: not a device
    await t.settle()
    assert w.links["lamp"]["online"] is False and w.links["fan"]["online"] is False and "bridge" not in w.links
    assert changes[-1:] == [("lamp", False)]

    w.stop()
    await t.publish("rustuya/error/lamp", json.dumps({"errorCode": 0}))
    await t.settle()
    assert w.links["lamp"]["online"] is False                                    # stopped: no longer listening


async def test_the_bridges_own_topic_layout():
    t = InProcessTransport()
    await t.publish("rustuya/bridge/config", json.dumps({"mqtt_message_topic": "{root}/msg/{id}/{level}"}), retain=True)
    await t.publish("rustuya/msg/lamp/error", json.dumps({"errorCode": 0}), retain=True)
    await t.publish("rustuya/error/stale", json.dumps({"errorCode": 0}), retain=True)       # the default layout's topic
    w = LinkWatcher(t, "rustuya")
    await w.start(timeout=1)
    await t.settle()
    assert set(w.links) == {"lamp"}


def test_error_text():
    assert error_text({"errorCode": 1, "errorMsg": "Offline"}) == "Offline"
    assert error_text({"errorCode": 1, "reason": "x", "detail": {"a": 1}}) == "reason=x"


async def test_restart_snapshot_cannot_override_offline_but_connection_success_can():
    t = InProcessTransport()
    await t.publish('rustuya/bridge/config', '{}', retain=True)
    await t.publish('rustuya/error/lamp', '{"errorCode":905,"errorMsg":"Timeout"}', retain=True)
    await t.publish('rustuya/event/state/unknown', '{"1":true}', retain=True)
    w = LinkWatcher(t, 'rustuya')
    await w.start()
    await t.settle()
    assert 'unknown' not in w.links
    await t.publish('rustuya/event/state/lamp', '{"1":true}')
    await t.publish('rustuya/event/active/lamp', '{"1":true}')
    await t.settle()
    assert not w.links['lamp']['online']
    await t.publish('rustuya/error/lamp', '{"errorCode":0}')
    await t.settle()
    assert w.links['lamp']['online']
    await t.publish('rustuya/event/active/unknown', '{"1":true}')
    await t.settle()
    assert w.links['unknown']['online']
    w.stop()
