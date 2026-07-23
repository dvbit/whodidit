"""Config flow for Whodidit.

Spec ref: "Config flow": picker entità (esclude già tracciate),
1 config entry = 1 sensore = 1 device page.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback, split_entity_id
from homeassistant.helpers import selector

from .const import CONF_TRACKED_ENTITY_ID, DOMAIN, SUPPORTED_DOMAINS


class WhoditFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Whodidit."""

    VERSION = 1

    @callback
    def _already_tracked_entity_ids(self) -> set[str]:
        """Entities already monitored by an existing Whodidit entry."""
        return {
            entry.data[CONF_TRACKED_ENTITY_ID]
            for entry in self._async_current_entries()
            if CONF_TRACKED_ENTITY_ID in entry.data
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial (and only) step: pick the entity to monitor."""
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
                title = state.attributes.get("friendly_name", entity_id) if state else entity_id
                return self.async_create_entry(
                    title=title, data={CONF_TRACKED_ENTITY_ID: entity_id}
                )

        # Already-tracked entities are hidden from the picker (spec:
        # "Already-tracked entities are automatically hidden from the
        # picker to prevent duplicates").
        #
        # NOTE: EntitySelectorConfig is a TypedDict validated by voluptuous
        # inside the frontend selector schema. Passing a key with value
        # `None` triggers a schema validation error (returned as HTTP 400
        # by the frontend flow endpoint). Build the kwargs dict
        # dynamically and only include `exclude_entities` when the list
        # is non-empty.
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
