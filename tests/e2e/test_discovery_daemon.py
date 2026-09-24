"""Discovery through the real daemon and a real broker, and the `rustuya-local discovery` commands on what it left."""
import asyncio
import json
import signal
import subprocess
import sys

from devices import lamp
from test_daemon import answer_status

from rustuya_local.discovery import ops
from rustuya_local.discovery.render import Options
from tuya2ildevice.host import MqttTransport

CONFIG_TOPIC = "hadisc/light/dnode/dlamp1_light/config"


def cli(*args, cfg):
    return subprocess.run([sys.executable, "-m", "rustuya_local", "discovery", *args, "--config", str(cfg),
                           "--wait", "0.7", "--backup-dir", str(cfg.parent / "bk")], capture_output=True, text=True)


async def test_the_daemon_publishes_discovery_and_the_commands_manage_it(broker, tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"bridge": {"port": broker, "root": "rd"}, "il": {"port": broker, "prefix": "ild2"},
                               "devices": [lamp("dlamp1")],
                               "discovery": {"prefix": "hadisc", "node_id": "dnode", "relay_prefix": "dr/ha",
                                             "sweep_after": 0.5}}))
    seen = {}
    w = MqttTransport("127.0.0.1", broker, client_id="dwatch")
    await w.connect()
    await w.subscribe("hadisc/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    await w.subscribe("ild2/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    await w.publish("hadisc/switch/dnode/gone_x/config", '{"name": "gone"}', 1, True)      # left from before
    await answer_status(w, "rd", ["dlamp1"])
    proc = subprocess.Popen([sys.executable, "-m", "rustuya_local", "run", "--config", str(cfg)],
                            stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if CONFIG_TOPIC in seen and seen.get("hadisc/switch/dnode/gone_x/config") == "":
                break
            await asyncio.sleep(0.05)
        cfg_doc = json.loads(seen[CONFIG_TOPIC])
        assert cfg_doc["command_topic"] == "dr/ha/dlamp1/light/set"
        assert seen["hadisc/switch/dnode/gone_x/config"] == ""                             # swept

        # a Home Assistant command on the relay reaches IL
        await w.publish("dr/ha/dlamp1/light/set", json.dumps({"state": "ON", "brightness": 30}), 1, False)
        for _ in range(100):
            if seen.get("ild2/dlamp1/brightness/set") == "30":
                break
            await asyncio.sleep(0.05)
        assert seen.get("ild2/dlamp1/switch_led/set") == "true" and seen.get("ild2/dlamp1/brightness/set") == "30"

        r = cli("status", "--detail", cfg=cfg)
        assert r.returncode == 0 and "1 ok, 0 missing, 0 differ, 0 stale" in r.stdout, r.stdout + r.stderr
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(10)
    assert "Traceback" not in proc.stderr.read()

    # the daemon is stopped: clear by hand, then undo it
    assert "dry run" in cli("clear", cfg=cfg).stdout
    r = cli("clear", "--yes", cfg=cfg)
    assert r.returncode == 0 and "backup:" in r.stdout, r.stdout + r.stderr
    await asyncio.sleep(0.3)
    assert seen[CONFIG_TOPIC] == ""
    r = cli("status", cfg=cfg)
    assert r.returncode == 1 and "1 missing" in r.stdout
    r = cli("restore", "--yes", "--no-backup", cfg=cfg)
    await asyncio.sleep(0.3)
    assert json.loads(seen[CONFIG_TOPIC]) == cfg_doc, r.stdout + r.stderr

    for topic in [t for t in seen if t.startswith(("hadisc/", "ild2/", "dr/"))]:
        await w.publish(topic, "", 1, True)
    await asyncio.sleep(0.2)
    await w.close()


def test_status_and_plans_are_pure():
    o = Options(node_id="n")
    desc = {"il": 0, "id": "a", "props": {"x": {"type": "number"}}}
    expected_topic = "homeassistant/sensor/n/a_x/config"
    snap = ops.Snapshot(descriptors={"il/a": json.dumps(desc)},
                        configs={expected_topic: "{}", "homeassistant/switch/n/old/config": "{}"})
    st = ops.status(snap, o)
    assert (st.ok, st.missing, st.differs, st.stale) == ([], [], [expected_topic], ["homeassistant/switch/n/old/config"])
    assert ops.clear_plan(snap, o, stale_only=True) == ["homeassistant/switch/n/old/config"]
    assert ops.restore_plan(snap, {expected_topic: '{"a": 1}'}) == [
        (expected_topic, '{"a": 1}'), ("homeassistant/switch/n/old/config", "")]
