"""Mute switches: video and audio, one each per output."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ExtronConfigEntry, ExtronEntity, is_scaler
from .scaler_entities import setup_scaler_switches


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mute switches."""
    if is_scaler(entry):
        await setup_scaler_switches(entry, async_add_entities)
        return
    client = entry.runtime_data.client
    layers = (True, False) if client.audio_present else (True,)
    entities: list[SwitchEntity] = [ExtronMuteAll(entry, video=v) for v in layers]
    for output in range(1, client.outputs + 1):
        entities.extend(ExtronMute(entry, output, video=v) for v in layers)
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


class ExtronMuteAll(ExtronEntity, SwitchEntity):
    """On when every output is muted; turning it on or off mutes or unmutes them all."""

    def __init__(self, entry: ExtronConfigEntry, *, video: bool) -> None:
        """Initialise."""
        super().__init__(entry, f"mute_all_{'video' if video else 'audio'}")
        self._video = video
        self._attr_name = f"Mute all {'video' if video else 'audio'}"
        self._attr_icon = "mdi:monitor-off" if video else "mdi:volume-off"

    @property
    def _table(self) -> dict[int, bool]:
        return self._client.video_mute if self._video else self._client.audio_mute

    @property
    def is_on(self) -> bool | None:
        """Return whether every output is muted (None until they are all known)."""
        outputs = range(1, self._client.outputs + 1)
        if any(o not in self._table for o in outputs):
            return None
        return all(self._table[o] for o in outputs)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show how many outputs are muted."""
        return {"muted_outputs": sum(1 for muted in self._table.values() if muted)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Mute every output."""
        await self._call(self._client.async_set_mute_all(video=self._video, muted=True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unmute every output."""
        await self._call(
            self._client.async_set_mute_all(video=self._video, muted=False)
        )
