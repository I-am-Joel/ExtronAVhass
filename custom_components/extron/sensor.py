"""Preset sensors: whether each preset is saved or empty, and its name."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ExtronConfigEntry, ExtronEntity

STATE_SAVED = "saved"
STATE_EMPTY = "empty"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one sensor per preset the unit has."""
    client = entry.runtime_data.client
    async_add_entities(
        ExtronPresetSensor(entry, p) for p in range(1, client.preset_count + 1)
    )


class ExtronPresetSensor(ExtronEntity, SensorEntity):
    """Saved or empty, with the preset's name as an attribute."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [STATE_SAVED, STATE_EMPTY]
    _attr_icon = "mdi:content-save-cog"

    def __init__(self, entry: ExtronConfigEntry, preset: int) -> None:
        """Initialise."""
        super().__init__(entry, f"preset_{preset}")
        self._preset = preset
        self._attr_name = f"Preset {preset}"

    @property
    def native_value(self) -> str | None:
        """Return saved or empty."""
        info = self._client.presets.get(self._preset)
        if info is None:
            return None
        return STATE_SAVED if info.saved else STATE_EMPTY

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the preset number and name."""
        info = self._client.presets.get(self._preset)
        return {
            "preset": self._preset,
            "preset_name": info.name if info and info.saved else None,
        }
