"""Sensor platform for Whodidit.

Spec ref: "Core detection" + "Attributi sensore" + "Persistenza & lifecycle".
"""
from __future__ import annotations

from collections import deque
import logging
import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, callback, split_entity_id
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .cache import ClassificationResult, TriggerCache
from .const import (
    ATTR_CACHE_DEBUG,
    ATTR_CONFIDENCE,
    ATTR_CONTEXT_ID,
    ATTR_EVENT_TIME,
    ATTR_HISTORY_LOG,
    ATTR_SOURCE_ID,
    ATTR_SOURCE_NAME,
    ATTR_SOURCE_TYPE,
    ATTR_USER_ID,
    ATTRIBUTE_DEBOUNCE_SECONDS,
    CONF_TRACKED_ENTITY_ID,
    DOMAIN,
    EVENT_TRIGGER_DETECTED,
    HISTORY_LOG_SIZE,
    MONITORED_ATTRIBUTES,
    SOURCE_AUTOMATION,
    SOURCE_DEVICE,
    SOURCE_SCENE,
    SOURCE_SCRIPT,
    SOURCE_SERVICE,
    SOURCE_USER,
    STATE_AUTOMATION,
    STATE_DEVICE,
    STATE_MONITORING,
    STATE_SCENE,
    STATE_SCRIPT,
    STATE_SERVICE,
    STATE_UI,
    VALID_SENSOR_STATES,
)

_LOGGER = logging.getLogger(__name__)

# Maps the internal source_type (category) to the sensor's state slug
# (mechanism), per spec: "`state` vs `source_type`" distinction.
_SOURCE_TO_STATE = {
    SOURCE_AUTOMATION: STATE_AUTOMATION,
    SOURCE_SCRIPT: STATE_SCRIPT,
    SOURCE_SCENE: STATE_SCENE,
    SOURCE_USER: STATE_UI,
    SOURCE_SERVICE: STATE_SERVICE,
    SOURCE_DEVICE: STATE_DEVICE,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the single Whodidit sensor for this config entry."""
    cache: TriggerCache = hass.data[DOMAIN]["cache"]
    tracked_entity_id = entry.data[CONF_TRACKED_ENTITY_ID]
    device_info = _resolve_device_info(hass, entry, tracked_entity_id)

    async_add_entities([WhoditSensor(cache, entry, tracked_entity_id, device_info)])


def _resolve_device_info(hass: HomeAssistant, entry: ConfigEntry, tracked_entity_id: str) -> DeviceInfo:
    """Attach to the tracked entity's existing device, or build a virtual
    one for helpers with no physical device (spec: "Helper and Virtual
    Devices")."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    tracked_entry = ent_reg.async_get(tracked_entity_id)
    if tracked_entry and tracked_entry.device_id:
        device = dev_reg.async_get(tracked_entry.device_id)
        if device is not None:
            # Reuse the existing device's identifiers/connections so this
            # entity is shown on the same device page, without owning it.
            return DeviceInfo(identifiers=device.identifiers, connections=device.connections)

    state = hass.states.get(tracked_entity_id)
    name = state.attributes.get("friendly_name", tracked_entity_id) if state else tracked_entity_id
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=name,
        manufacturer="Whodidit",
        model="Virtual tracked entity",
        entry_type=DeviceEntryType.SERVICE,
    )


class WhoditSensor(RestoreEntity, SensorEntity):
    """Diagnostic sensor exposing who/what last changed a tracked entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "trigger_source"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(VALID_SENSOR_STATES)
    _attr_should_poll = False

    def __init__(
        self,
        cache: TriggerCache,
        entry: ConfigEntry,
        tracked_entity_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._cache = cache
        self._entry = entry
        self._tracked_entity_id = tracked_entity_id
        self._domain = split_entity_id(tracked_entity_id)[0]
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = device_info

        self._attr_native_value: str = STATE_MONITORING
        self._source_type: str | None = None
        self._source_id: str | None = None
        self._source_name: str | None = None
        self._context_id: str | None = None
        self._user_id: str | None = None
        self._event_time: str | None = None
        self._confidence: str | None = None
        self._cache_debug: dict | None = None
        self._history_log: deque[dict] = deque(maxlen=HISTORY_LOG_SIZE)

        self._last_attr_update: float | None = None
        self._unsub_state_change = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in VALID_SENSOR_STATES:
            self._restore_from_state(last_state)
        else:
            if last_state is not None:
                _LOGGER.warning(
                    "Whodidit: discarding invalid restored state '%s' for %s, "
                    "resetting to '%s'",
                    last_state.state,
                    self._tracked_entity_id,
                    STATE_MONITORING,
                )
            self._attr_native_value = STATE_MONITORING

        # Resolve once, at setup, whether the tracked entity's device
        # belongs to the esphome integration (spec: cache the bleed-platform
        # check per entity rather than on every state change).
        self._cache.async_register_bleed_check(
            self._tracked_entity_id, self._is_esphome_backed()
        )

        current = self.hass.states.get(self._tracked_entity_id)
        self._attr_available = current is not None

        self._unsub_state_change = async_track_state_change_event(
            self.hass, [self._tracked_entity_id], self._async_state_changed
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state_change:
            self._unsub_state_change()
        self._cache.async_forget_entity(self._tracked_entity_id)

    def _restore_from_state(self, last_state: State) -> None:
        self._attr_native_value = last_state.state
        attrs = last_state.attributes
        self._source_type = attrs.get(ATTR_SOURCE_TYPE)
        self._source_id = attrs.get(ATTR_SOURCE_ID)
        self._source_name = attrs.get(ATTR_SOURCE_NAME)
        self._context_id = attrs.get(ATTR_CONTEXT_ID)
        self._user_id = attrs.get(ATTR_USER_ID)
        self._event_time = attrs.get(ATTR_EVENT_TIME)
        self._confidence = attrs.get(ATTR_CONFIDENCE)
        self._cache_debug = attrs.get(ATTR_CACHE_DEBUG)
        restored_log = attrs.get(ATTR_HISTORY_LOG) or []
        self._history_log = deque(restored_log, maxlen=HISTORY_LOG_SIZE)

    def _is_esphome_backed(self) -> bool:
        """True if the tracked entity's device is managed by the esphome
        integration (spec: "ESPHome Context Bleed")."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        tracked_entry = ent_reg.async_get(self._tracked_entity_id)
        if not tracked_entry or not tracked_entry.device_id:
            return False
        device = dev_reg.async_get(tracked_entry.device_id)
        if device is None:
            return False
        for config_entry_id in device.config_entries:
            config_entry = self.hass.config_entries.async_get_entry(config_entry_id)
            if config_entry is not None and config_entry.domain == "esphome":
                return True
        return False

    # ------------------------------------------------------------------
    # State change handling
    # ------------------------------------------------------------------
    @callback
    def _async_state_changed(self, event: Event) -> None:
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")

        if new_state is None:
            # Tracked entity was removed from HA (spec: "availability
            # tracking - the sensor reports unavailable if the tracked
            # entity is removed").
            self._attr_available = False
            self.async_write_ha_state()
            return

        if not self._attr_available:
            self._attr_available = True

        is_state_change = old_state is None or old_state.state != new_state.state

        if not is_state_change:
            monitored = MONITORED_ATTRIBUTES.get(self._domain)
            if not monitored or old_state is None:
                return
            changed = any(
                old_state.attributes.get(attr) != new_state.attributes.get(attr)
                for attr in monitored
            )
            if not changed:
                return
            # Debounce rapid attribute-only changes (spec: "Note on
            # attribute-only changes" - one update per 2s per entity).
            now = time.monotonic()
            if (
                self._last_attr_update is not None
                and now - self._last_attr_update < ATTRIBUTE_DEBOUNCE_SECONDS
            ):
                return
            self._last_attr_update = now

        self.hass.async_create_task(self._async_classify_and_update(new_state))

    async def _async_classify_and_update(self, new_state: State) -> None:
        result = await self._cache.async_classify(self._tracked_entity_id, new_state.context)
        self._apply_result(result, new_state.context.id, new_state.context.user_id)

    def _apply_result(self, result: ClassificationResult, context_id: str, user_id: str | None) -> None:
        event_time = dt_util.utcnow().isoformat()
        slug = _SOURCE_TO_STATE.get(result.source_type, STATE_DEVICE)

        self._attr_native_value = slug
        self._source_type = result.source_type
        self._source_id = result.source_id
        self._source_name = result.source_name
        self._context_id = context_id
        self._user_id = user_id
        self._event_time = event_time
        self._confidence = result.confidence
        self._cache_debug = result.cache_debug

        self._history_log.appendleft(
            {
                ATTR_EVENT_TIME: event_time,
                ATTR_SOURCE_TYPE: result.source_type,
                ATTR_SOURCE_ID: result.source_id,
                ATTR_SOURCE_NAME: result.source_name,
                ATTR_CONFIDENCE: result.confidence,
                ATTR_CONTEXT_ID: context_id,
            }
        )

        self.async_write_ha_state()

        self.hass.bus.async_fire(
            EVENT_TRIGGER_DETECTED,
            {
                "entity_id": self._tracked_entity_id,
                "state": slug,
                ATTR_SOURCE_TYPE: result.source_type,
                ATTR_SOURCE_ID: result.source_id,
                ATTR_SOURCE_NAME: result.source_name,
                ATTR_CONFIDENCE: result.confidence,
                ATTR_CONTEXT_ID: context_id,
                ATTR_EVENT_TIME: event_time,
            },
        )

    # ------------------------------------------------------------------
    # Exposed attributes (spec: "Sensor Attributes")
    # ------------------------------------------------------------------
    @property
    def extra_state_attributes(self) -> dict:
        return {
            ATTR_SOURCE_TYPE: self._source_type,
            ATTR_SOURCE_ID: self._source_id,
            ATTR_SOURCE_NAME: self._source_name,
            ATTR_CONTEXT_ID: self._context_id,
            ATTR_USER_ID: self._user_id,
            ATTR_EVENT_TIME: self._event_time,
            ATTR_CONFIDENCE: self._confidence,
            ATTR_HISTORY_LOG: list(self._history_log),
            ATTR_CACHE_DEBUG: self._cache_debug,
        }
