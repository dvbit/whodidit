"""The Whodidit integration.

Spec ref: "Standard project workflow" - entry point wiring config entries
to the sensor platform and to the single shared TriggerCache instance
(spec: "Architecture" - one shared listener set for all tracked entities).
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .cache import TriggerCache
from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Whodidit from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if "cache" not in domain_data:
        cache = TriggerCache(hass)
        cache.async_start()
        domain_data["cache"] = cache
        domain_data["entries"] = 0
        _LOGGER.debug("Whodidit shared trigger cache started")

    domain_data["entries"] += 1

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data["entries"] = max(0, domain_data.get("entries", 1) - 1)
        if domain_data["entries"] == 0:
            cache: TriggerCache | None = domain_data.get("cache")
            if cache is not None:
                cache.async_stop()
            hass.data.pop(DOMAIN, None)
            _LOGGER.debug("Whodidit shared trigger cache stopped (last entry removed)")
    return unload_ok
