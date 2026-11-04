"""What every platform module does: hand its `async_add_entities` to the ildevice hub, which creates the entities as
descriptors arrive."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


def platform_setup(module: str):
    """The `async_setup_entry` of the platform module named `module` (its `__name__`)."""
    platform = module.rsplit(".", 1)[-1]

    async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
    ) -> None:
        hass.data[DOMAIN][entry.entry_id].hub.register_platform(platform, async_add_entities)

    return async_setup_entry
