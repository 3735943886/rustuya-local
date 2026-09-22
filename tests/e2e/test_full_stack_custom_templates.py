"""The same real-bridge/real-device stack as test_full_stack.py, but with a non-default `mqtt_event_topic`,
`mqtt_command_topic` AND — the case the old hand-rolled `BridgeTopics` could never interpret at all —
a non-default `mqtt_payload_template`. This is the exact scenario that motivated moving bridge-wire parsing onto
`pyrustuyabridge`: a custom payload envelope silently produced near-empty dps maps before.
"""
import json
import signal
import subprocess
import sys
import threading
import uuid

import pytest
from devices import lamp
from test_full_stack import DEV, IP, KEY, Observer, wait

pyrustuyabridge = pytest.importorskip("pyrustuyabridge")
tuyamock = pytest.importorskip("tuyamock")
mqtt = pytest.importorskip("paho.mqtt.client")


def test_a_custom_event_command_and_payload_template_all_work_together(broker, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("custom-templates")
    root, prefix = f"rbx{uuid.uuid4().hex[:6]}", f"ilx{uuid.uuid4().hex[:6]}"
    obs = Observer(broker, [f"{root}/#", f"{prefix}/#"])

    bridge = pyrustuyabridge.PyBridgeServer(
        mqtt_broker=f"mqtt://127.0.0.1:{broker}", mqtt_root_topic=root,
        mqtt_event_topic="{root}/ev/{type}/{id}", mqtt_command_topic="{root}/cmd",
        mqtt_payload_template='{"d":{value}}', mqtt_retain=True, scavenger_timeout_secs=1,
        state_file=str(tmp / "bridge-state.json"), no_signals=True, log_level="warn")
    thread = threading.Thread(target=bridge.start, daemon=True)
    thread.start()
    assert wait(lambda: f"{root}/bridge/config" in obs.last, 20), "the bridge never published its config"
    try:
        mock = tuyamock.MockDevice(local_key=KEY, version="3.4", host=IP, port=6668, gw_id=DEV,
                                   dps={"20": True, "22": 500, "23": 300})
        mock.start()
        try:
            obs.publish(f"{root}/cmd", json.dumps({"action": "add", "id": DEV, "key": KEY, "ip": IP, "version": "3.4"}))
            assert wait(lambda: mock.connected, 30), "the bridge never connected to the mock device"

            devfile = tmp / "tuyadevices.json"
            devfile.write_text(json.dumps([lamp(DEV)]))
            cfg = tmp / "config.json"
            cfg.write_text(json.dumps({"bridge": {"port": broker, "root": root}, "il": {"port": broker, "prefix": prefix},
                                       "devices": "tuyadevices.json", "watch_interval": 0}))
            daemon = subprocess.Popen([sys.executable, "-m", "rustuya_local", "run", "--config", str(cfg)],
                                      stderr=subprocess.PIPE, text=True)
            try:
                assert wait(lambda: obs.last.get(f"{prefix}/{DEV}"), 20), "the daemon never published the descriptor"
                assert wait(lambda: obs.last.get(f"{prefix}/{DEV}/switch_led") == "true"
                            and obs.last.get(f"{prefix}/{DEV}/brightness") == "49", 20), \
                    "state through the custom event/payload templates never reached IL"

                obs.publish(f"{prefix}/{DEV}/brightness/set", "50")
                assert wait(lambda: mock.dps["22"] == 507, 15), \
                    "a write never reached the device through the custom command template"
            finally:
                daemon.send_signal(signal.SIGTERM)
                try:
                    daemon.wait(10)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                log = daemon.stderr.read()
                assert "Traceback" not in log and "ERROR" not in log, log
        finally:
            mock.stop()
    finally:
        bridge.stop()
        thread.join(10)
        obs.close()
