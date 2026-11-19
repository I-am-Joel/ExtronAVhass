"""Numbers: output volume (0 to 64) and input level (-18 to +24 dB)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ExtronConfigEntry, ExtronEntity
from .protocol import MAX_GAIN, MAX_VOLUME, MIN_GAIN, MIN_VOLUME


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the numbers."""
    client = entry.runtime_data.client
    entities: list[NumberEntity] = [
        ExtronVolume(entry, o) for o in range(1, client.outputs + 1)
    ]
    entities += [ExtronLevel(entry, i) for i in range(1, client.inputs + 1)]
    async_add_entities(entities)


class ExtronVolume(ExtronEntity, NumberEntity):
    """An output's volume, 0 to 64 (the unit's own scale)."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = MIN_VOLUME
    _attr_native_max_value = MAX_VOLUME
    _attr_native_step = 1
    _attr_icon = "mdi:volume-high"

    def __init__(self, entry: ExtronConfigEntry, output: int) -> None:
        """Initialise."""
        super().__init__(entry, f"out_{output}_volume")
        self._output = output
        self._attr_name = f"{self._data.out_audio[output - 1]} Volume"

    @property
    def native_value(self) -> float | None:
        """Return the volume."""
        return self._client.volume.get(self._output)

    async def async_set_native_value(self, value: float) -> None:
        """Set the volume."""
        await self._call(self._client.async_set_volume(self._output, int(value)))


class ExtronLevel(ExtronEntity, NumberEntity):
    """An input's audio level in dB (negative values attenuate)."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = MIN_GAIN
    _attr_native_max_value = MAX_GAIN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"
    _attr_icon = "mdi:tune-vertical"

    def __init__(self, entry: ExtronConfigEntry, number: int) -> None:
        """Initialise."""
        super().__init__(entry, f"in_{number}_level")
        self._number = number
        self._attr_name = f"{self._data.in_audio[number - 1]} Level"

    @property
    def native_value(self) -> float | None:
        """Return the level."""
        return self._client.gain.get(self._number)

    async def async_set_native_value(self, value: float) -> None:
        """Set the level."""
        await self._call(self._client.async_set_gain(self._number, int(value)))
