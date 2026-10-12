"""IL MQTT wire mapping and host actions. Tuya interpretation stays in tuya-interpreter."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tuya_interpreter import (
    Absent,
    CancelTimer,
    Descriptor,
    Event,
    Reject,
    SendMessage,
    SetTimer,
    SettingsChanged,
    Value,
)

IL = "il"


@dataclass(frozen=True)
class Publish:
    side: str
    topic: str
    payload: str
    retain: bool = False
    qos: int = 1


@dataclass(frozen=True)
class Subscribe:
    side: str
    topic: str
    qos: int = 1


@dataclass(frozen=True)
class Schedule:
    """Ask the host to call `Hub.on_timer(now, device_id, name)` after `after` seconds (replacing an equal name)."""
    device_id: str
    name: str
    after: float


@dataclass(frozen=True)
class Unschedule:
    device_id: str
    name: str


@dataclass(frozen=True)
class BridgeCommand:
    """Ask the host to tell the bridge to read (`get`) or write (`set`, `dps`) a device."""
    device_id: str
    action: str
    dps: dict[str, Any] | None = None


@dataclass(frozen=True)
class SaveSettings:
    """Ask the host to keep `block` as the device's settings (its override block; empty removes it) and reload."""
    device_id: str
    block: dict


class IlTopics:
    """Transport-owned il-mqtt topic layout."""

    def __init__(self, prefix: str = "il", source: str = "tuya"):
        self.prefix, self.source = prefix, source

    def descriptor(self, device_id: str) -> str:
        return f"{self.prefix}/{device_id}"

    def state(self, device_id: str, prop: str) -> str:
        return f"{self.prefix}/{device_id}/{prop}"

    def reject(self, device_id: str) -> str:
        return f"{self.prefix}/{device_id}/reject"

    @property
    def presence(self) -> str:
        return f"{self.prefix}/_producer/{self.source}"

    def set_subscription(self) -> str:
        return f"{self.prefix}/+/+/set"

    def parse_set(self, topic: str) -> tuple[str, str] | None:
        prefix = f"{self.prefix}/"
        if not topic.startswith(prefix):
            return None
        parts = topic[len(prefix):].split("/")
        if len(parts) == 3 and not parts[0].startswith("_") and parts[2] == "set":
            return parts[0], parts[1]
        return None

    def clear(self, device_id: str, props, *, descriptor=False) -> list[Publish]:
        outputs = [Publish(IL, self.state(device_id, p), "", True) for p in props]
        if descriptor:
            outputs.append(Publish(IL, self.descriptor(device_id), "", True))
        return outputs

    def actions(self, device_id: str, outputs) -> list:
        actions = []
        for output in outputs:
            if isinstance(output, Descriptor):
                desc = {**output.desc, "source": self.source}
                actions.append(Publish(IL, self.descriptor(device_id), json.dumps(desc, ensure_ascii=False), True))
            elif isinstance(output, Value):
                actions.append(Publish(IL, self.state(device_id, output.prop), encode_value(output.value), True))
            elif isinstance(output, Absent):
                actions.append(Publish(IL, self.state(device_id, output.prop), "", True))
            elif isinstance(output, Event):
                actions.append(Publish(IL, self.state(device_id, output.prop), output.kind, False))
            elif isinstance(output, Reject):
                payload = {"prop": output.prop, "code": output.code, "reason": output.reason}
                actions.append(Publish(IL, self.reject(device_id), json.dumps(payload, ensure_ascii=False), False))
            elif isinstance(output, SetTimer):
                actions.append(Schedule(device_id, output.name, output.after))
            elif isinstance(output, CancelTimer):
                actions.append(Unschedule(device_id, output.name))
            elif isinstance(output, SendMessage):
                if output.channel != "set":
                    raise ValueError(f"unsupported driver send channel: {output.channel}")
                actions.append(BridgeCommand(device_id, "set", output.json["dps"]))
            elif isinstance(output, SettingsChanged):
                actions.append(SaveSettings(device_id, output.block))
            else:
                raise TypeError(f"unsupported driver output: {output!r}")
        return actions


def encode_value(value: Any) -> str:
    """IL state payloads: strings are unquoted; integral numbers have no fractional part."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def decode_write(ptype: str, payload: str) -> Any:
    """Decode wire data; the Rust driver's IL command checks decide whether it is valid."""
    if ptype == "trigger":
        return None
    if ptype == "number":
        try:
            value = json.loads(payload, parse_constant=lambda _: None)
            return value
        except ValueError:
            pass
    return payload


def usable_id(device_id: str) -> bool:
    return bool(device_id) and not device_id.startswith("_") and not any(c in device_id for c in "/+#\0")
