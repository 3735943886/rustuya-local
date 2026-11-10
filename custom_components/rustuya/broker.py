"""The broker an entry's data points at: its URL, its credentials and an MQTT connection to it, and the rule its topic
names (the bridge root, the IL prefix and source) follow. An entry on the built-in broker (`CONF_BROKER_BUILTIN`) stores
no broker fields: `resolve` fills them in from the running one, and the functions here take what it returns."""

from __future__ import annotations

import importlib
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BROKER_BUILTIN,
    CONF_BROKER_HOST,
    CONF_BROKER_PASSWORD,
    CONF_BROKER_PORT,
    CONF_BROKER_USERNAME,
)


async def resolve(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """`data` with the broker it uses filled in: the built-in one started if need be (`import_runtime` first)."""
    if not data.get(CONF_BROKER_BUILTIN):
        return dict(data)
    from . import builtin_broker

    endpoint = await builtin_broker.async_ensure(hass)
    return {**data, CONF_BROKER_HOST: endpoint.host, CONF_BROKER_PORT: endpoint.port,
            CONF_BROKER_USERNAME: endpoint.username, CONF_BROKER_PASSWORD: endpoint.password}


def url(data: dict[str, Any]) -> str:
    return f"mqtt://{data[CONF_BROKER_HOST]}:{data[CONF_BROKER_PORT]}"


def credentials(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """(username, password); an empty field is none."""
    return data.get(CONF_BROKER_USERNAME) or None, data.get(CONF_BROKER_PASSWORD) or None


OPTIONAL = ("rustuya_manager", "pyrustuyabridge", "pymqttd")
_found: dict[str, bool] = {}


def _import_runtime() -> dict[str, bool]:
    """Load host modules in Home Assistant's import executor; Service loads native code and rules off the loop, and
    rustuya_manager pulls in pyrustuyabridge, tuyawizard and qrcode; the ildevice hub imports its entity classes, and
    with them Home Assistant's platforms, when the first device arrives. Which of the `OPTIONAL` packages imported."""
    import ildevice_ha.attach
    import ildevice_ha.entity  # noqa: F401

    import rustuya_local.host
    import rustuya_local.manager_plugin.api  # the panel's converter views
    import rustuya_local.service
    import rustuya_local.validation  # noqa: F401

    found = {}
    for name in OPTIONAL:
        try:
            importlib.import_module(name)
        except ImportError:
            found[name] = False
        else:
            found[name] = True
    return found


async def import_runtime(hass: HomeAssistant) -> None:
    """Once loaded, the imports the integration does where it uses these modules are only `sys.modules` lookups."""
    if not _found:
        _found.update(await hass.async_add_import_executor_job(_import_runtime))


async def importable(hass: HomeAssistant, name: str) -> bool:
    """Whether the `OPTIONAL` package `name` is installed and loaded."""
    await import_runtime(hass)
    return _found[name]


def transport(data: dict[str, Any], client_id: str, will: Any = None) -> Any:
    """An `MqttTransport` to the broker, not connected yet (`import_runtime` first)."""
    from rustuya_local.host import MqttTransport

    username, password = credentials(data)
    return MqttTransport(data[CONF_BROKER_HOST], data[CONF_BROKER_PORT], client_id=client_id, username=username,
                         password=password, will=will)


def topic_ok(value: str) -> bool:
    """An MQTT topic to publish on: not empty, no wildcards, no empty levels (`import_runtime` first)."""
    from rustuya_local.validation import topic_ok as valid_topic

    return valid_topic(value)
