"""Connection sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ExtronConfigEntry, ExtronEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the connection sensor."""
    async_add_entities([ExtronConnection(entry)])


class ExtronConnection(ExtronEntity, BinarySensorEntity):
    """On while the unit is answering."""

    _attr_name = "Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "connection")

    @property
    def available(self) -> bool:
        """Always available: it reports the state of the link itself."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the unit is answering."""
        return self._client.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the unit and the latest problem."""
        client = self._client
        return {
            "firmware": client.banner.get("firmware"),
            "part_number": client.banner.get("part_number"),
            "login": client.privilege or None,
            "read_only": client.read_only,
            "last_reply": client.last_reply.isoformat() if client.last_reply else None,
            "last_error": client.last_error,
            "last_error_time": client.last_error_time.isoformat()
            if client.last_error_time
            else None,
        }
