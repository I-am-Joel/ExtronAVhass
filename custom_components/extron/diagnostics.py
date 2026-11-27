"""Diagnostics download, with addresses, passwords and personal names removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AUDIO_INPUT_NAMES,
    CONF_AUDIO_OUTPUT_NAMES,
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_OUTPUT_NAMES,
    CONF_PASSWORD,
    CONF_UNIT_NAME,
)
from .entity import ExtronConfigEntry, is_scaler

TO_REDACT = {
    CONF_HOST,
    CONF_PASSWORD,
    CONF_UNIT_NAME,
    CONF_AUDIO_INPUT_NAMES,
    CONF_INPUT_NAMES,
    CONF_OUTPUT_NAMES,
    CONF_AUDIO_OUTPUT_NAMES,
    "unique_id",
    "title",
    "name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ExtronConfigEntry
) -> dict[str, Any]:
    """Return what is useful for fault-finding."""
    client = entry.runtime_data.client
    if is_scaler(entry):
        return {
            "entry": async_redact_data(
                {
                    "title": entry.title,
                    "unique_id": entry.unique_id,
                    "data": dict(entry.data),
                    "options": dict(entry.options),
                },
                TO_REDACT,
            ),
            "model": entry.runtime_data.model_name,
            "banner": dict(client.banner),
            "state": {
                "available": client.available,
                "read_only": client.read_only,
                "input": client.input,
                "pip_input": client.pip_input,
                "standard": client.standard,
                "video_mute": client.video_mute,
                "audio_mute": client.audio_mute,
                "freeze": client.freeze,
                "volume": client.volume,
                "pip": client.pip,
                "pip_size": client.pip_size,
                "resolution": client.resolution,
                "rate": client.rate,
                "pictures": {
                    str(n): dict(p.values) for n, p in sorted(client.pictures.items())
                },
                "last_reply": client.last_reply.isoformat() if client.last_reply else None,
                "last_error": client.last_error,
                "last_error_time": client.last_error_time.isoformat()
                if client.last_error_time
                else None,
            },
        }
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "model": entry.runtime_data.model_name,
        "banner": dict(client.banner),
        "status": dict(client.status),
        "info": {
            k: v
            for k, v in client.info.items()
            if k not in ("ip", "subnet", "gateway", "mac", "unit_name")
        },
        "login": client.privilege,
        "state": {
            "available": client.available,
            "read_only": client.read_only,
            "lock_mode": client.lock_mode,
            "video_ties": dict(sorted(client.video.items())),
            "audio_ties": dict(sorted(client.audio.items())),
            "video_mutes": dict(sorted(client.video_mute.items())),
            "audio_mutes": dict(sorted(client.audio_mute.items())),
            "volumes": dict(sorted(client.volume.items())),
            "levels": dict(sorted(client.gain.items())),
            "groups_in": client.groups_in,
            "groups_out": client.groups_out,
            "presets": {str(n): p.saved for n, p in sorted(client.presets.items())},
            "last_reply": client.last_reply.isoformat() if client.last_reply else None,
            "last_error": client.last_error,
            "last_error_time": client.last_error_time.isoformat()
            if client.last_error_time
            else None,
        },
    }
