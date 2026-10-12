"""Run the host-owned `Hub` over tuya-interpreter drivers: MQTT (or in-process) transports, timers, Last Will presence, reconnects,
runtime device changes, following rustuya-manager's `tuyadevices.json` and a directory of user rule files. TuyaDriver is Sans-I/O; this package owns file loading, clocks and transport operations.

    from rustuya_local.host import MqttTransport, Runner
"""
from .devices import DeviceWatcher, load_devices, parse_devices
from .hub import Hub
from .il import (
    BridgeCommand,
    IlTopics,
    Publish,
    SaveSettings,
    Schedule,
    Subscribe,
    Unschedule,
)
from .memory import InProcessTransport
from .overrides import SETTINGS_FILE, OverrideSet, OverrideWatcher, load_overrides
from .runner import Runner, producer_running
from .transport import Message, Transport

__all__ = [
    "SETTINGS_FILE",
    "BridgeCommand",
    "DeviceWatcher",
    "Hub",
    "IlTopics",
    "InProcessTransport",
    "Message",
    "OverrideSet",
    "OverrideWatcher",
    "Publish",
    "Runner",
    "SaveSettings",
    "Schedule",
    "Subscribe",
    "Transport",
    "Unschedule",
    "load_devices",
    "load_overrides",
    "parse_devices",
    "producer_running",
]


def __getattr__(name: str):                  # MqttTransport imports paho only when asked for
    if name == "MqttTransport":
        from .mqtt import MqttTransport
        return MqttTransport
    raise AttributeError(name)
