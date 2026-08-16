"""JavaScript module registration for the Whodidit card.

Spec ref: v1.2.0 - the custom Lovelace card ships inside the integration
and is auto-registered (no separate HACS frontend install required).

Verified pattern (HA developer docs + KipK community guide):
  - serve the JS via hass.http.async_register_static_paths([StaticPathConfig])
    (the async, non-blocking replacement for register_static_path)
  - in Lovelace *storage* mode, add the module to lovelace resources so the
    card loads automatically; in YAML mode the user adds the resource
    manually (the static path is still served either way).
Registration happens once in async_setup (not per config entry).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import JSMODULES, URL_BASE

_LOGGER = logging.getLogger(__name__)


class JSModuleRegistration:
    """Registers the Whodidit card JavaScript module in Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.lovelace = self.hass.data.get("lovelace")

    async def async_register(self) -> None:
        """Register the static path and (storage mode) the Lovelace resource."""
        await self._async_register_path()
        # `mode` on modern HA, `resource_mode` on older builds; default yaml.
        mode = getattr(
            self.lovelace, "mode", getattr(self.lovelace, "resource_mode", "yaml")
        )
        if mode == "storage":
            await self._async_wait_for_lovelace_resources()

    async def _async_register_path(self) -> None:
        """Serve the frontend/ directory under URL_BASE."""
        folder = str(Path(__file__).parent)
        try:
            # Modern API (HA 2024.7+): async, non-blocking, batch.
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(URL_BASE, folder, False)]
            )
            _LOGGER.debug("Whodidit: static path registered %s", URL_BASE)
        except RuntimeError:
            # Already registered (e.g. reload) - safe to ignore.
            _LOGGER.debug("Whodidit: static path already registered %s", URL_BASE)
        except AttributeError:
            # Older HA without async_register_static_paths - fall back to
            # the legacy synchronous registration.
            self.hass.http.register_static_path(URL_BASE, folder, False)
            _LOGGER.debug("Whodidit: static path registered (legacy) %s", URL_BASE)

    async def _async_wait_for_lovelace_resources(self) -> None:
        """Wait until Lovelace resources are loaded, then register modules."""

        async def _check_loaded(_now: Any) -> None:
            if getattr(self.lovelace.resources, "loaded", False):
                await self._async_register_modules()
            else:
                _LOGGER.debug("Whodidit: Lovelace resources not loaded, retrying in 5s")
                async_call_later(self.hass, 5, _check_loaded)

        await _check_loaded(0)

    async def _async_register_modules(self) -> None:
        """Create or update the Lovelace resource entries for the card."""
        existing = [
            r
            for r in self.lovelace.resources.async_items()
            if r["url"].startswith(URL_BASE)
        ]

        for module in JSMODULES:
            url = f"{URL_BASE}/{module['filename']}"
            registered = False
            for resource in existing:
                if resource["url"].split("?")[0] == url:
                    registered = True
                    if self._version_of(resource["url"]) != module["version"]:
                        _LOGGER.info(
                            "Whodidit: updating card resource to v%s", module["version"]
                        )
                        await self.lovelace.resources.async_update_item(
                            resource["id"],
                            {"res_type": "module", "url": f"{url}?v={module['version']}"},
                        )
                    break
            if not registered:
                _LOGGER.info("Whodidit: registering card resource v%s", module["version"])
                await self.lovelace.resources.async_create_item(
                    {"res_type": "module", "url": f"{url}?v={module['version']}"}
                )

    @staticmethod
    def _version_of(url: str) -> str:
        parts = url.split("?")
        if len(parts) > 1 and parts[1].startswith("v="):
            return parts[1][2:]
        return "0"
