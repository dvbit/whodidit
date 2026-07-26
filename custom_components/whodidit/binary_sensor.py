"""Binary sensor platform for Whodidit - Physical Interaction.

Spec ref: v1.1.0 consolidated requirement -
- ON at first physical click (whodidit source_type == device)
- OFF via:
  (a) service whodidit.reset_physical_interaction
  (b) if a reference sensor (occupancy or motion) is configured: after
      `reset_lapse_seconds` counted *from when the reference sensor turned
      OFF*; the countdown restarts if the reference goes ON again during
      the wait
  (c) if no reference sensor is configured: after `reset_lapse_seconds`
      counted from the moment the binary went ON
- If a new physical click arrives while the reset timer is pending, the
  timer is cancelled (the user is still interacting)
- Reference sensor priority: occupancy > motion > time-only
- click_count attribute follows the same window model as
  dvbit/switch_interaction (default 3s, extended on every click, reset
  after `click_window_seconds` of silence)
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

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
)
from .runtime import WhoditEntryRuntime

_LOGGER = logging.getLogger(__name__)

ATTR_CLICK_COUNT = "click_count"
ATTR_LAST_CLICK_TIME = "last_click_time"
ATTR_REFERENCE_SENSOR = "reference_sensor"
ATTR_TRACKED_ENTITY = "tracked_entity"
ATTR_RESET_LAPSE = "reset_lapse_seconds"
ATTR_CLICK_WINDOW = "click_window_seconds"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the physical-interaction binary sensor if enabled."""
    if not entry.options.get(CONF_ENABLE_PHYSICAL, DEFAULT_ENABLE_PHYSICAL):
        _LOGGER.debug(
            "Whodidit: physical-interaction binary sensor disabled for entry %s",
            entry.entry_id,
        )
        return

    runtime: WhoditEntryRuntime = hass.data[DOMAIN]["entries_runtime"][entry.entry_id]
    tracked_entity_id = entry.data[CONF_TRACKED_ENTITY_ID]

    # Device info: attach to the tracked entity's device page just like the
    # main sensor does (identifiers-only DeviceInfo means "reuse existing
    # device without owning it").
    from .sensor import _resolve_device_info  # local import to avoid cycle

    device_info = _resolve_device_info(hass, entry, tracked_entity_id)

    async_add_entities(
        [WhoditPhysicalInteractionSensor(runtime, entry, tracked_entity_id, device_info)]
    )


class WhoditPhysicalInteractionSensor(RestoreEntity, BinarySensorEntity):
    """Binary sensor that is ON while the tracked entity is being
    physically interacted with."""

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
        # Use a distinct suffix so this ID differs from the main sensor
        # (unique_id must be unique per entity, not per entry).
        self._attr_unique_id = f"{entry.entry_id}_physical_interaction"
        self._attr_device_info = device_info

        # Live state ---------------------------------------------------
        self._attr_is_on = False
        self._click_count = 0
        self._last_click_iso: str | None = None
        # True while a detection window is open (a click train is in
        # progress). When False, the next click starts a fresh train.
        self._click_window_open = False

        # Timer handles (cancellables returned by async_call_later) ----
        self._unsub_click_window = None
        self._unsub_reset_lapse = None
        self._unsub_click_listener = None
        self._unsub_reference_listener = None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    @property
    def _reset_lapse_seconds(self) -> int:
        return int(self._entry.options.get(CONF_RESET_LAPSE_SECONDS, DEFAULT_RESET_LAPSE_SECONDS))

    @property
    def _click_window_seconds(self) -> int:
        return int(self._entry.options.get(CONF_CLICK_WINDOW_SECONDS, DEFAULT_CLICK_WINDOW_SECONDS))

    @property
    def _reference_sensor(self) -> str | None:
        """Occupancy > motion > None (spec: reference priority)."""
        occ = self._entry.options.get(CONF_OCCUPANCY_SENSOR)
        if occ:
            return occ
        mot = self._entry.options.get(CONF_MOTION_SENSOR)
        return mot or None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore state and counters across HA restarts.
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == STATE_ON
            self._last_click_iso = last_state.attributes.get(ATTR_LAST_CLICK_TIME)
            # Spec (v1.3.2): click_count persists (it shows the last
            # completed train), so we DO restore it. The detection window
            # does not survive a restart, so _click_window_open stays False
            # and the next physical click starts a fresh train.
            self._click_count = int(last_state.attributes.get(ATTR_CLICK_COUNT, 0) or 0)

        # Subscribe to physical-click hand-offs from the main sensor.
        self._unsub_click_listener = self._runtime.register_device_click_listener(
            self._async_on_device_click
        )

        # If the binary is ON at boot and a reference sensor is set, start
        # (or resume) the reset-lapse logic. If it's ON without reference,
        # start the plain time-only countdown.
        if self._attr_is_on:
            self._start_reset_logic()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_click_listener:
            self._unsub_click_listener()
        self._cancel_click_window()
        self._cancel_reset_lapse()
        self._detach_reference_listener()

    # ------------------------------------------------------------------
    # Physical click handling (called from runtime by the main sensor)
    # ------------------------------------------------------------------
    @callback
    def _async_on_device_click(self, context_id: str, event_time_iso: str) -> None:
        _LOGGER.debug(
            "Whodidit: physical click detected on %s (context=%s)",
            self._tracked_entity_id,
            context_id[:8],
        )

        # A new physical click always cancels any pending reset countdown
        # (spec: "se durante l'attesa avviene una nuova interazione fisica,
        # si azzera qualsiasi timer di reset pendente").
        self._cancel_reset_lapse()
        self._detach_reference_listener()

        # Spec (v1.3.2): the click_count value persists after a window
        # closes and only restarts at the FIRST click of the next train.
        # So if the previous window is closed, this click starts a fresh
        # train: zero the counter before incrementing.
        if not self._click_window_open:
            self._click_count = 0
            self._click_window_open = True

        self._click_count += 1
        self._last_click_iso = event_time_iso
        self._attr_is_on = True

        # Restart the click-count window (dvbit/switch_interaction model:
        # sliding window - extended on every new click).
        self._cancel_click_window()
        self._unsub_click_window = async_call_later(
            self.hass, self._click_window_seconds, self._async_close_click_window
        )

        self.async_write_ha_state()

    @callback
    def _async_close_click_window(self, _now) -> None:
        """Click window expired.

        Spec (v1.3.2): when the detection window closes, the click_count
        VALUE PERSISTS (it keeps showing the size of the last completed
        train, e.g. 2). It is NOT zeroed here. The counter is only reset to
        0 at the arrival of the *first* click of the next train (see
        _async_on_device_click), so a single click shows 1, then after the
        window a double-click shows 2 (not 3), while 2 remains visible in
        between.

        This is independent from the binary sensor's reset lapse: the
        binary can stay ON while the click window has closed.
        """
        self._unsub_click_window = None
        # Mark that the current train has ended; the next click starts fresh.
        self._click_window_open = False
        _LOGGER.debug(
            "Whodidit: click window closed on %s (train size %d persists)",
            self._tracked_entity_id,
            self._click_count,
        )
        if self._attr_is_on:
            self._start_reset_logic()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Reset logic - 3 modes (spec)
    # ------------------------------------------------------------------
    def _start_reset_logic(self) -> None:
        """Decide which reset strategy applies based on configured sensors."""
        ref = self._reference_sensor
        if ref is None:
            # Mode C: time-only, from when the binary went ON.
            _LOGGER.debug(
                "Whodidit: no reference sensor for %s - starting %ds time-only reset",
                self._tracked_entity_id,
                self._reset_lapse_seconds,
            )
            self._schedule_reset_lapse()
            return

        # Mode B: reference-based. If the reference is currently OFF,
        # start the countdown; if it's ON, wait for the OFF transition.
        state = self.hass.states.get(ref)
        self._attach_reference_listener(ref)
        if state is not None and state.state == STATE_OFF:
            _LOGGER.debug(
                "Whodidit: reference %s is OFF - starting %ds reset lapse for %s",
                ref,
                self._reset_lapse_seconds,
                self._tracked_entity_id,
            )
            self._schedule_reset_lapse()
        else:
            _LOGGER.debug(
                "Whodidit: reference %s is ON - waiting for it to clear before "
                "starting reset lapse for %s",
                ref,
                self._tracked_entity_id,
            )

    def _schedule_reset_lapse(self) -> None:
        self._cancel_reset_lapse()
        self._unsub_reset_lapse = async_call_later(
            self.hass, self._reset_lapse_seconds, self._async_reset_lapse_fired
        )

    @callback
    def _async_reset_lapse_fired(self, _now) -> None:
        """Reset-lapse timer expired - turn OFF and clear counters."""
        self._unsub_reset_lapse = None
        _LOGGER.debug(
            "Whodidit: reset lapse fired on %s - turning binary OFF",
            self._tracked_entity_id,
        )
        self._do_reset()

    @callback
    def _async_reference_state_changed(self, event: Event) -> None:
        """Reference sensor changed - manage the reset countdown."""
        new_state: State | None = event.data.get("new_state")
        if new_state is None:
            return

        if new_state.state == STATE_OFF:
            # Reference became clear - (re)start the lapse.
            _LOGGER.debug(
                "Whodidit: reference %s went OFF - (re)starting %ds reset lapse for %s",
                new_state.entity_id,
                self._reset_lapse_seconds,
                self._tracked_entity_id,
            )
            self._schedule_reset_lapse()
        else:
            # Reference active again - cancel any pending countdown
            # (spec: "se durante il conteggio torna ON, si annulla e si
            # riparte quando torna OFF").
            if self._unsub_reset_lapse is not None:
                _LOGGER.debug(
                    "Whodidit: reference %s went ON during lapse - cancelling reset for %s",
                    new_state.entity_id,
                    self._tracked_entity_id,
                )
            self._cancel_reset_lapse()

    def _attach_reference_listener(self, ref_entity_id: str) -> None:
        self._detach_reference_listener()
        self._unsub_reference_listener = async_track_state_change_event(
            self.hass, [ref_entity_id], self._async_reference_state_changed
        )

    def _detach_reference_listener(self) -> None:
        if self._unsub_reference_listener is not None:
            self._unsub_reference_listener()
            self._unsub_reference_listener = None

    def _cancel_click_window(self) -> None:
        if self._unsub_click_window is not None:
            self._unsub_click_window()
            self._unsub_click_window = None

    def _cancel_reset_lapse(self) -> None:
        if self._unsub_reset_lapse is not None:
            self._unsub_reset_lapse()
            self._unsub_reset_lapse = None

    # ------------------------------------------------------------------
    # Manual reset (called from the service handler)
    # ------------------------------------------------------------------
    @callback
    def async_force_reset(self) -> None:
        """External reset trigger (spec: reset via service)."""
        _LOGGER.debug(
            "Whodidit: manual service reset for %s", self._tracked_entity_id
        )
        self._do_reset()

    def _do_reset(self) -> None:
        self._cancel_click_window()
        self._cancel_reset_lapse()
        self._detach_reference_listener()
        self._attr_is_on = False
        self._click_count = 0
        self._click_window_open = False
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------
    @property
    def extra_state_attributes(self) -> dict:
        return {
            ATTR_CLICK_COUNT: self._click_count,
            ATTR_LAST_CLICK_TIME: self._last_click_iso,
            ATTR_TRACKED_ENTITY: self._tracked_entity_id,
            ATTR_REFERENCE_SENSOR: self._reference_sensor,
            ATTR_RESET_LAPSE: self._reset_lapse_seconds,
            ATTR_CLICK_WINDOW: self._click_window_seconds,
        }
