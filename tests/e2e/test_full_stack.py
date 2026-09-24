"""The whole chain with nothing faked but the Tuya device itself:

    tuyamock device (real Tuya LAN protocol) <-> rustuya-bridge (the real Rust bridge, in process)
        <-> mosquitto <-> rustuya-local daemon (subprocess) <-> mosquitto (il/...) <-> a plain MQTT observer

Synchronous on purpose: the bridge and the mock run in threads, the daemon in its own process.
Skipped when mosquitto, pyrustuyabridge or tuyamock is missing.
"""
import json
import signal
import subprocess
import sys
import threading
import time
import uuid

import pytest
from devices import lamp

pyrustuyabridge = pytest.importorskip("pyrustuyabridge")
tuyamock = pytest.importorskip("tuyamock")
mqtt = pytest.importorskip("paho.mqtt.client")

KEY = "thisisarealkey00"
DEV = "ebfullstack0000000001"
IP = "127.0.7.21"                      # the whole 127/8 block is loopback on Linux; the bridge reaches the mock by IP:6668


def wait(predicate, timeout=20.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


class Observer:
    """A paho client that remembers the last payload of every topic it sees."""

    def __init__(self, port, filters):
        # `stack`/`many` are module-scoped fixtures that construct this before anything
        # function-scoped (like tests/e2e/conftest.py's `_real_sockets`) has run for that test --
        # see the `broker` fixture there for the full explanation. Enable directly.
        try:
            import pytest_socket
        except ImportError:
            pass
        else:
            pytest_socket.enable_socket()
        self.last: dict[str, str] = {}
        self.log: list[tuple[str, str]] = []
        self._c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"obs-{uuid.uuid4().hex[:6]}")
        ready = threading.Event()
        self._c.on_connect = lambda *a: ready.set()
        self._c.on_message = self._on
        self._c.connect("127.0.0.1", port, 30)
        self._c.loop_start()
        ready.wait(5)
        for f in filters:
            self._c.subscribe(f, 1)

    def _on(self, _c, _u, m):
        payload = m.payload.decode("utf-8", "replace")
        self.last[m.topic] = payload
        self.log.append((m.topic, payload))

    def publish(self, topic, payload, retain=False):
        self._c.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)

    def close(self):
        self._c.loop_stop()
        self._c.disconnect()


@pytest.fixture(scope="module")
def stack(broker, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("stack")
    root, prefix = f"rb{uuid.uuid4().hex[:6]}", f"il{uuid.uuid4().hex[:6]}"
    obs = Observer(broker, [f"{root}/#", f"{prefix}/#"])

    bridge = pyrustuyabridge.PyBridgeServer(
        mqtt_broker=f"mqtt://127.0.0.1:{broker}", mqtt_root_topic=root, mqtt_event_topic="{root}/event/{type}/{id}",
        mqtt_command_topic="{root}/command", mqtt_payload_template="{value}", mqtt_retain=True,
        scavenger_timeout_secs=1, state_file=str(tmp / "bridge-state.json"), no_signals=True, log_level="warn")
    thread = threading.Thread(target=bridge.start, daemon=True)
    thread.start()
    assert wait(lambda: f"{root}/bridge/config" in obs.last, 20), "the bridge never published its config"
    time.sleep(6.0)                                     # the bridge's seed phase on an empty root

    mock = tuyamock.MockDevice(local_key=KEY, version="3.4", host=IP, port=6668, gw_id=DEV,
                               dps={"20": True, "22": 500, "23": 300})
    mock.start()
    obs.publish(f"{root}/command", json.dumps({"action": "add", "id": DEV, "key": KEY, "ip": IP, "version": "3.4"}))
    assert wait(lambda: mock.connected, 30), "the bridge never connected to the mock device"

    devfile = tmp / "tuyadevices.json"
    devfile.write_text(json.dumps([lamp(DEV)]))
    cfg = tmp / "config.json"
    cfg.write_text(json.dumps({"bridge": {"port": broker, "root": root}, "il": {"port": broker, "prefix": prefix},
                               "devices": "tuyadevices.json", "watch_interval": 0.2}))
    daemon = subprocess.Popen([sys.executable, "-m", "rustuya_local", "run", "--config", str(cfg)],
                              stderr=subprocess.PIPE, text=True)
    assert wait(lambda: obs.last.get(f"{prefix}/{DEV}"), 20), "the daemon never published the descriptor"
    st = type("Stack", (), dict(obs=obs, mock=mock, root=root, prefix=prefix, daemon=daemon, bridge=bridge,
                                devfile=devfile))
    yield st
    st.daemon.send_signal(signal.SIGTERM)
    try:
        st.daemon.wait(10)
    except subprocess.TimeoutExpired:
        st.daemon.kill()
    log = st.daemon.stderr.read()
    st.mock.stop()
    bridge.stop()
    thread.join(10)
    obs.close()
    assert "Traceback" not in log and "ERROR" not in log, log       # the daemon logged no exception or error


def il(stack, prop):
    return stack.obs.last.get(f"{stack.prefix}/{DEV}/{prop}")


def test_the_device_appears_as_an_il_light_with_its_real_state(stack):
    desc = json.loads(stack.obs.last[f"{stack.prefix}/{DEV}"])
    assert desc["kind"] == "light" and desc["source"] == "tuya" and desc["props"]["brightness"]["role"] == "brightness"
    assert stack.obs.last[f"{stack.prefix}/_producer/tuya"] == "online"
    assert wait(lambda: il(stack, "available") == "true"), "the device never became available"
    assert wait(lambda: il(stack, "switch_led") == "true" and il(stack, "brightness") == "49"), \
        "\n".join(f"{t}={v[:60]}" for t, v in sorted(stack.obs.last.items()) if 'config' not in t and DEV + '"' not in v[:200])


def test_a_change_made_on_the_device_reaches_il(stack):
    stack.mock.dps["22"] = 1000
    assert stack.mock.push({"22": 1000})
    assert wait(lambda: il(stack, "brightness") == "100"), (il(stack, "brightness"), stack.obs.log[-10:])


def test_a_command_written_to_il_reaches_the_device(stack):
    stack.obs.publish(f"{stack.prefix}/{DEV}/brightness/set", "50")
    assert wait(lambda: stack.mock.dps["22"] == 507, 15), stack.mock.dps
    assert stack.mock.dps["20"] is True                                     # a brightness write also switches the lamp on
    assert wait(lambda: il(stack, "brightness") == "50")                    # and the device's own report comes back


def test_switching_off_and_a_refused_command(stack):
    stack.obs.publish(f"{stack.prefix}/{DEV}/switch_led/set", "false")
    assert wait(lambda: stack.mock.dps["20"] is False, 15)
    assert wait(lambda: il(stack, "switch_led") == "false")
    stack.obs.log.clear()
    stack.obs.publish(f"{stack.prefix}/{DEV}/brightness/set", "500")         # out of range: refused, never sent
    assert wait(lambda: f"{stack.prefix}/{DEV}/reject" in stack.obs.last)
    assert json.loads(stack.obs.last[f"{stack.prefix}/{DEV}/reject"])["code"] == "out_of_range"
    assert stack.mock.dps["22"] == 507


def test_a_device_that_drops_off_the_network_is_unavailable_and_comes_back(stack):
    dps = dict(stack.mock.dps)
    stack.mock.stop()
    assert wait(lambda: il(stack, "available") == "false", 60), (il(stack, "available"), stack.obs.log[-6:])
    dps["22"] = 300
    stack.mock = tuyamock.MockDevice(local_key=KEY, version="3.4", host=IP, port=6668, gw_id=DEV, dps=dps)   # a stopped mock is not restartable
    stack.mock.start()
    assert wait(lambda: il(stack, "available") == "true", 90), "the device did not come back"
    assert wait(lambda: il(stack, "brightness") == "29"), il(stack, "brightness")


def test_a_restarted_daemon_rebuilds_everything_from_the_bridge_alone(stack, tmp_path):
    # forget everything the first daemon left retained, so nothing survives but what the bridge can tell
    stack.daemon.send_signal(signal.SIGTERM)
    stack.daemon.wait(10)
    assert wait(lambda: stack.obs.last.get(f"{stack.prefix}/_producer/tuya") == "offline")
    for topic in [t for t in stack.obs.last if t.startswith(f"{stack.prefix}/") and stack.obs.last[t] != ""]:
        stack.obs.publish(topic, "", retain=True)
    assert wait(lambda: not [t for t, v in stack.obs.last.items() if t.startswith(f"{stack.prefix}/") and v != ""])
    cfg = next(tmp_path.parent.glob("stack*/config.json"))
    stack.daemon = subprocess.Popen([sys.executable, "-m", "rustuya_local", "run", "--config", str(cfg)],
                                    stderr=subprocess.PIPE, text=True)
    assert wait(lambda: il(stack, "available") == "true" and il(stack, "brightness") == "29", 30), \
        {t: v[:40] for t, v in stack.obs.last.items() if t.startswith(stack.prefix)}
    assert stack.obs.last[f"{stack.prefix}/_producer/tuya"] == "online"


def test_the_daemon_survives_all_of_it(stack):
    assert stack.daemon.poll() is None
