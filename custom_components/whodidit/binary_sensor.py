"""Binary sensor platform for Whodidit - Physical Interaction.

Spec ref: v2.4.0 - the MAIN SENSOR is the click-train authority. This
binary sensor is a thin mirror of that state:
- Turns ON on a physical click, OFF when the sensor reports the train
  closed (click window elapsed).
- `click_count` reflects the train position reported by the sensor and
  PERSISTS after OFF (shows the last train size), resetting at the first
  click of the next train.
- Always enabled (no toggle). Manual reset via
  whodidit.reset_physical_interaction forces OFF and clears the count.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_CLICK_WINDOW_SECONDS,
    CONF_TRACKED_ENTITY_ID,
    DEFAULT_CLICK_WINDOW_SECONDS,
    DOMAIN,
)
from .runtime import WhoditEntryRuntime

_LOGGER = logging.getLogger(__name__)

ATTR_CLICK_COUNT = "click_count"
ATTR_LAST_CLICK_TIME = "last_click_time"
ATTR_TRACKED_ENTITY = "tracked_entity"
ATTR_CLICK_WINDOW = "click_window_seconds"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the physical-interaction binary sensor (always enabled)."""
    runtime: WhoditEntryRuntime = hass.data[DOMAIN]["entries_runtime"][entry.entry_id]
    tracked_entity_id = entry.data[CONF_TRACKED_ENTITY_ID]

    from .sensor import _resolve_device_info  # local import to avoid cycle

    device_info = _resolve_device_info(hass, entry, tracked_entity_id)

    async_add_entities(
        [WhoditPhysicalInteractionSensor(runtime, entry, tracked_entity_id, device_info)]
    )


class WhoditPhysicalInteractionSensor(RestoreEntity, BinarySensorEntity):
    """ON during a physical click train, OFF between trains. Mirrors the
    train state computed by the main sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "physical_interaction"
    _attr_should_poll = False

    def __init__(
        self,
        runtime: WhoditEntryRuntime,
        entry: ConfigEntry,
        tracked_entity_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._runtime = runtime
        self._entry = entry
        self._tracked_entity_id = tracked_entity_id
        self._attr_unique_id = f"{entry.entry_id}_physical_interaction"
        self._attr_device_info = device_info

        self._attr_is_on = False
        self._click_count = 0
        self._last_click_iso: str | None = None

        self._unsub_click_listener = None
        self._unsub_train_closed_listener = None

    @property
    def _click_window_seconds(self) -> int:
        return int(
            self._entry.options.get(CONF_CLICK_WINDOW_SECONDS, DEFAULT_CLICK_WINDOW_SECONDS)
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore the last click_count for display; always start OFF (a
        # train cannot be in progress across a restart).
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._last_click_iso = last_state.attributes.get(ATTR_LAST_CLICK_TIME)
            self._click_count = int(last_state.attributes.get(ATTR_CLICK_COUNT, 0) or 0)
        self._attr_is_on = False

        self._unsub_click_listener = self._runtime.register_device_click_listener(
            self._async_on_device_click
        )
        self._unsub_train_closed_listener = self._runtime.register_train_closed_listener(
            self._async_on_train_closed
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_click_listener:
            self._unsub_click_listener()
        if self._unsub_train_closed_listener:
            self._unsub_train_closed_listener()

    # ------------------------------------------------------------------
    # Runtime callbacks (from the main sensor)
    # ------------------------------------------------------------------
    @callback
    def _async_on_device_click(self, context_id: str, event_time_iso: str, click_index: int) -> None:
        """A physical click: mirror the train position from the sensor."""
        self._attr_is_on = True
        self._click_count = click_index
        self._last_click_iso = event_time_iso
        self.async_write_ha_state()

    @callback
    def _async_on_train_closed(self) -> None:
        """The sensor reports the click window elapsed: go OFF, keep count."""
        self._attr_is_on = False
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Manual reset (service)
    # ------------------------------------------------------------------
    @callback
    def async_force_reset(self) -> None:
        _LOGGER.debug("Whodidit: manual service reset for %s", self._tracked_entity_id)
        self._attr_is_on = False
        self._click_count = 0
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------
    @property
    def icon(self) -> str:
        return "mdi:hand-back-right" if self._attr_is_on else "mdi:hand-back-right-off"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            ATTR_CLICK_COUNT: self._click_count,
            ATTR_LAST_CLICK_TIME: self._last_click_iso,
            ATTR_TRACKED_ENTITY: self._tracked_entity_id,
            ATTR_CLICK_WINDOW: self._click_window_seconds,
        }
