"""Small Tuya device records (as in tuyadevices.json plus the cloud schema) for the end-to-end tests."""
import json


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


def status_reply(ids, offset=0, has_more=False):
    """What rustuya-bridge answers to `{"action": "status"}`: the devices it holds (a page of them)."""
    return json.dumps({"action": "status", "status": "ok", "devices": {i: {"id": i} for i in ids}, "offset": offset,
                       "returned": len(ids), "has_more": has_more, "device_count": len(ids)})
