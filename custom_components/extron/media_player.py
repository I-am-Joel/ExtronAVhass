"""One media player for the whole unit: choose an input for every output at once."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import strip_video
from .const import SOURCE_MIXED, SOURCE_NONE
from .entity import (
    ExtronConfigEntry,
    ExtronEntity,
    name_to_number,
    number_to_name,
    source_options,
)

MODES = {"both": (True, True), "video": (True, False), "audio": (False, True)}

ROUTE_SCHEMA = {
    vol.Required("input"): cv.string,
    vol.Required("outputs"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
    vol.Optional("mode", default="both"): vol.In(list(MODES)),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the media player and the route action."""
    async_add_entities([ExtronAllOutputs(entry)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("route", ROUTE_SCHEMA, "async_route")


def _lookup(candidates: list[tuple[str, int]], text: str, what: str) -> int:
    """Find a name (any case) or a plain number in a list of (name, number) pairs."""
    wanted = text.strip().casefold()
    for name, number in candidates:
        if name.casefold() == wanted:
            return number
    if wanted.isdigit() and any(number == int(wanted) for _, number in candidates):
        return int(wanted)
    raise ServiceValidationError(f"Unknown {what}: {text!r}")


class ExtronAllOutputs(ExtronEntity, MediaPlayerEntity):
    """Selecting a source ties that input (video and audio) to every output."""

    _attr_name = "All outputs"
    _attr_icon = "mdi:video-input-component"
    _attr_supported_features = MediaPlayerEntityFeature.SELECT_SOURCE

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "all_outputs")
        self._names = [strip_video(n) for n in self._data.in_video]
        self._attr_source_list = source_options(self._names)

    @property
    def state(self) -> MediaPlayerState | None:
        """The matrix is always on while it answers."""
        return MediaPlayerState.ON if self.available else None

    @property
    def source(self) -> str | None:
        """The input feeding every output, None (the word) if none, or Mixed."""
        values = self._client.source_values()
        if None in values:
            return None
        if len(values) == 1:
            return number_to_name(self._names, values.pop())
        return SOURCE_MIXED

    async def async_select_source(self, source: str) -> None:
        """Tie the chosen input (or none) to every output, video and audio."""
        if source == SOURCE_MIXED:
            raise ServiceValidationError(
                "Mixed is only a status. Choose an input or None instead."
            )
        if source not in self._attr_source_list:
            raise ServiceValidationError(f"Unknown input: {source!r}")
        await self._call(
            self._client.async_set_tie_all(name_to_number(self._names, source))
        )

    async def async_route(self, **kwargs: Any) -> None:
        """Tie one input to the named outputs (the action ``extron.route``)."""
        data = self._data
        inputs = [(n, i) for i, n in enumerate(self._names, start=1)]
        inputs += [(n, i) for i, n in enumerate(data.in_base, start=1)]
        inputs.append((SOURCE_NONE, 0))
        input_number = (
            0
            if kwargs["input"].strip().isdigit() and int(kwargs["input"]) == 0
            else _lookup(inputs, kwargs["input"], "input")
        )
        outputs = [(n, o) for o, n in enumerate(data.out_base, start=1)]
        outputs += [(strip_video(n), o) for o, n in enumerate(data.out_video, start=1)]
        outputs += [
            (n.removesuffix(" Audio"), o) for o, n in enumerate(data.out_audio, start=1)
        ]
        chosen = [_lookup(outputs, text, "output") for text in kwargs["outputs"]]
        video, audio = MODES[kwargs["mode"]]
        await self._call(
            self._client.async_route(input_number, chosen, video=video, audio=audio)
        )
