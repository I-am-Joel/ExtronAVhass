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
from .entity import ExtronConfigEntry

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
