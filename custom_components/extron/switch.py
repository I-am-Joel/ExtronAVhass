"""Mute switches: video and audio, one each per output."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ExtronConfigEntry, ExtronEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mute switches."""
    outputs = entry.runtime_data.client.outputs
    entities: list[SwitchEntity] = []
    for output in range(1, outputs + 1):
        entities.append(ExtronMute(entry, output, video=True))
        entities.append(ExtronMute(entry, output, video=False))
    async_add_entities(entities)


class ExtronMute(ExtronEntity, SwitchEntity):
    """On = muted."""

    def __init__(self, entry: ExtronConfigEntry, output: int, *, video: bool) -> None:
        """Initialise."""
        super().__init__(entry, f"out_{output}_{'video' if video else 'audio'}_mute")
        self._output = output
        self._video = video
        names = self._data.out_video if video else self._data.out_audio
        self._attr_name = f"{names[output - 1]} Mute"
        self._attr_icon = "mdi:monitor-off" if video else "mdi:volume-off"

    @property
    def is_on(self) -> bool | None:
        """Return whether muted."""
        table = self._client.video_mute if self._video else self._client.audio_mute
        return table.get(self._output)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Mute."""
        await self._call(
            self._client.async_set_mute(self._output, video=self._video, muted=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unmute."""
        await self._call(
            self._client.async_set_mute(self._output, video=self._video, muted=False)
        )
