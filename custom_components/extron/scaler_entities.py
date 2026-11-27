"""Entities for the IN1508 scaling switcher (the platform files hand over to these)."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ExtronConfigEntry, ExtronEntity
from .in1508 import (
    ASPECTS,
    INPUTS,
    MAX_GAIN,
    MAX_PICTURE,
    MAX_VOLUME,
    MIN_GAIN,
    MIN_PICTURE,
    MIN_VOLUME,
    PIP_SIZES,
    RATES,
    RESOLUTIONS,
    STANDARDS,
    VALID_RATES,
    clamp_rate,
)

INPUT_LABELS = {n: f"Input {n}" for n in range(1, INPUTS + 1)}
RESOLUTION_BY_LABEL = {label: number for number, label in RESOLUTIONS.items()}
RATE_BY_LABEL = {label: number for number, label in RATES.items()}
PIP_BY_LABEL = {label: number for number, label in PIP_SIZES.items()}
ASPECT_BY_LABEL = {label: number for number, label in ASPECTS.items()}

PICTURE_CONTROLS = (
    ("colour", "Colour", "mdi:palette"),
    ("tint", "Tint", "mdi:palette-swatch"),
    ("brightness", "Brightness", "mdi:brightness-6"),
    ("contrast", "Contrast", "mdi:contrast-circle"),
    ("sharpness", "Sharpness", "mdi:image-filter-center-focus"),
)


async def setup_scaler_selects(
    entry: ExtronConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Input, output resolution and rate, PIP size and aspect ratio."""
    async_add_entities(
        [
            ScalerInputSelect(entry),
            ScalerResolutionSelect(entry),
            ScalerRateSelect(entry),
            ScalerPipSizeSelect(entry),
            ScalerAspectSelect(entry),
        ]
    )


async def setup_scaler_numbers(
    entry: ExtronConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Volume, the picture controls and the input gain."""
    entities: list[NumberEntity] = [ScalerVolume(entry)]
    entities += [
        ScalerPictureNumber(entry, key, name, icon) for key, name, icon in PICTURE_CONTROLS
    ]
    entities.append(ScalerGain(entry))
    async_add_entities(entities)


async def setup_scaler_switches(
    entry: ExtronConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Picture-in-picture, freeze and the two mutes."""
    async_add_entities(
        [
            ScalerSwitch(entry, "pip", "Picture-in-picture", "mdi:picture-in-picture-top-right"),
            ScalerSwitch(entry, "freeze", "Freeze", "mdi:pause-box-outline"),
            ScalerSwitch(entry, "video_mute", "Video mute", "mdi:monitor-off"),
            ScalerSwitch(entry, "audio_mute", "Audio mute", "mdi:volume-off"),
        ]
    )


async def setup_scaler_sensors(
    entry: ExtronConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """The detected signal standard, firmware version and part number."""
    entities: list[SensorEntity] = [ScalerStandardSensor(entry)]
    banner = entry.runtime_data.client.banner
    for key, name, icon in (
        ("firmware", "Firmware version", "mdi:chip"),
        ("part_number", "Part number", "mdi:identifier"),
    ):
        if key in banner:
            entities.append(ScalerBannerSensor(entry, key, name, icon))
    async_add_entities(entities)


# -- selects -----------------------------------------------------------------------------


class ScalerInputSelect(ExtronEntity, SelectEntity):
    """The input shown on the output (video and audio together). Changing it takes a few seconds."""

    _attr_name = "Input"
    _attr_icon = "mdi:video-input-component"
    _attr_options: ClassVar[list[str]] = list(INPUT_LABELS.values())

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "input")

    @property
    def current_option(self) -> str | None:
        """Return the selected input."""
        return INPUT_LABELS.get(self._client.input)

    async def async_select_option(self, option: str) -> None:
        """Select the input."""
        number = next(n for n, label in INPUT_LABELS.items() if label == option)
        await self._call(self._client.async_select_input(number))


class ScalerResolutionSelect(ExtronEntity, SelectEntity):
    """The output resolution (the refresh rate is kept where the new resolution allows it)."""

    _attr_name = "Output resolution"
    _attr_icon = "mdi:monitor-screenshot"
    _attr_options: ClassVar[list[str]] = list(RESOLUTIONS.values())

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "resolution")

    @property
    def current_option(self) -> str | None:
        """Return the output resolution."""
        return RESOLUTIONS.get(self._client.resolution)

    async def async_select_option(self, option: str) -> None:
        """Set the resolution, keeping the rate if it is allowed, else 60 Hz or the nearest allowed."""
        resolution = RESOLUTION_BY_LABEL[option]
        rate = clamp_rate(resolution, self._client.rate or 2)
        await self._call(self._client.async_set_output(resolution, rate))


class ScalerRateSelect(ExtronEntity, SelectEntity):
    """The output refresh rate. Each resolution allows only some rates; a refused one says which."""

    _attr_name = "Refresh rate"
    _attr_icon = "mdi:update"
    _attr_options: ClassVar[list[str]] = list(RATES.values())

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "rate")

    @property
    def current_option(self) -> str | None:
        """Return the refresh rate."""
        return RATES.get(self._client.rate)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show the rates the current resolution allows."""
        allowed = VALID_RATES.get(self._client.resolution, ())
        return {"allowed_rates": [RATES[r] for r in allowed]}

    async def async_select_option(self, option: str) -> None:
        """Set the rate for the current resolution."""
        resolution = self._client.resolution
        if resolution is None:
            return
        await self._call(self._client.async_set_output(resolution, RATE_BY_LABEL[option]))


class ScalerPipSizeSelect(ExtronEntity, SelectEntity):
    """The size of the picture-in-picture window."""

    _attr_name = "Picture-in-picture size"
    _attr_icon = "mdi:picture-in-picture-bottom-right-outline"
    _attr_options: ClassVar[list[str]] = list(PIP_SIZES.values())

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "pip_size")

    @property
    def current_option(self) -> str | None:
        """Return the size."""
        return PIP_SIZES.get(self._client.pip_size)

    async def async_select_option(self, option: str) -> None:
        """Set the size."""
        await self._call(self._client.async_set_pip_size(PIP_BY_LABEL[option]))


class ScalerAspectSelect(ExtronEntity, SelectEntity):
    """The aspect ratio of the selected input (each input keeps its own)."""

    _attr_name = "Aspect ratio"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:aspect-ratio"
    _attr_options: ClassVar[list[str]] = list(ASPECTS.values())

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "aspect")

    @property
    def _value(self) -> int | None:
        picture = self._client.pictures.get(self._client.input)
        return picture.values.get("aspect") if picture else None

    @property
    def available(self) -> bool:
        """Only where the unit offers it for the selected input."""
        return super().available and self._value is not None

    @property
    def current_option(self) -> str | None:
        """Return the aspect ratio."""
        return ASPECTS.get(self._value)

    async def async_select_option(self, option: str) -> None:
        """Set the aspect ratio for the selected input."""
        await self._call(self._client.async_set_picture("aspect", ASPECT_BY_LABEL[option]))


# -- numbers --------------------------------------------------------------------------------


class ScalerVolume(ExtronEntity, NumberEntity):
    """The audio output volume, 0 to 100."""

    _attr_name = "Volume"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = MIN_VOLUME
    _attr_native_max_value = MAX_VOLUME
    _attr_native_step = 1
    _attr_icon = "mdi:volume-high"

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "volume")

    @property
    def native_value(self) -> float | None:
        """Return the volume."""
        return self._client.volume

    async def async_set_native_value(self, value: float) -> None:
        """Set the volume."""
        await self._call(self._client.async_set_volume(int(value)))


class _PictureBase(ExtronEntity, NumberEntity):
    """A control that applies to the selected input and is not offered for every input."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _key = ""

    @property
    def _value(self) -> int | None:
        picture = self._client.pictures.get(self._client.input)
        return picture.values.get(self._key) if picture else None

    @property
    def available(self) -> bool:
        """Only where the unit offers it for the selected input and its signal."""
        return super().available and self._value is not None

    @property
    def native_value(self) -> float | None:
        """Return the value for the selected input."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the value for the selected input."""
        await self._call(self._client.async_set_picture(self._key, int(value)))


class ScalerPictureNumber(_PictureBase):
    """Colour, tint, brightness, contrast or sharpness of the selected input (0 to 128)."""

    _attr_native_min_value = MIN_PICTURE
    _attr_native_max_value = MAX_PICTURE

    def __init__(self, entry: ExtronConfigEntry, key: str, name: str, icon: str) -> None:
        """Initialise."""
        super().__init__(entry, f"picture_{key}")
        self._key = key
        self._attr_name = name
        self._attr_icon = icon


class ScalerGain(_PictureBase):
    """The audio gain of the selected input, -50 to +9 dB."""

    _attr_name = "Input gain"
    _attr_native_min_value = MIN_GAIN
    _attr_native_max_value = MAX_GAIN
    _attr_native_unit_of_measurement = "dB"
    _attr_icon = "mdi:tune-vertical"
    _key = "gain"

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "gain")


# -- switches ------------------------------------------------------------------------------------


class ScalerSwitch(ExtronEntity, SwitchEntity):
    """Picture-in-picture, freeze, video mute or audio mute."""

    def __init__(self, entry: ExtronConfigEntry, key: str, name: str, icon: str) -> None:
        """Initialise."""
        super().__init__(entry, key)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return getattr(self._client, self._key)

    async def _set(self, on: bool) -> None:
        client = self._client
        if self._key == "pip":
            await self._call(client.async_set_pip(on))
        elif self._key == "freeze":
            await self._call(client.async_set_freeze(on))
        else:
            await self._call(
                client.async_set_mute(video=self._key == "video_mute", muted=on)
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._set(False)


# -- sensors -----------------------------------------------------------------------------------------


class ScalerStandardSensor(ExtronEntity, SensorEntity):
    """The video standard the unit detects on the selected input."""

    _attr_name = "Signal standard"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = list(STANDARDS.values())
    _attr_icon = "mdi:television-classic"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "standard")

    @property
    def native_value(self) -> str | None:
        """Return the standard (None means no signal of a standard the unit knows)."""
        return STANDARDS.get(self._client.standard)


class ScalerBannerSensor(ExtronEntity, SensorEntity):
    """Firmware version or part number."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ExtronConfigEntry, key: str, name: str, icon: str) -> None:
        """Initialise."""
        super().__init__(entry, f"diag_{key}")
        self._key = key
        self._attr_name = name
        self._attr_icon = icon

    @property
    def native_value(self) -> str | None:
        """Return the reading."""
        return self._client.banner.get(self._key)
