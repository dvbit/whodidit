"""Diagnostics support for Whodidit.

Spec ref: "Persistenza & lifecycle" -> diagnostics download.
Documented entry point: `async_get_config_entry_diagnostics`.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_TRACKED_ENTITY_ID, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for this config entry."""
    tracked_entity_id = entry.data.get(CONF_TRACKED_ENTITY_ID)
    ent_reg = er.async_get(hass)
    sensor_entry = next(
        (e for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)), None
    )

    state = None
    if sensor_entry is not None:
        entity_state = hass.states.get(sensor_entry.entity_id)
        if entity_state is not None:
            state = {"state": entity_state.state, "attributes": dict(entity_state.attributes)}

    domain_data = hass.data.get(DOMAIN, {})
    cache = domain_data.get("cache")

    return {
        "tracked_entity_id": tracked_entity_id,
        "sensor_state": state,
        "shared_cache": {
            "total_context_entries": len(cache._context_cache) if cache else None,
            "total_resolved_users": len(cache._user_cache) if cache else None,
        },
    }
