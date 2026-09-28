"""The broker an entry's data points at: its URL, its credentials and an MQTT connection to it, and the rule its topic
names (the bridge root, the IL prefix and source) follow."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BROKER_HOST,
    CONF_BROKER_PASSWORD,
    CONF_BROKER_PORT,
    CONF_BROKER_USERNAME,
)


def url(data: dict[str, Any]) -> str:
    return f"mqtt://{data[CONF_BROKER_HOST]}:{data[CONF_BROKER_PORT]}"


def credentials(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """(username, password); an empty field is none."""
    return data.get(CONF_BROKER_USERNAME) or None, data.get(CONF_BROKER_PASSWORD) or None


def _import_runtime() -> None:
    """Importing tuya2ildevice reads its data files; run in Home Assistant's import executor, not the event loop."""
    import tuya2ildevice.host  # noqa: F401

    import rustuya_local.service  # noqa: F401


async def import_runtime(hass: HomeAssistant) -> None:
    await hass.async_add_import_executor_job(_import_runtime)


def transport(data: dict[str, Any], client_id: str, will: Any = None) -> Any:
    """An `MqttTransport` to the broker, not connected yet (`import_runtime` first)."""
    from tuya2ildevice.host import MqttTransport

    username, password = credentials(data)
    return MqttTransport(data[CONF_BROKER_HOST], data[CONF_BROKER_PORT], client_id=client_id, username=username,
                         password=password, will=will)


def topic_ok(value: str) -> bool:
    """An MQTT topic to publish on: not empty, no wildcards, no empty levels."""
    return bool(value) and not any(c in value for c in "+#\0") and "" not in value.split("/")
