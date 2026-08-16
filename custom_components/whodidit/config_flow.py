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
    CONF_TRACKED_ENTITY_ID,
    DEFAULT_CLICK_WINDOW_SECONDS,
    DOMAIN,
    SUPPORTED_DOMAINS,
)


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Options schema (v2.2.0): only the click-detection window.

    The physical-interaction binary sensor is always enabled and cannot be
    turned off - so there is no enable toggle and no config-flow step 2.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_CLICK_WINDOW_SECONDS,
                default=defaults.get(
                    CONF_CLICK_WINDOW_SECONDS, DEFAULT_CLICK_WINDOW_SECONDS
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _normalize_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """Coerce the click window to int."""
    out = dict(user_input)
    if CONF_CLICK_WINDOW_SECONDS in out and out[CONF_CLICK_WINDOW_SECONDS] is not None:
        out[CONF_CLICK_WINDOW_SECONDS] = int(out[CONF_CLICK_WINDOW_SECONDS])
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
                state = self.hass.states.get(entity_id)
                title = (
                    state.attributes.get("friendly_name", entity_id)
                    if state
                    else entity_id
                )
                # Binary sensor is always enabled; only the click window is
                # configurable (later, via Options). Create the entry
                # directly - no second step.
                return self.async_create_entry(
                    title=title,
                    data={CONF_TRACKED_ENTITY_ID: entity_id},
                    options={CONF_CLICK_WINDOW_SECONDS: DEFAULT_CLICK_WINDOW_SECONDS},
                )

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


class WhoditOptionsFlowHandler(OptionsFlow):
    """Handle Whodidit options - editing the click window."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=_normalize_options(user_input))
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
