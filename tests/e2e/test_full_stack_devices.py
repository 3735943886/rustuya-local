"""Differential test over the real bridge: many real device shapes (Home Assistant core's tuya fixtures) each get a
tuyamock device with the fixture's dps, go through the real rustuya-bridge and the rustuya-local daemon, and the IL values
that come out must equal what tuya2ildevice produces when it is fed the same dps directly.

What this catches that the in-process tests cannot: how the bridge serialises each Tuya type on the wire (booleans,
scaled integers, enums, strings, raw/base64, json), and anything the daemon does to a message on the way through.
"""
import collections
import json
import pathlib
import signal
import subprocess
import sys
import threading
import time
import uuid

import pytest

pyrustuyabridge = pytest.importorskip("pyrustuyabridge")
tuyamock = pytest.importorskip("tuyamock")

GOLDEN = pathlib.Path(__file__).resolve().parents[3] / "tuya2ildevice/tests/golden"      # the sibling checkout
sys.path.insert(0, str(GOLDEN))
import fixtures
from devices import fixture_record as record
from test_full_stack import Observer, wait
from tuya2ildevice import Connected, Message, TuyaDriver, Value
from tuya2ildevice.mqtt import encode_value

KEY = "thisisarealkey00"
def pick(per_platform=4, cap=60):
    gold = json.loads((GOLDEN / "golden.json").read_text())
    chosen, seen = [], collections.Counter()
    for code in fixtures.all_codes():
        platforms = set(gold.get(code, {}))
        d = fixtures.load(code)
        if not platforms or not d["status"]:
            continue
        if any(seen[p] < per_platform for p in platforms):
            chosen.append(code)
            seen.update(platforms)
        if len(chosen) >= cap:
            break
    return chosen


def expected(rec, dps):
    """What the daemon should publish, computed with the driver directly."""
    drv = TuyaDriver(rec)
    drv.handle(0, Connected())
    outs = drv.handle(1, Message("state", dps))
    return {o.prop: encode_value(o.value) for o in outs if isinstance(o, Value)}


@pytest.fixture(scope="module")
def many(broker, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("many")
    root, prefix = f"rm{uuid.uuid4().hex[:6]}", f"il{uuid.uuid4().hex[:6]}"
    obs = Observer(broker, [f"{root}/bridge/config", f"{prefix}/#"])
    bridge = pyrustuyabridge.PyBridgeServer(
        mqtt_broker=f"mqtt://127.0.0.1:{broker}", mqtt_root_topic=root, mqtt_event_topic="{root}/event/{type}/{id}",
        mqtt_command_topic="{root}/command", mqtt_payload_template="{value}", mqtt_retain=True,
        scavenger_timeout_secs=1, state_file=str(tmp / "s.json"), no_signals=True, log_level="warn")
    thread = threading.Thread(target=bridge.start, daemon=True)
    thread.start()
    assert wait(lambda: f"{root}/bridge/config" in obs.last, 20)
    time.sleep(6.0)

    cases = []
    for n, code in enumerate(pick()):
        dev_id = f"ebdiff{n:03d}00000000000"
        rec, dps = record(code, dev_id)
        ip = f"127.0.9.{n + 2}"
        mock = tuyamock.MockDevice(local_key=KEY, version="3.4", host=ip, port=6668, gw_id=dev_id, dps=dps)
        mock.start()
        obs.publish(f"{root}/command", json.dumps({"action": "add", "id": dev_id, "key": KEY, "ip": ip, "version": "3.4"}))
        cases.append({"code": code, "id": dev_id, "rec": rec, "dps": dps, "mock": mock, "want": expected(rec, dps)})
    assert wait(lambda: all(c["mock"].connected for c in cases), 60), [c["code"] for c in cases if not c["mock"].connected]

    devfile = tmp / "tuyadevices.json"
    devfile.write_text(json.dumps([c["rec"] for c in cases]))
    cfg = tmp / "config.json"
    cfg.write_text(json.dumps({"bridge": {"port": broker, "root": root}, "il": {"port": broker, "prefix": prefix},
                               "devices": "tuyadevices.json", "watch_interval": 0}))
    daemon = subprocess.Popen([sys.executable, "-m", "rustuya_local", "run", "--config", str(cfg)],
                              stderr=subprocess.PIPE, text=True)
    yield type("Many", (), {"obs": obs, "cases": cases, "prefix": prefix, "daemon": daemon})
    daemon.send_signal(signal.SIGTERM)
    try:
        daemon.wait(10)
    except subprocess.TimeoutExpired:
        daemon.kill()
    log = daemon.stderr.read()
    for c in cases:
        c["mock"].stop()
    bridge.stop()
    thread.join(10)
    obs.close()
    assert "Traceback" not in log and "ERROR" not in log, log       # the daemon logged no exception or error


def observed(many, case):
    base = f"{many.prefix}/{case['id']}/"
    return {t[len(base):]: v for t, v in many.obs.last.items() if t.startswith(base)}


def test_every_device_is_published_with_the_values_the_driver_computes(many):
    assert len(many.cases) >= 30, "too few fixtures were picked to mean anything"
    assert sum(len(c["want"]) for c in many.cases) >= 200 and all(c["want"] for c in many.cases), \
        "the expected values are too thin for a match to mean anything"
    pending = {c["id"]: c for c in many.cases}

    def all_match():
        for c in list(pending.values()):
            got = observed(many, c)
            if got.get("available") == "true" and all(got.get(p) == v for p, v in c["want"].items()):
                del pending[c["id"]]
        return not pending

    wait(all_match, 60)
    report = []
    for c in pending.values():
        got = observed(many, c)
        bad = {p: (v, got.get(p)) for p, v in c["want"].items() if got.get(p) != v}
        report.append(f"{c['code']}: available={got.get('available')} differences={dict(list(bad.items())[:4])}")
    assert not pending, "\n" + "\n".join(report)


def test_the_daemon_is_still_running_and_quiet(many):
    assert many.daemon.poll() is None
