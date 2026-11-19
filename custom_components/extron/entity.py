"""Shared pieces for Extron entities."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SOURCE_NONE
from .protocol import ExtronClient, ExtronError, ExtronReadOnlyError


@dataclass
class ExtronData:
    """Everything the platforms need, stored on the config entry."""

    client: ExtronClient
    model_name: str
    unit_name: str
    in_base: list[str]
    in_video: list[str]
    in_audio: list[str]
    out_base: list[str]
    out_video: list[str]
    out_audio: list[str]
    read_only: bool = False
    applied_options: dict = field(default_factory=dict)


type ExtronConfigEntry = ConfigEntry[ExtronData]


def source_options(names: list[str]) -> list[str]:
    """Return the selectable sources: every name plus None (untied)."""
    return [*names, SOURCE_NONE]


def number_to_name(names: list[str], number: int | None) -> str | None:
    """Convert an input number to its name (0 = None)."""
    if number is None:
        return None
    if number == 0:
        return SOURCE_NONE
    if 1 <= number <= len(names):
        return names[number - 1]
    return None


def name_to_number(names: list[str], name: str) -> int:
    """Convert a source name to an input number (None = 0)."""
    if name == SOURCE_NONE:
        return 0
    return names.index(name) + 1


class ExtronEntity(Entity):
    """Base class: push-updated, one device per unit."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ExtronConfigEntry, key: str) -> None:
        """Initialise the entity."""
        self._entry = entry
        self._data = entry.runtime_data
        self._client = entry.runtime_data.client
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Extron",
            model=self._data.model_name,
            name=entry.title,
            sw_version=self._client.banner.get("firmware"),
        )

    @property
    def available(self) -> bool:
        """Return whether the unit is answering."""
        return self._client.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes."""
        self.async_on_remove(self._client.add_listener(self.async_write_ha_state))

    def _ensure_writable(self) -> None:
        """Refuse a change on a read-only unit (before anything is armed or sent)."""
        if self._client.read_only:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="read_only"
            )

    async def _call(self, coro: Awaitable[None]) -> None:
        """Run a client command, turning failures into Home Assistant errors."""
        try:
            await coro
        except ExtronReadOnlyError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="read_only"
            ) from err
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except ExtronError as err:
            raise HomeAssistantError(f"Extron command failed: {err}") from err
