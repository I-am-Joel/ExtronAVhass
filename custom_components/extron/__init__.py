"""The Extron matrix switcher integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUDIO_INPUT_NAMES,
    CONF_AUDIO_OUTPUT_NAMES,
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_INPUTS,
    CONF_MODEL_NAME,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_READ_ONLY,
    CONF_REFRESH_SECONDS,
    CONF_UNIT_NAME,
    DEFAULT_PORT,
    DEFAULT_REFRESH,
    DOMAIN,
    MAX_REFRESH,
    MIN_REFRESH,
)
from .entity import ExtronConfigEntry, ExtronData
from .protocol import ExtronClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA: vol.Schema = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
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
    inputs, outputs = int(data[CONF_INPUTS]), int(data[CONF_OUTPUTS])
    client = ExtronClient(
        data[CONF_HOST],
        int(data.get(CONF_PORT, DEFAULT_PORT)),
        inputs,
        outputs,
        password=data.get(CONF_PASSWORD, "") or "",
        read_only=bool(data.get(CONF_READ_ONLY, False)),
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
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


def _without_refresh(options: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in options.items() if k != CONF_REFRESH_SECONDS}


async def _reload(hass: HomeAssistant, entry: ExtronConfigEntry) -> None:
    """Reload after options or reconfiguration change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ExtronConfigEntry) -> bool:
    """Unload an entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_stop()
    return unloaded
