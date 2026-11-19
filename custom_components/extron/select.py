"""Selects: per output the linked, video and audio input, a group; per input a group; the front-panel lock."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import strip_video
from .const import GROUP_OPTIONS, LOCK_OPTIONS, SOURCE_MIXED
from .entity import (
    ExtronConfigEntry,
    ExtronEntity,
    name_to_number,
    number_to_name,
    source_options,
)

LOCK_BY_LABEL = {label: mode for mode, label in LOCK_OPTIONS.items()}
GROUP_BY_LABEL = {label: group for group, label in GROUP_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the selects."""
    data = entry.runtime_data
    entities: list[SelectEntity] = [ExtronLockSelect(entry)]
    for output in range(1, data.client.outputs + 1):
        entities.append(ExtronLinkedSelect(entry, output))
        entities.append(ExtronLayerSelect(entry, output, video=True))
        entities.append(ExtronLayerSelect(entry, output, video=False))
        entities.append(ExtronGroupSelect(entry, output, is_output=True))
    for number in range(1, data.client.inputs + 1):
        entities.append(ExtronGroupSelect(entry, number, is_output=False))
    async_add_entities(entities)


class ExtronLinkedSelect(ExtronEntity, SelectEntity):
    """Choose one input for an output's video and audio together (shows Mixed if they differ)."""

    def __init__(self, entry: ExtronConfigEntry, output: int) -> None:
        """Initialise."""
        super().__init__(entry, f"out_{output}_link")
        self._output = output
        names = [strip_video(n) for n in self._data.in_video]
        self._names = names
        self._attr_name = f"{self._data.out_base[output - 1]} Input"
        self._attr_options = [*source_options(names), SOURCE_MIXED]

    @property
    def current_option(self) -> str | None:
        """Return the input feeding both video and audio, or Mixed."""
        video = self._client.video.get(self._output)
        audio = self._client.audio.get(self._output)
        if video is None or audio is None:
            return None
        if video != audio:
            return SOURCE_MIXED
        return number_to_name(self._names, video)

    @property
    def icon(self) -> str | None:
        """Show a linked chain while video and audio are in step."""
        video = self._client.video.get(self._output)
        audio = self._client.audio.get(self._output)
        if video is None or audio is None:
            return None
        return "mdi:link" if video == audio else "mdi:link-off"

    async def async_select_option(self, option: str) -> None:
        """Tie the chosen input to video and audio."""
        if option == SOURCE_MIXED:
            raise ServiceValidationError(
                "Mixed is only a status. Choose an input or None instead."
            )
        await self._call(
            self._client.async_set_tie(
                self._output, name_to_number(self._names, option)
            )
        )


class ExtronLayerSelect(ExtronEntity, SelectEntity):
    """Choose the input for one output's video, or for its audio, on its own."""

    def __init__(self, entry: ExtronConfigEntry, output: int, *, video: bool) -> None:
        """Initialise."""
        super().__init__(entry, f"out_{output}_{'video' if video else 'audio'}")
        self._output = output
        self._video = video
        self._names = self._data.in_video if video else self._data.in_audio
        label = self._data.out_video if video else self._data.out_audio
        self._attr_name = f"{label[output - 1]} Input"
        self._attr_options = source_options(self._names)

    @property
    def current_option(self) -> str | None:
        """Return the tied input."""
        table = self._client.video if self._video else self._client.audio
        return number_to_name(self._names, table.get(self._output))

    async def async_select_option(self, option: str) -> None:
        """Tie the chosen input to this layer."""
        await self._call(
            self._client.async_set_tie(
                self._output,
                name_to_number(self._names, option),
                video=self._video,
                audio=not self._video,
            )
        )


class ExtronGroupSelect(ExtronEntity, SelectEntity):
    """An input's or output's I/O group (limits which ties the front panel allows; SIS is unaffected)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_options: ClassVar[list[str]] = list(GROUP_OPTIONS.values())
    _attr_icon = "mdi:group"

    def __init__(
        self, entry: ExtronConfigEntry, number: int, *, is_output: bool
    ) -> None:
        """Initialise."""
        super().__init__(entry, f"{'out' if is_output else 'in'}_{number}_group")
        self._number = number
        self._is_output = is_output
        names = self._data.out_base if is_output else self._data.in_base
        self._attr_name = f"{names[number - 1]} Group"

    @property
    def _groups(self) -> list[int] | None:
        return self._client.groups_out if self._is_output else self._client.groups_in

    @property
    def available(self) -> bool:
        """Only where the unit reported its groups."""
        return super().available and self._groups is not None

    @property
    def current_option(self) -> str | None:
        """Return the group."""
        groups = self._groups
        if groups is None or self._number > len(groups):
            return None
        return GROUP_OPTIONS.get(groups[self._number - 1])

    async def async_select_option(self, option: str) -> None:
        """Change the group."""
        await self._call(
            self._client.async_set_group(
                output=self._is_output,
                number=self._number,
                group=GROUP_BY_LABEL[option],
            )
        )


class ExtronLockSelect(ExtronEntity, SelectEntity):
    """The front-panel lock. SIS changes still work while it is on."""

    _attr_name = "Front panel lock"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = list(LOCK_OPTIONS.values())
    _attr_icon = "mdi:lock"

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "lock")

    @property
    def current_option(self) -> str | None:
        """Return the lock mode."""
        mode = self._client.lock_mode
        return None if mode is None else LOCK_OPTIONS.get(mode)

    async def async_select_option(self, option: str) -> None:
        """Set the lock mode."""
        await self._call(self._client.async_set_lock(LOCK_BY_LABEL[option]))
