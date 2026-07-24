"""Config flow and Options flow for Whodidit.

Spec ref: v1.0 "Config flow: picker entità (esclude già tracciate)".
Spec ref: v1.1.0 - step 2 for optional physical-interaction settings +
Options Flow to edit them later.

Memory note (per user preferences): "OptionsFlow.__init__ must not
overwrite HA's built-in self.config_entry; instantiate without arguments."
This handler defines no custom __init__ - the framework injects
`self.config_entry` for us.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback, split_entity_id
from homeassistant.helpers import selector

from .const import (
    CONF_CLICK_WINDOW_SECONDS,
    CONF_ENABLE_PHYSICAL,
    CONF_MOTION_SENSOR,
    CONF_OCCUPANCY_SENSOR,
    CONF_RESET_LAPSE_SECONDS,
    CONF_TRACKED_ENTITY_ID,
    DEFAULT_CLICK_WINDOW_SECONDS,
    DEFAULT_ENABLE_PHYSICAL,
    DEFAULT_RESET_LAPSE_SECONDS,
    DOMAIN,
    SUPPORTED_DOMAINS,
)


def _binary_sensor_selector() -> selector.EntitySelector:
    """Optional binary_sensor picker (motion / occupancy)."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the (config-step-2 == options-step) schema.

    Optional entity fields are declared with `vol.Optional` so the user can
    submit the form leaving them empty ("no reference sensor" is a
    supported configuration per spec).
    """
    schema_dict: dict = {
        vol.Required(
            CONF_ENABLE_PHYSICAL,
            default=defaults.get(CONF_ENABLE_PHYSICAL, DEFAULT_ENABLE_PHYSICAL),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_RESET_LAPSE_SECONDS,
            default=defaults.get(CONF_RESET_LAPSE_SECONDS, DEFAULT_RESET_LAPSE_SECONDS),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=86400, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_CLICK_WINDOW_SECONDS,
            default=defaults.get(CONF_CLICK_WINDOW_SECONDS, DEFAULT_CLICK_WINDOW_SECONDS),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
    }
    # Optional entity selectors: only pre-fill the default when a value
    # was previously stored - otherwise leave the field blank so the user
    # can submit "nothing selected".
    if defaults.get(CONF_OCCUPANCY_SENSOR):
        schema_dict[
            vol.Optional(CONF_OCCUPANCY_SENSOR, default=defaults[CONF_OCCUPANCY_SENSOR])
        ] = _binary_sensor_selector()
    else:
        schema_dict[vol.Optional(CONF_OCCUPANCY_SENSOR)] = _binary_sensor_selector()

    if defaults.get(CONF_MOTION_SENSOR):
        schema_dict[
            vol.Optional(CONF_MOTION_SENSOR, default=defaults[CONF_MOTION_SENSOR])
        ] = _binary_sensor_selector()
    else:
        schema_dict[vol.Optional(CONF_MOTION_SENSOR)] = _binary_sensor_selector()

    return vol.Schema(schema_dict)


def _normalize_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """Coerce numbers to int and strip empty optional keys."""
    out = dict(user_input)
    for key in (CONF_RESET_LAPSE_SECONDS, CONF_CLICK_WINDOW_SECONDS):
        if key in out and out[key] is not None:
            out[key] = int(out[key])
    for key in (CONF_MOTION_SENSOR, CONF_OCCUPANCY_SENSOR):
        # NumberSelector never returns None; EntitySelector returns None
        # or empty string when the user leaves it blank.
        if key in out and not out[key]:
            out.pop(key)
    return out


class WhoditFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Whodidit."""

    VERSION = 1

    def __init__(self) -> None:
        self._entity_id: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler for an existing entry."""
        # NOTE (memory): no argument passed - HA injects self.config_entry.
        return WhoditOptionsFlowHandler()

    @callback
    def _already_tracked_entity_ids(self) -> set[str]:
        """Entities already monitored by an existing Whodidit entry."""
        return {
            entry.data[CONF_TRACKED_ENTITY_ID]
            for entry in self._async_current_entries()
            if CONF_TRACKED_ENTITY_ID in entry.data
        }

    # ------------------------------------------------------------------
    # Step 1: pick the entity to monitor
    # ------------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_id = user_input[CONF_TRACKED_ENTITY_ID]
            domain, _ = split_entity_id(entity_id)

            if domain not in SUPPORTED_DOMAINS:
                errors["base"] = "unsupported_domain"
            elif entity_id in self._already_tracked_entity_ids():
                errors["base"] = "already_tracked"
            else:
                await self.async_set_unique_id(entity_id)
                self._abort_if_unique_id_configured()
                self._entity_id = entity_id
                return await self.async_step_options()

        excluded = self._already_tracked_entity_ids()
        selector_kwargs: dict = {"domain": sorted(SUPPORTED_DOMAINS)}
        if excluded:
            selector_kwargs["exclude_entities"] = sorted(excluded)

        schema = vol.Schema(
            {
                vol.Required(CONF_TRACKED_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(**selector_kwargs)
                )
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Step 2: physical-interaction options (spec v1.1.0)
    # ------------------------------------------------------------------
    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._entity_id is not None

        if user_input is not None:
            options = _normalize_options(user_input)
            state = self.hass.states.get(self._entity_id)
            title = (
                state.attributes.get("friendly_name", self._entity_id)
                if state
                else self._entity_id
            )
            return self.async_create_entry(
                title=title,
                data={CONF_TRACKED_ENTITY_ID: self._entity_id},
                options=options,
            )

        return self.async_show_form(
            step_id="options",
            data_schema=_options_schema({}),
        )


class WhoditOptionsFlowHandler(OptionsFlow):
    """Handle Whodidit options - editing physical-interaction settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=_normalize_options(user_input))
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
