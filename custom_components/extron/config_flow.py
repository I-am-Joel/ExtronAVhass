"""Config, reconfigure, re-authentication and options flows for the Extron integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import refresh_seconds
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
    CONF_SHOW_ADVANCED,
    CONF_UNIT_NAME,
    DEFAULT_PORT,
    DOMAIN,
    MAX_REFRESH,
    MIN_REFRESH,
    SOURCE_MIXED,
    SOURCE_NONE,
)
from .entity import ExtronData
from .protocol import (
    ExtronAuthError,
    ExtronConnectionError,
    ExtronError,
    ExtronTimeoutError,
    async_probe,
)

CONF_NAME = "name"


def _model_name(info: Mapping[str, Any]) -> str:
    product = info.get("product", "Matrix switcher")
    part = info.get("part_number")
    return f"{product} {info['inputs']}x{info['outputs']}" + (
        f" ({part})" if part else ""
    )


class ExtronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup, reconfiguration and re-authentication."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ExtronOptionsFlow:
        """Return the options flow."""
        return ExtronOptionsFlow()

    async def _probe(
        self, user_input: Mapping[str, Any], errors: dict[str, str]
    ) -> dict[str, Any] | None:
        """Connect to the unit; fill ``errors`` and return None if it cannot be used."""
        try:
            return await async_probe(
                user_input[CONF_HOST].strip(),
                int(user_input.get(CONF_PORT, DEFAULT_PORT)),
                user_input.get(CONF_PASSWORD, "") or "",
            )
        except ExtronAuthError:
            errors["base"] = "invalid_auth"
        except (ExtronConnectionError, ExtronTimeoutError):
            errors["base"] = "cannot_connect"
        except ExtronError:
            errors["base"] = "not_extron"
        return None

    def _schema(self, defaults: Mapping[str, Any], *, with_name: bool) -> vol.Schema:
        fields: dict[Any, Any] = {}
        if with_name:
            fields[
                vol.Required(
                    CONF_NAME, default=defaults.get(CONF_NAME, "Extron Matrix")
                )
            ] = TextSelector()
        fields[
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED))
        ] = TextSelector()
        fields[
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT))
        ] = NumberSelector(
            NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
        )
        fields[vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, ""))] = (
            TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        )
        fields[
            vol.Required(CONF_READ_ONLY, default=defaults.get(CONF_READ_ONLY, False))
        ] = BooleanSelector()
        return vol.Schema(fields)

    def _entry_data(
        self, user_input: Mapping[str, Any], info: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            CONF_HOST: user_input[CONF_HOST].strip(),
            CONF_PORT: int(user_input[CONF_PORT]),
            CONF_PASSWORD: user_input.get(CONF_PASSWORD, "") or "",
            CONF_READ_ONLY: bool(user_input.get(CONF_READ_ONLY, False)),
            CONF_INPUTS: info["inputs"],
            CONF_OUTPUTS: info["outputs"],
            CONF_MODEL_NAME: _model_name(info),
            CONF_UNIT_NAME: info.get("unit_name", ""),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info = await self._probe(user_input, errors)
            if info is not None:
                await self.async_set_unique_id(
                    info.get("unit_name")
                    or f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME].strip() or "Extron Matrix",
                    data=self._entry_data(user_input, info),
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(user_input or {}, with_name=True),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address, password or read-only setting of an existing unit."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            info = await self._probe(user_input, errors)
            if info is not None:
                await self.async_set_unique_id(info.get("unit_name") or entry.unique_id)
                self._abort_if_unique_id_mismatch(reason="different_unit")
                return self.async_update_reload_and_abort(
                    entry, data_updates=self._entry_data(user_input, info)
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(user_input or entry.data, with_name=False),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The unit wants a password that is missing or wrong."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {
                **entry.data,
                CONF_PASSWORD: user_input.get(CONF_PASSWORD, "") or "",
            }
            info = await self._probe(candidate, errors)
            if info is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: candidate[CONF_PASSWORD]}
                )
        schema = vol.Schema(
            {
                vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )


def _parse_names(text: str, defaults: list[str]) -> tuple[list[str], str | None]:
    """One name per line; a blank line keeps the default. Returns (names, error key)."""
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    while lines and lines[-1] == "" and len(lines) > len(defaults):
        lines.pop()
    if len(lines) != len(defaults):
        return defaults, "wrong_count"
    names = [line or default for line, default in zip(lines, defaults, strict=True)]
    if len({n.casefold() for n in names}) != len(names):
        return names, "duplicate_names"
    if {n.casefold() for n in names} & {
        SOURCE_NONE.casefold(),
        SOURCE_MIXED.casefold(),
    }:
        return names, "reserved_name"
    return names, None


class ExtronOptionsFlow(OptionsFlow):
    """Edit the names and the refresh interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show every name, one per line."""
        entry = self.config_entry
        data: ExtronData | None = getattr(entry, "runtime_data", None)
        if data is None:
            return self.async_abort(reason="unit_unavailable")
        specs = (
            (CONF_INPUT_NAMES, data.in_video, [f"{n} Video" for n in data.in_base]),
            (
                CONF_AUDIO_INPUT_NAMES,
                data.in_audio,
                [f"{n} Audio" for n in data.in_base],
            ),
            (CONF_OUTPUT_NAMES, data.out_video, [f"{n} Video" for n in data.out_base]),
            (
                CONF_AUDIO_OUTPUT_NAMES,
                data.out_audio,
                [f"{n} Audio" for n in data.out_base],
            ),
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            saved = {**entry.options}
            for key, _current, defaults in specs:
                names, error = _parse_names(user_input[key], defaults)
                if error:
                    errors[key] = error
                else:
                    saved[key] = [
                        n if n != d else ""
                        for n, d in zip(names, defaults, strict=True)
                    ]
            if not errors:
                saved[CONF_REFRESH_SECONDS] = int(user_input[CONF_REFRESH_SECONDS])
                saved[CONF_SHOW_ADVANCED] = bool(user_input[CONF_SHOW_ADVANCED])
                return self.async_create_entry(data=saved)
        typed = user_input or {}
        multiline = TextSelector(TextSelectorConfig(multiline=True))
        fields: dict[Any, Any] = {}
        for key, current, _defaults in specs:
            fields[vol.Required(key, default=typed.get(key, "\n".join(current)))] = (
                multiline
            )
        fields[
            vol.Required(
                CONF_REFRESH_SECONDS,
                default=typed.get(CONF_REFRESH_SECONDS, refresh_seconds(entry.options)),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=MIN_REFRESH, max=MAX_REFRESH, step=1, mode=NumberSelectorMode.BOX
            )
        )
        fields[
            vol.Required(
                CONF_SHOW_ADVANCED,
                default=typed.get(
                    CONF_SHOW_ADVANCED, bool(entry.options.get(CONF_SHOW_ADVANCED))
                ),
            )
        ] = BooleanSelector()
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(fields), errors=errors
        )
