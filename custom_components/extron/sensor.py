"""Preset sensors: whether each preset is saved or empty, and its name."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DST_OPTIONS, SOURCE_NONE
from .entity import ExtronConfigEntry, ExtronEntity, is_scaler
from .protocol import parse_clock
from .scaler_entities import setup_scaler_sensors

STATE_SAVED = "saved"
STATE_EMPTY = "empty"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one sensor per preset the unit has."""
    if is_scaler(entry):
        await setup_scaler_sensors(entry, async_add_entities)
        return
    client = entry.runtime_data.client
    entities: list[SensorEntity] = [ExtronRoutingSummary(entry)]
    entities.extend(
        ExtronPresetSensor(entry, p) for p in range(1, client.preset_count + 1)
    )
    for description in DESCRIPTIONS:
        present = (
            description.key in client.status
            if description.source == "status"
            else description.key in client.info
            if description.source == "info"
            else description.key in client.banner
            if description.source == "banner"
            else True
        )
        if present:
            entities.append(ExtronDiagnosticSensor(entry, description))
    async_add_entities(entities)


def _fahrenheit_to_celsius(value: float) -> float:
    return round((float(value) - 32) * 5 / 9, 1)


def _clock_to_timestamp(value: Any) -> Any:
    """The unit shows a local time; take it as Home Assistant's local time (what Sync clock writes)."""
    return parse_clock(str(value), dt_util.get_default_time_zone())


def _on_off(value: Any) -> str:
    return "On" if value else "Off"


@dataclass(frozen=True, kw_only=True)
class ExtronSensorDescription(SensorEntityDescription):
    """A sensor read from the unit's status, its information, its banner or worked out here."""

    source: str  # status, info, banner or computed
    convert: Callable[[Any], Any] | None = None


def _volts(key: str, name: str) -> ExtronSensorDescription:
    return ExtronSensorDescription(
        key=key,
        source="status",
        name=name,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
    )


def _text(
    source: str, key: str, name: str, icon: str, **extra: Any
) -> ExtronSensorDescription:
    return ExtronSensorDescription(
        key=key,
        source=source,
        name=name,
        icon=icon,
        entity_category=EntityCategory.DIAGNOSTIC,
        **extra,
    )


DESCRIPTIONS: tuple[ExtronSensorDescription, ...] = (
    ExtronSensorDescription(
        key="temp_f",
        source="status",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        convert=_fahrenheit_to_celsius,
    ),
    ExtronSensorDescription(
        key="fan1",
        source="status",
        name="Fan 1 speed",
        icon="mdi:fan",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        convert=int,
    ),
    ExtronSensorDescription(
        key="fan2",
        source="status",
        name="Fan 2 speed",
        icon="mdi:fan",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        convert=int,
    ),
    _volts("v5", "5 V rail"),
    _volts("v3_3", "3.3 V rail"),
    _volts("v2_5", "2.5 V rail"),
    _volts("fan_v", "Fan supply voltage"),
    _volts("v_m5", "-5 V rail"),
    _volts("v_m12", "-12 V rail"),
    _volts("v12", "12 V rail"),
    _volts("v15", "15 V rail"),
    _volts("v_m15", "-15 V rail"),
    _text("banner", "firmware", "Firmware version", "mdi:chip"),
    _text("banner", "part_number", "Part number", "mdi:identifier"),
    _text("info", "unit_name", "Unit name", "mdi:tag-text"),
    _text("info", "ip", "IP address", "mdi:ip-network"),
    _text("info", "subnet", "Subnet mask", "mdi:ip-network-outline"),
    _text("info", "gateway", "Gateway", "mdi:router-network"),
    _text("info", "mac", "MAC address", "mdi:network"),
    _text("info", "dhcp", "DHCP", "mdi:ip", convert=_on_off),
    ExtronSensorDescription(
        key="clock",
        source="info",
        name="Unit clock",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        convert=_clock_to_timestamp,
    ),
    _text("info", "gmt_offset", "Time zone offset", "mdi:earth"),
    _text(
        "info",
        "dst",
        "Daylight saving",
        "mdi:weather-sunny",
        convert=lambda value: DST_OPTIONS.get(value, "Unknown"),
    ),
    _text(
        "info",
        "connections",
        "Open connections",
        "mdi:lan-connect",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _text(
        "info",
        "timeout",
        "Connection timeout",
        "mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
    _text("info", "serial", "Serial port settings", "mdi:serial-port"),
    _text(
        "info",
        "free_space",
        "Free file space",
        "mdi:harddisk",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.KILOBYTES,
        suggested_display_precision=0,
    ),
    _text("computed", "inputs", "Inputs", "mdi:import"),
    _text("computed", "outputs", "Outputs", "mdi:export"),
    _text("computed", "tied_outputs", "Outputs with a source", "mdi:call-merge"),
)


class ExtronDiagnosticSensor(ExtronEntity, SensorEntity):
    """A reading from the unit's status, information or banner."""

    entity_description: ExtronSensorDescription

    def __init__(
        self, entry: ExtronConfigEntry, description: ExtronSensorDescription
    ) -> None:
        """Initialise."""
        super().__init__(entry, f"diag_{description.key}")
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the reading."""
        description = self.entity_description
        client = self._client
        if description.source == "status":
            value = client.status.get(description.key)
        elif description.source == "info":
            value = client.info.get(description.key)
        elif description.source == "banner":
            value = client.banner.get(description.key)
        elif description.key == "inputs":
            return client.inputs
        elif description.key == "outputs":
            return client.outputs
        else:
            return sum(
                1
                for o in range(1, client.outputs + 1)
                if client.video.get(o) or client.audio.get(o)
            )
        if value is None:
            return None
        return description.convert(value) if description.convert else value


class ExtronRoutingSummary(ExtronEntity, SensorEntity):
    """How many outputs have a source, with the full routing as attributes."""

    _attr_name = "Routing summary"
    _attr_icon = "mdi:transit-connection-variant"
    _unrecorded_attributes = frozenset({"routes", "by_input_video", "by_input_audio"})

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "routing_summary")

    def _input_name(self, number: int | None) -> str | None:
        if number is None:
            return None
        if number == 0:
            return SOURCE_NONE
        names = self._data.in_base
        return names[number - 1] if 1 <= number <= len(names) else f"Input {number}"

    @property
    def native_value(self) -> str | None:
        """Return, for example, "24 of 24 outputs routed"."""
        client = self._client
        if not client.video and not client.audio:
            return None
        routed = sum(
            1
            for o in range(1, client.outputs + 1)
            if client.video.get(o) or client.audio.get(o)
        )
        return f"{routed} of {client.outputs} outputs routed"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return each output's sources, and each input's outputs."""
        client = self._client
        data = self._data
        routes: dict[str, dict[str, str | None]] = {}
        by_video: dict[str, list[str]] = {}
        by_audio: dict[str, list[str]] = {}
        for output in range(1, client.outputs + 1):
            out_name = data.out_base[output - 1]
            video = self._input_name(client.video.get(output))
            audio = self._input_name(client.audio.get(output))
            routes[out_name] = {"video": video, "audio": audio}
            if video and video != SOURCE_NONE:
                by_video.setdefault(video, []).append(out_name)
            if audio and audio != SOURCE_NONE:
                by_audio.setdefault(audio, []).append(out_name)
        return {
            "routes": routes,
            "by_input_video": by_video,
            "by_input_audio": by_audio,
        }


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
