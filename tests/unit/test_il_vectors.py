"""The host wire mapping follows ildevice conformance vectors (../ildevice/vectors, or $ILDEVICE): payloads on the
wire and topics. (Command checks and descriptor schemas are exercised by tuya2ildevice.)"""
import json
import os
from pathlib import Path

import pytest
from tuya2ildevice import Rejected, check_command

from rustuya_local.host import Hub, IlTopics
from rustuya_local.host.il import decode_write, encode_value

ROOT = Path(os.environ.get("ILDEVICE", Path(__file__).resolve().parents[3] / "ildevice"))
VECTORS = ROOT / "vectors"
pytestmark = pytest.mark.skipif(not VECTORS.is_dir(), reason=f"no ildevice checkout at {ROOT} (set ILDEVICE)")


def load(name):
    return json.loads((VECTORS / name).read_text()) if VECTORS.is_dir() else {}


@pytest.mark.parametrize("case", load("wire-values.json").get("state_encode", []), ids=lambda c: f"{c['type']}:{c['value']!r}")
def test_published_values_are_encoded_as_the_vectors_say(case):
    assert encode_value(case["value"]) == case["payload"]


@pytest.mark.parametrize("case", load("wire-values.json").get("write_decode", []), ids=lambda c: f"{c['type']}:{c['payload']!r}")
def test_written_payloads_are_taken_as_the_vectors_say(case):
    """The Hub decodes by type, then the driver's check canonicalises (on/off/1/0 for a binary)."""
    desc = {"il": 0, "id": "d", "props": {"p": {"type": case["type"], "rw": case["type"] != "trigger"}}}
    if case["type"] == "number":
        desc["props"]["p"].update(min=0, max=100)
    if case["type"] == "select":
        desc["props"]["p"]["options"] = ["jet", "eco"]
    try:
        got = check_command(desc, {}, "p", decode_write(case["type"], case["payload"]))
    except Rejected as e:                       # the vector says what value the payload means, not that it is in range
        pytest.fail(f"refused: {e.code}")
    assert got == case["value"]


@pytest.mark.parametrize("case", load("topics.json").get("layouts", []), ids=lambda c: c["name"])
def test_default_topics_follow_the_vectors(case):
    """This package only publishes to the default layout (M-7); the x-mqtt cases are a consumer's business."""
    if "x-mqtt" in json.dumps(case["descriptor"]):
        pytest.skip("x-mqtt is added by a host, not by the driver (M-5)")
    prefix = case["il_prefix"]
    t = IlTopics(prefix, "tuya")
    assert {p: t.state("dev1", p) for p in case["state"]} == case["state"]
    assert t.reject("dev1") == case["reject"]
    for prop, topic in case["set"].items():
        assert t.parse_set(topic) == (case["descriptor"]["id"], prop)


@pytest.mark.parametrize("case", load("topics.json").get("presence", []), ids=lambda c: c["topic"])
def test_presence_topic_follows_the_vectors(case):
    assert IlTopics(case["il_prefix"], case["source"]).presence == case["topic"]


def _hub_for(dev_id):
    from tests.unit.tuya_samples import light
    d = light()
    d["id"] = dev_id
    return Hub([d])


@pytest.mark.parametrize("dev_id", load("topics.json").get("device_ids", {}).get("valid", []))
def test_valid_device_ids_are_accepted(dev_id):
    assert dev_id in _hub_for(dev_id).drivers


@pytest.mark.parametrize("dev_id", load("topics.json").get("device_ids", {}).get("invalid", []))
def test_invalid_device_ids_are_refused(dev_id):
    with pytest.raises(ValueError):
        _hub_for(dev_id)
