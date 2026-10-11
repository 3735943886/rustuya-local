"""Small hand-made Tuya devices for the host tests."""
import json


def fn(code, typ, **values):
    return {"code": code, "type": typ, "values": json.dumps(values) if values else "{}"}


def strat(dpid_code_type):
    out = {}
    for dpid, (code, typ) in dpid_code_type.items():
        out[dpid] = {"value_convert": "default", "status_code": code,
                     "config_item": {"statusFormat": {code: "$"}, "valueType": typ, "valueDesc": {}, "enumMappingMap": {}}}
    return out


def light():
    ints = {"unit": "", "min": 10, "max": 1000, "scale": 0, "step": 1}
    f = {"switch_led": fn("switch_led", "Boolean"), "bright_value_v2": fn("bright_value_v2", "Integer", **ints),
         "temp_value_v2": fn("temp_value_v2", "Integer", **ints)}
    return {"id": "lamp1", "category": "dj", "product_id": "p", "name": "Lamp", "product_name": "Bulb",
            "function": f, "status_range": f, "status": {},
            "local_strategy": strat({"20": ("switch_led", "Boolean"), "22": ("bright_value_v2", "Integer"),
                                     "23": ("temp_value_v2", "Integer")})}


def curtain():
    f = {"control": fn("control", "Enum", range=["open", "stop", "close"]),
         "percent_control": fn("percent_control", "Integer", unit="%", min=0, max=100, scale=0, step=1)}
    return {"id": "cur1", "category": "cl", "product_id": "p", "name": "Curtain", "product_name": "Curtain",
            "function": f, "status_range": {**f, "percent_state": fn("percent_state", "Integer", unit="%", min=0, max=100, scale=0, step=1)},
            "status": {}, "local_strategy": strat({"1": ("control", "Enum"), "2": ("percent_control", "Integer"),
                                                    "3": ("percent_state", "Integer")})}
