"""Preset buttons: recall, and (off by default) save and clear, which ask for a second press to overwrite."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import CONFIRM_SECONDS, DOMAIN
from .entity import ExtronConfigEntry, ExtronEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the preset buttons."""
    client = entry.runtime_data.client
    entities: list[ButtonEntity] = []
    entities.append(ExtronSyncClockButton(entry))
    for preset in range(1, client.preset_count + 1):
        entities.append(ExtronRecallButton(entry, preset))
        entities.append(ExtronSaveButton(entry, preset))
        entities.append(ExtronClearButton(entry, preset))
    async_add_entities(entities)


class ExtronRecallButton(ExtronEntity, ButtonEntity):
    """Recall a saved preset (its ties; volume, level and mutes are not part of a preset)."""

    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, entry: ExtronConfigEntry, preset: int) -> None:
        """Initialise."""
        super().__init__(entry, f"preset_{preset}_recall")
        self._preset = preset
        self._attr_name = f"Recall preset {preset}"

    @property
    def available(self) -> bool:
        """Only a saved preset can be recalled."""
        info = self._client.presets.get(self._preset)
        return super().available and bool(info and info.saved)

    async def async_press(self) -> None:
        """Recall."""
        self._ensure_writable()
        await self._call(self._client.async_recall_preset(self._preset))


class _ConfirmButton(ExtronEntity, ButtonEntity):
    """A button that changes a preset and needs a second press within a few seconds if it holds something."""

    _attr_entity_category = EntityCategory.CONFIG
    _verb = ""
    _verb_past = ""

    def __init__(self, entry: ExtronConfigEntry, preset: int, key: str) -> None:
        """Initialise."""
        super().__init__(entry, f"preset_{preset}_{key}")
        self._preset = preset
        self._armed = False
        self._attr_entity_registry_enabled_default = self._data.show_advanced
        self._cancel: CALLBACK_TYPE | None = None
        self._notification_id = f"{DOMAIN}_{entry.entry_id}_{key}_{preset}"

    async def async_will_remove_from_hass(self) -> None:
        """Drop a pending confirmation timer when the entity goes away."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show whether the next press will go ahead."""
        return {"armed": self._armed, "preset": self._preset}

    def _action(self) -> Awaitable[None]:
        raise NotImplementedError

    @callback
    def _disarm(self, _now: datetime | None = None) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        if self._armed:
            self._armed = False
            persistent_notification.async_dismiss(self.hass, self._notification_id)
            self.async_write_ha_state()

    async def async_press(self) -> None:
        """Act at once on an empty preset; on a saved one, arm first and act on the second press."""
        self._ensure_writable()
        info = self._client.presets.get(self._preset)
        if info is None:
            return
        if info.saved and not self._armed:
            self._armed = True
            self._cancel = async_call_later(self.hass, CONFIRM_SECONDS, self._disarm)
            label = f" ({info.name})" if info.name else ""
            persistent_notification.async_create(
                self.hass,
                f"Preset {self._preset}{label} on {self._entry.title} is saved. "
                f"Press {self._attr_name!r} again within {CONFIRM_SECONDS} seconds to {self._verb} it.",
                title="Extron: confirm",
                notification_id=self._notification_id,
            )
            self.async_write_ha_state()
            return
        if not info.saved and self._skip_when_empty:
            return
        self._disarm()
        await self._call(self._action())

    _skip_when_empty = False


class ExtronSaveButton(_ConfirmButton):
    """Save the current ties into a preset (overwrites after a second press if it is already saved)."""

    _verb = "overwrite"
    _attr_icon = "mdi:content-save"

    def __init__(self, entry: ExtronConfigEntry, preset: int) -> None:
        """Initialise."""
        super().__init__(entry, preset, "save")
        self._attr_name = f"Save preset {preset}"

    def _action(self) -> Awaitable[None]:
        return self._client.async_save_preset(self._preset)


class ExtronClearButton(_ConfirmButton):
    """Erase a saved preset (after a second press)."""

    _verb = "erase"
    _attr_icon = "mdi:delete-outline"
    _skip_when_empty = True

    def __init__(self, entry: ExtronConfigEntry, preset: int) -> None:
        """Initialise."""
        super().__init__(entry, preset, "clear")
        self._attr_name = f"Clear preset {preset}"

    def _action(self) -> Awaitable[None]:
        return self._client.async_clear_preset(self._preset)


class ExtronSyncClockButton(ExtronEntity, ButtonEntity):
    """Set the unit's clock to Home Assistant's local time."""

    _attr_name = "Sync clock"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, entry: ExtronConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "sync_clock")

    async def async_press(self) -> None:
        """Set the clock."""
        self._ensure_writable()
        now = dt_util.now().replace(tzinfo=None, microsecond=0)
        await self._call(self._client.async_set_clock(now))
