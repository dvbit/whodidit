"""Binary sensor platform for Whodidit - Physical Interaction.

Spec ref: v2.0.0 (breaking simplification) -
- Turns ON at the first physical click (whodidit source_type == device).
- Turns OFF at the end of the click-detection window (`click_window_seconds`).
  So the binary is ON for the duration of a "click train" and OFF between
  trains. There is no motion/occupancy sensor and no separate reset lapse.
- `click_count` attribute holds the number of physical clicks in the train.
  It PERSISTS after the binary goes OFF (keeps showing the last train size)
  and resets to 0 only at the first click of the next train.
- A new physical click while the window is open extends the window (sliding
  model) and increments the count.
- Manual reset via the whodidit.reset_physical_interaction service forces
  OFF and clears the count.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
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
    """Set up the physical-interaction binary sensor.

    v2.2.0: the binary sensor is always enabled - there is no toggle.
    """
    runtime: WhoditEntryRuntime = hass.data[DOMAIN]["entries_runtime"][entry.entry_id]
    tracked_entity_id = entry.data[CONF_TRACKED_ENTITY_ID]

    from .sensor import _resolve_device_info  # local import to avoid cycle

    device_info = _resolve_device_info(hass, entry, tracked_entity_id)

    async_add_entities(
        [WhoditPhysicalInteractionSensor(runtime, entry, tracked_entity_id, device_info)]
    )


class WhoditPhysicalInteractionSensor(RestoreEntity, BinarySensorEntity):
    """Binary sensor: ON during a physical click train, OFF between trains."""

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

        # Live state ---------------------------------------------------
        self._attr_is_on = False
        self._click_count = 0
        self._last_click_iso: str | None = None
        # True while a detection window is open (a click train in progress).
        self._click_window_open = False

        self._unsub_click_window = None
        self._unsub_click_listener = None

    # ------------------------------------------------------------------
    # Config helper
    # ------------------------------------------------------------------
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

        # Restore counters across restarts. The detection window does not
        # survive a restart, so the binary starts OFF and the next click
        # begins a fresh train; click_count value persists for display.
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._last_click_iso = last_state.attributes.get(ATTR_LAST_CLICK_TIME)
            self._click_count = int(last_state.attributes.get(ATTR_CLICK_COUNT, 0) or 0)
        # Always start OFF at boot: a window cannot be in progress.
        self._attr_is_on = False
        self._click_window_open = False

        self._unsub_click_listener = self._runtime.register_device_click_listener(
            self._async_on_device_click
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_click_listener:
            self._unsub_click_listener()
        self._cancel_click_window()

    # ------------------------------------------------------------------
    # Physical click handling (called from runtime by the main sensor)
    # ------------------------------------------------------------------
    @callback
    def _async_on_device_click(self, context_id: str, event_time_iso: str) -> None:
        _LOGGER.debug(
            "Whodidit: physical click on %s (context=%s)",
            self._tracked_entity_id,
            context_id[:8],
        )

        # If the previous window is closed, this click starts a fresh train:
        # zero the counter before incrementing (spec: count persists until
        # the first click of the next train).
        if not self._click_window_open:
            self._click_count = 0
            self._click_window_open = True

        self._click_count += 1
        self._last_click_iso = event_time_iso
        self._attr_is_on = True

        # Sliding detection window: each click extends it.
        self._cancel_click_window()
        self._unsub_click_window = async_call_later(
            self.hass, self._click_window_seconds, self._async_close_click_window
        )

        self.async_write_ha_state()

        # v2.2.0: tell the main sensor the 1-based index of this click in
        # the current train so it can annotate the history entry it just
        # appended (provisional train_size == click_index for now).
        self._runtime.notify_train_update("progress", self._click_count, self._click_count)

    @callback
    def _async_close_click_window(self, _now) -> None:
        """Window expired: binary goes OFF, click_count value persists."""
        self._unsub_click_window = None
        self._click_window_open = False
        self._attr_is_on = False
        _LOGGER.debug(
            "Whodidit: click window closed on %s -> OFF (train size %d persists)",
            self._tracked_entity_id,
            self._click_count,
        )
        self.async_write_ha_state()

        # v2.2.0: consolidate the final train_size onto the train's history
        # entries in the main sensor.
        self._runtime.notify_train_update("final", 0, self._click_count)

    def _cancel_click_window(self) -> None:
        if self._unsub_click_window is not None:
            self._unsub_click_window()
            self._unsub_click_window = None

    # ------------------------------------------------------------------
    # Manual reset (service)
    # ------------------------------------------------------------------
    @callback
    def async_force_reset(self) -> None:
        """External reset trigger (spec: reset via service)."""
        _LOGGER.debug("Whodidit: manual service reset for %s", self._tracked_entity_id)
        self._cancel_click_window()
        self._attr_is_on = False
        self._click_count = 0
        self._click_window_open = False
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
