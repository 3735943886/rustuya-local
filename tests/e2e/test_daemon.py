"""`rustuya-local run` as a real process against a real broker."""
import asyncio
import json
import signal
import subprocess
import sys

from devices import lamp

from tuya2ildevice.host import MqttTransport


async def test_the_daemon_publishes_devices_and_goes_offline_on_sigterm(broker, tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"bridge": {"port": broker}, "il": {"port": broker, "prefix": "ild"},
                               "devices": [lamp("daemon1")]}))
    seen = {}
    watcher = MqttTransport("127.0.0.1", broker, client_id="watcher")
    await watcher.connect()
    await watcher.subscribe("ild/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    proc = subprocess.Popen([sys.executable, "-m", "rustuya_local", "run", "--config", str(cfg)],
                            stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if seen.get("ild/_producer/tuya") == "online" and "ild/daemon1" in seen:
                break
            await asyncio.sleep(0.05)
        assert seen["ild/_producer/tuya"] == "online" and json.loads(seen["ild/daemon1"])["kind"] == "light"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 0
        for _ in range(100):
            if seen["ild/_producer/tuya"] == "offline":
                break
            await asyncio.sleep(0.05)
        assert seen["ild/_producer/tuya"] == "offline"
    finally:
        if proc.poll() is None:
            proc.kill()
        cleaner = ["ild/daemon1", "ild/daemon1/available", "ild/daemon1/switch_led", "ild/daemon1/brightness",
                   "ild/daemon1/color_temperature", "ild/_producer/tuya"]
        for t in cleaner:
            await watcher.publish(t, "", 1, True)
        await asyncio.sleep(0.1)
        await watcher.close()


async def test_the_daemon_uses_the_bridges_templates_and_follows_the_device_file(broker, tmp_path):
    devfile = tmp_path / "tuyadevices.json"
    devfile.write_text(json.dumps([lamp("w1")]))
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"bridge": {"port": broker, "root": "rb"}, "il": {"port": broker, "prefix": "ilw"},
                               "devices": "tuyadevices.json", "watch_interval": 0.1}))
    seen = {}
    t = MqttTransport("127.0.0.1", broker, client_id="watcher2")
    await t.connect()
    await t.subscribe("ilw/#", lambda m: seen.__setitem__(m.topic, m.payload.decode()))
    # the bridge's own layout: events under {root}/ev/{type}/{id}
    await t.publish("rb/bridge/config", json.dumps({"mqtt_root_topic": "rb", "mqtt_event_topic": "{root}/ev/{type}/{id}"}), 1, True)
    proc = subprocess.Popen([sys.executable, "-m", "rustuya_local", "run", "--config", str(cfg)], stderr=subprocess.DEVNULL)

    async def until(cond, what):
        for _ in range(100):
            if cond():
                return
            await asyncio.sleep(0.05)
        raise AssertionError(what)

    try:
        await until(lambda: "ilw/w1" in seen, "w1 was not published")
        await t.publish("rb/error/w1", '{"errorCode":0,"errorMsg":"Connection Successful"}', 1, True)
        await t.publish("rb/ev/state/w1", '{"20":true,"22":1000,"23":0}', 1, True)
        await until(lambda: seen.get("ilw/w1/brightness") == "100", "the state on the bridge's own topic was not followed")
        devfile.write_text(json.dumps([lamp("w1"), lamp("w2")]))               # manager adds a device
        await until(lambda: "ilw/w2" in seen, "the new device was not picked up")
        devfile.write_text(json.dumps([lamp("w2")]))                            # and removes one
        await until(lambda: seen.get("ilw/w1") == "" and seen.get("ilw/w1/brightness") == "", "w1 was not removed")
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(10)
        for topic in list(seen):
            await t.publish(topic, "", 1, True)
        for topic in ("rb/bridge/config", "rb/error/w1", "rb/ev/state/w1"):
            await t.publish(topic, "", 1, True)
        await asyncio.sleep(0.1)
        await t.close()
