"""The Extron matrix switcher integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUDIO,
    CONF_AUDIO_INPUT_NAMES,
    CONF_AUDIO_OUTPUT_NAMES,
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_INPUTS,
    CONF_KIND,
    CONF_MODEL_NAME,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_READ_ONLY,
    CONF_REFRESH_SECONDS,
    CONF_SHOW_ADVANCED,
    CONF_UNIT_NAME,
    DEFAULT_PORT,
    DEFAULT_REFRESH,
    DOMAIN,
    KIND_IN1508,
    MAX_REFRESH,
    MIN_REFRESH,
)
from .entity import ExtronConfigEntry, ExtronData, ScalerData, is_scaler
from .in1508 import Extron1508Client
from .protocol import ExtronClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA: vol.Schema = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


# The IN1508 has no ties, presets, names or groups, so it uses fewer platforms.
SCALER_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def refresh_seconds(options: Mapping[str, Any]) -> int:
    """Return the saved refresh interval, kept within the allowed range."""
    try:
        seconds = int(options.get(CONF_REFRESH_SECONDS, DEFAULT_REFRESH))
    except (TypeError, ValueError):
        seconds = DEFAULT_REFRESH
    return max(MIN_REFRESH, min(MAX_REFRESH, seconds))


def unique_names(names: list[str]) -> list[str]:
    """Make names unique (the unit lets two inputs share a name): the second becomes "Name (2)"."""
    seen: dict[str, int] = {}
    result = []
    for name in names:
        key = name.casefold()
        seen[key] = seen.get(key, 0) + 1
        result.append(name if seen[key] == 1 else f"{name} ({seen[key]})")
    return result


def effective_names(saved: list[str] | None, defaults: list[str]) -> list[str]:
    """Use saved names where customised; defaults for blanks."""
    saved = saved or []
    return [
        saved[i] if i < len(saved) and saved[i] else default
        for i, default in enumerate(defaults)
    ]


def strip_video(name: str) -> str:
    """Return a name without a trailing " Video", for things that cover both layers."""
    return name.removesuffix(" Video")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Nothing to do globally; each unit is set up from its entry."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ExtronConfigEntry) -> bool:
    """Set up a unit from a config entry."""
    data = entry.data
    if data.get(CONF_KIND) == KIND_IN1508:
        return await _setup_scaler(hass, entry)
    inputs, outputs = int(data[CONF_INPUTS]), int(data[CONF_OUTPUTS])
    client = ExtronClient(
        data[CONF_HOST],
        int(data.get(CONF_PORT, DEFAULT_PORT)),
        inputs,
        outputs,
        password=data.get(CONF_PASSWORD, "") or "",
        read_only=bool(data.get(CONF_READ_ONLY, False)),
        audio=bool(data.get(CONF_AUDIO, True)),
        refresh_interval=refresh_seconds(entry.options),
    )
    await client.async_start()
    # The names come from the unit, so wait for the first full read. A unit that is off
    # makes Home Assistant retry later rather than create entities with the wrong names.
    if not await client.async_wait_ready(20):
        problem = client.last_error or "not answering"
        await client.async_stop()
        if "password" in problem.lower():
            raise ConfigEntryAuthFailed(problem)
        raise ConfigEntryNotReady(f"Extron unit at {data[CONF_HOST]} is {problem}")

    in_base = unique_names(
        [client.in_names.get(i, f"Input {i:02d}") for i in range(1, inputs + 1)]
    )
    out_base = unique_names(
        [client.out_names.get(o, f"Output {o:02d}") for o in range(1, outputs + 1)]
    )
    options = entry.options
    in_video = effective_names(
        options.get(CONF_INPUT_NAMES), [f"{n} Video" for n in in_base]
    )
    in_audio = effective_names(
        options.get(CONF_AUDIO_INPUT_NAMES), [f"{n} Audio" for n in in_base]
    )
    out_video = effective_names(
        options.get(CONF_OUTPUT_NAMES), [f"{n} Video" for n in out_base]
    )
    out_audio = effective_names(
        options.get(CONF_AUDIO_OUTPUT_NAMES), [f"{n} Audio" for n in out_base]
    )
    _enable_reset_mutes(hass, entry)
    entry.runtime_data = ExtronData(
        client=client,
        model_name=data.get(CONF_MODEL_NAME) or f"Matrix {inputs}x{outputs}",
        unit_name=data.get(CONF_UNIT_NAME, ""),
        in_base=in_base,
        in_video=in_video,
        in_audio=in_audio,
        out_base=out_base,
        out_video=out_video,
        out_audio=out_audio,
        read_only=client.read_only,
        applied_options=_without_refresh(options),
        show_advanced=bool(options.get(CONF_SHOW_ADVANCED, False)),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _setup_scaler(hass: HomeAssistant, entry: ExtronConfigEntry) -> bool:
    """Set up an IN1508 scaling switcher."""
    data = entry.data
    client = Extron1508Client(
        data[CONF_HOST],
        int(data.get(CONF_PORT, DEFAULT_PORT)),
        read_only=bool(data.get(CONF_READ_ONLY, False)),
        refresh_interval=refresh_seconds(entry.options),
    )
    await client.async_start()
    if not await client.async_wait_ready(20):
        problem = client.last_error or "not answering"
        await client.async_stop()
        raise ConfigEntryNotReady(f"Extron unit at {data[CONF_HOST]} is {problem}")
    entry.runtime_data = ScalerData(
        client=client,
        model_name=data.get(CONF_MODEL_NAME) or "IN1508",
        unit_name=data.get(CONF_UNIT_NAME, ""),
        read_only=client.read_only,
        applied_options=_without_refresh(entry.options),
    )
    await hass.config_entries.async_forward_entry_setups(entry, SCALER_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


def _without_refresh(options: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in options.items() if k != CONF_REFRESH_SECONDS}


@callback
def _enable_reset_mutes(hass: HomeAssistant, entry: ExtronConfigEntry) -> None:
    """From 0.2.0 "Reset all mutes" is on by default: switch it on where the integration had left it off.

    A button the user turned off themselves is left alone.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_reset_mutes"
    )
    item = registry.async_get(entity_id) if entity_id else None
    if item is not None and item.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
        registry.async_update_entity(item.entity_id, disabled_by=None)


# Entities that are off by default: the group selects and the save and clear preset buttons.
ADVANCED_ID = re.compile(r"_(?:in|out)_\d+_group$|_preset_\d+_(?:save|clear)$")


@callback
def sync_advanced(hass: HomeAssistant, entry: ExtronConfigEntry) -> None:
    """Switch the advanced entities on or off to match the option (only when the option changed)."""
    registry = er.async_get(hass)
    show = bool(entry.options.get(CONF_SHOW_ADVANCED, False))
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not ADVANCED_ID.search(item.unique_id):
            continue
        if show and item.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(item.entity_id, disabled_by=None)
        elif not show and item.disabled_by is None:
            registry.async_update_entity(
                item.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )


async def _reload(hass: HomeAssistant, entry: ExtronConfigEntry) -> None:
    """Reload after options or reconfiguration change."""
    data = getattr(entry, "runtime_data", None)
    if (
        data is not None
        and not is_scaler(entry)
        and data.show_advanced != bool(
            entry.options.get(CONF_SHOW_ADVANCED, False)
        )
    ):
        sync_advanced(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ExtronConfigEntry) -> bool:
    """Unload an entry."""
    platforms = SCALER_PLATFORMS if is_scaler(entry) else PLATFORMS
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        await entry.runtime_data.client.async_stop()
    return unloaded
