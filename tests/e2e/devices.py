"""Small Tuya device records (as in tuyadevices.json plus the cloud schema) for the end-to-end tests."""
import json
import pathlib
import sys


def fn(code, typ, **values):
    return {"code": code, "type": typ, "values": json.dumps(values) if values else "{}"}


def strat(pairs):
    return {dpid: {"value_convert": "default", "status_code": code,
                   "config_item": {"statusFormat": {code: "$"}, "valueType": typ, "valueDesc": {}, "enumMappingMap": {}}}
            for dpid, (code, typ) in pairs.items()}


def lamp(dev_id="lamp1"):
    ints = dict(unit="", min=10, max=1000, scale=0, step=1)
    f = {"switch_led": fn("switch_led", "Boolean"), "bright_value_v2": fn("bright_value_v2", "Integer", **ints),
         "temp_value_v2": fn("temp_value_v2", "Integer", **ints)}
    return {"id": dev_id, "category": "dj", "product_id": "p", "name": "Lamp", "product_name": "Bulb",
            "function": f, "status_range": f, "status": {},
            "local_strategy": strat({"20": ("switch_led", "Boolean"), "22": ("bright_value_v2", "Integer"),
                                     "23": ("temp_value_v2", "Integer")})}


def curtain(dev_id="cur1"):
    pct = dict(unit="%", min=0, max=100, scale=0, step=1)
    f = {"control": fn("control", "Enum", range=["open", "stop", "close"]),
         "percent_control": fn("percent_control", "Integer", **pct)}
    return {"id": dev_id, "category": "cl", "product_id": "pc", "name": "Curtain", "product_name": "Curtain",
            "function": f, "status_range": {**f, "percent_state": fn("percent_state", "Integer", **pct)}, "status": {},
            "local_strategy": strat({"1": ("control", "Enum"), "2": ("percent_control", "Integer"),
                                     "3": ("percent_state", "Integer")})}


def fan_levels(dev_id="fan1"):
    """A fan whose speed is an Enum (Home Assistant core turns it into a percentage)."""
    f = {"switch": fn("switch", "Boolean"), "fan_speed_enum": fn("fan_speed_enum", "Enum", range=["low", "mid", "high"])}
    return {"id": dev_id, "category": "fs", "product_id": "pf", "name": "Fan", "product_name": "Fan",
            "function": f, "status_range": f, "status": {},
            "local_strategy": strat({"1": ("switch", "Boolean"), "3": ("fan_speed_enum", "Enum")})}


def status_reply(ids, offset=0, has_more=False):
    """What rustuya-bridge answers to `{"action": "status"}`: the devices it holds (a page of them)."""
    return json.dumps({"action": "status", "status": "ok", "devices": {i: {"id": i} for i in ids}, "offset": offset,
                       "returned": len(ids), "has_more": has_more, "device_count": len(ids)})


GOLDEN = pathlib.Path(__file__).resolve().parents[3] / "tuya2ildevice/tests/golden"      # the sibling checkout
WIRE = {"Boolean": "Boolean", "Integer": "Integer", "Enum": "Enum", "String": "String", "Raw": "Raw", "Json": "Json",
        "Bitmap": "Bitmap"}


def fixture_record(code, dev_id):
    """The fixture as a device record a real deployment would have: dps numbered in status order, with a local_strategy."""
    if str(GOLDEN) not in sys.path:
        sys.path.insert(0, str(GOLDEN))
    import fixtures
    d = fixtures.load(code)
    codes = list(d["status"])
    dpmap = {str(i + 1): c for i, c in enumerate(codes)}
    types = {}
    for c in codes:
        spec = d["status_range"].get(c) or d["function"].get(c)
        types[c] = WIRE.get(spec["type"], "String") if spec else ("Boolean" if isinstance(d["status"][c], bool) else
                   "Integer" if isinstance(d["status"][c], int) else "String")
    d["local_strategy"] = {i: {"value_convert": "default", "status_code": c,
                               "config_item": {"statusFormat": {c: "$"}, "valueType": types[c], "valueDesc": {},
                                               "enumMappingMap": {}}} for i, c in dpmap.items()}
    d["id"] = dev_id
    dps = {i: d["status"][c] for i, c in dpmap.items()}
    return d, dps


