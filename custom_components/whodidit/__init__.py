"""The Whodidit integration.

Spec ref: v1.0 - single shared TriggerCache across all entries.
Spec ref: v1.1.0 - per-entry WhoditEntryRuntime + global service
`whodidit.reset_physical_interaction` + Options Flow update listener that
reloads the entry so binary_sensor picks up new options.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .cache import TriggerCache
from .const import (
    CONF_TRACKED_ENTITY_ID,
    DOMAIN,
    PLATFORMS,
    SERVICE_RESET_PHYSICAL,
    SERVICE_UPDATE_OPTIONS,
)
from .frontend import JSModuleRegistration
from .runtime import WhoditEntryRuntime

_LOGGER = logging.getLogger(__name__)

# Service schema (spec: "Servizio" - target can be either the tracked
# entity or the binary sensor). We accept a plain entity_id list rather
# than a target selector because we need to match against two different
# entity IDs per entry.
_SERVICE_SCHEMA = vol.Schema(
    {vol.Required("entity_id"): vol.All(cv.ensure_list, [cv.entity_id])}
)

# v1.2.0: settings-dialog service. `entity_id` is the tracked entity or the
# whodidit sensor; `options` is a partial options dict merged into the entry.
_UPDATE_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("options"): dict,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the frontend card once, at integration load.

    Spec ref v1.2.0 / community guide: card registration must happen in
    async_setup (once), not async_setup_entry (per entry).
    """

    async def _register_frontend(_event=None) -> None:
        await JSModuleRegistration(hass).async_register()

    if hass.state == CoreState.running:
        await _register_frontend()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_frontend)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Whodidit from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    # First-entry-only global setup.
    if "cache" not in domain_data:
        cache = TriggerCache(hass)
        cache.async_start()
        domain_data["cache"] = cache
        domain_data["entries_runtime"] = {}
        domain_data["entries"] = 0
        _register_service(hass)
        _register_update_options_service(hass)
        _LOGGER.debug("Whodidit shared trigger cache started and services registered")

    domain_data["entries"] += 1

    # Per-entry runtime (spec: bus interno sensor <-> binary_sensor).
    runtime = WhoditEntryRuntime(entry.data[CONF_TRACKED_ENTITY_ID])
    domain_data["entries_runtime"][entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever options change so the binary_sensor
    # platform re-evaluates `CONF_ENABLE_PHYSICAL` (adding/removing the
    # entity as required) and picks up new timing/sensor settings.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    domain_data = hass.data.get(DOMAIN, {})
    domain_data.get("entries_runtime", {}).pop(entry.entry_id, None)
    domain_data["entries"] = max(0, domain_data.get("entries", 1) - 1)

    if domain_data["entries"] == 0:
        cache: TriggerCache | None = domain_data.get("cache")
        if cache is not None:
            cache.async_stop()
        if hass.services.has_service(DOMAIN, SERVICE_RESET_PHYSICAL):
            hass.services.async_remove(DOMAIN, SERVICE_RESET_PHYSICAL)
        if hass.services.has_service(DOMAIN, SERVICE_UPDATE_OPTIONS):
            hass.services.async_remove(DOMAIN, SERVICE_UPDATE_OPTIONS)
        hass.data.pop(DOMAIN, None)
        _LOGGER.debug("Whodidit shared trigger cache stopped (last entry removed)")

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    _LOGGER.debug("Whodidit: options changed for entry %s, reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


# ----------------------------------------------------------------------
# Service: whodidit.reset_physical_interaction
# ----------------------------------------------------------------------
def _register_service(hass: HomeAssistant) -> None:
    """Register the global reset service (spec: "Servizio")."""

    async def _handle_reset(call: ServiceCall) -> None:
        requested: list[str] = call.data["entity_id"]
        ent_reg = er.async_get(hass)
        entries_runtime: dict = hass.data.get(DOMAIN, {}).get("entries_runtime", {})
        matched = 0

        # For each requested entity_id, find the config entry it belongs
        # to. Two acceptance modes (spec): either the *tracked* entity or
        # the *binary_sensor* entity itself.
        for eid in requested:
            for entry_id, runtime in entries_runtime.items():
                if eid == runtime.tracked_entity_id:
                    _reset_binary_for_entry(hass, ent_reg, entry_id)
                    matched += 1
                    break
                bs_entity = ent_reg.async_get_entity_id(
                    "binary_sensor", DOMAIN, f"{entry_id}_physical_interaction"
                )
                if bs_entity == eid:
                    _reset_binary_for_entry(hass, ent_reg, entry_id)
                    matched += 1
                    break

        if matched == 0:
            raise ServiceValidationError(
                f"No Whodidit physical-interaction sensor matches {requested}"
            )

    hass.services.async_register(
        DOMAIN, SERVICE_RESET_PHYSICAL, _handle_reset, schema=_SERVICE_SCHEMA
    )


def _reset_binary_for_entry(hass: HomeAssistant, ent_reg, entry_id: str) -> None:
    """Locate the live binary_sensor entity for `entry_id` and force reset.

    Resolves the platform entity via the registry (unique_id ->
    entity_id) and then reaches the running instance through the entity
    component - HA's documented way to get an `Entity` instance from an
    `entity_id`.
    """
    bs_entity_id = ent_reg.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry_id}_physical_interaction"
    )
    if bs_entity_id is None:
        _LOGGER.debug(
            "Whodidit: no live binary_sensor for entry %s (feature disabled?)",
            entry_id,
        )
        return

    from homeassistant.helpers.entity_component import EntityComponent

    component: EntityComponent | None = hass.data.get("entity_components", {}).get(
        "binary_sensor"
    )
    if component is None:
        _LOGGER.warning("Whodidit: binary_sensor component not available")
        return
    entity = component.get_entity(bs_entity_id)
    if entity is None:
        _LOGGER.debug("Whodidit: binary_sensor %s not loaded", bs_entity_id)
        return
    if hasattr(entity, "async_force_reset"):
        entity.async_force_reset()


# ----------------------------------------------------------------------
# Service: whodidit.update_options  (used by the card settings dialog)
# ----------------------------------------------------------------------
def _register_update_options_service(hass: HomeAssistant) -> None:
    """Register the options-update service (spec v1.2.0)."""

    async def _handle_update(call: ServiceCall) -> None:
        eid: str = call.data["entity_id"]
        new_options: dict = call.data["options"]

        entry = _find_entry_for_entity(hass, eid)
        if entry is None:
            raise ServiceValidationError(
                f"No Whodidit config entry matches entity {eid}"
            )

        # Merge partial options over the existing ones so the dialog can
        # send only the fields it manages. Options flow already normalises
        # types on next reload; we coerce the known numeric keys here too.
        merged = dict(entry.options)
        merged.update(new_options)
        for key in ("reset_lapse_seconds", "click_window_seconds"):
            if key in merged and merged[key] is not None:
                try:
                    merged[key] = int(merged[key])
                except (TypeError, ValueError):
                    pass
        # Strip emptied optional sensors.
        for key in ("motion_sensor_entity_id", "occupancy_sensor_entity_id"):
            if key in merged and not merged[key]:
                merged.pop(key)

        # Updating options triggers the entry's update listener, which
        # reloads the entry (add/remove the binary sensor as needed).
        hass.config_entries.async_update_entry(entry, options=merged)
        _LOGGER.debug("Whodidit: options updated via service for %s", entry.entry_id)

    hass.services.async_register(
        DOMAIN, SERVICE_UPDATE_OPTIONS, _handle_update, schema=_UPDATE_OPTIONS_SCHEMA
    )


def _find_entry_for_entity(hass: HomeAssistant, entity_id: str):
    """Resolve a config entry from either the tracked entity id, the
    whodidit sensor, or the physical-interaction binary sensor."""
    ent_reg = er.async_get(hass)

    # Direct: the whodidit sensor / binary sensor is a registry entity that
    # carries its config_entry_id.
    reg_entity = ent_reg.async_get(entity_id)
    if reg_entity is not None and reg_entity.config_entry_id:
        entry = hass.config_entries.async_get_entry(reg_entity.config_entry_id)
        if entry is not None and entry.domain == DOMAIN:
            return entry

    # Indirect: the id is the *tracked* entity - match against entry data.
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_TRACKED_ENTITY_ID) == entity_id:
            return entry
    return None
