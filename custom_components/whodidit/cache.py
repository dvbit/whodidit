"""Shared trigger-context cache and classification cascade.

Spec ref: "Detection Logic" / "Detection cascade" (4 steps) and
"Architecture" note (v1.3.0-equivalent: a single shared listener set
populates a shared context cache read by every sensor, instead of each
tracked entity registering its own system-wide listeners -> O(1) instead
of O(N)).

Verified against home-assistant/core (dev branch):
  - homeassistant/components/automation/__init__.py
        EVENT_AUTOMATION_TRIGGERED = "automation_triggered"
  - homeassistant/components/script (documented pattern)
        EVENT_SCRIPT_STARTED = "script_started"
  - homeassistant/components/homeassistant/scene.py
        Scenes do NOT fire a dedicated "activated" event, only
        EVENT_SCENE_RELOADED on reload. Scene activation is therefore
        observed via EVENT_CALL_SERVICE (domain=="scene",
        service=="turn_on"), which core.py's ServiceRegistry fires for
        every service call before execution, carrying the calling
        Context. This is the documented, stable way to observe a scene
        activation's context.
  - homeassistant.core.Context has three stable public attributes:
        id, parent_id, user_id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CACHE_CLEANUP_INTERVAL,
    CONTEXT_CACHE_TTL,
    ESPHOME_BLEED_WINDOW_SECONDS,
    SOURCE_AUTOMATION,
    SOURCE_DEVICE,
    SOURCE_SCENE,
    SOURCE_SCRIPT,
    SOURCE_SERVICE,
    SOURCE_USER,
    USER_IDENTITY_CACHE_TTL,
)

_LOGGER = logging.getLogger(__name__)

EVENT_AUTOMATION_TRIGGERED = "automation_triggered"
EVENT_SCRIPT_STARTED = "script_started"


@dataclass
class _CacheEntry:
    """One cached action context (spec: cascade step 1)."""

    source_type: str
    source_id: str
    source_name: str
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class _UserIdentity:
    """Resolved person / service-account identity (5 min TTL)."""

    name: str
    is_service_account: bool
    resolved_at: float = field(default_factory=time.monotonic)


@dataclass
class ClassificationResult:
    """Return value of TriggerCache.classify()."""

    source_type: str
    source_id: str | None
    source_name: str
    confidence: str
    cache_debug: dict


class TriggerCache:
    """System-wide singleton: one instance per hass, shared by all sensors.

    Spec ref: architecture note "single shared listener set" - avoids each
    tracked entity registering its own automation/script/scene listeners.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._context_cache: dict[str, _CacheEntry] = {}
        self._user_cache: dict[str, _UserIdentity] = {}
        # Per-entity bookkeeping used only for the ESPHome bleed heuristic.
        self._last_ui_command: dict[str, tuple[str, float]] = {}
        # entity_id -> whether its device belongs to the esphome integration.
        self._esphome_flag: dict[str, bool] = {}
        self._unsub_listeners: list = []
        self._unsub_cleanup = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @callback
    def async_start(self) -> None:
        """Register the shared event listeners (called once, on first entry)."""
        self._unsub_listeners.append(
            self.hass.bus.async_listen(
                EVENT_AUTOMATION_TRIGGERED, self._async_handle_automation
            )
        )
        self._unsub_listeners.append(
            self.hass.bus.async_listen(
                EVENT_SCRIPT_STARTED, self._async_handle_script
            )
        )
        self._unsub_listeners.append(
            self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._async_handle_call_service)
        )
        self._unsub_cleanup = async_track_time_interval(
            self.hass, self._async_cleanup, timedelta_seconds(CACHE_CLEANUP_INTERVAL)
        )

    @callback
    def async_stop(self) -> None:
        """Tear down listeners when the last config entry is removed."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        if self._unsub_cleanup:
            self._unsub_cleanup()
            self._unsub_cleanup = None

    # ------------------------------------------------------------------
    # Event listeners - populate the cache (cascade step 1 source data)
    # ------------------------------------------------------------------
    @callback
    def _async_handle_automation(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        name = event.data.get("name") or entity_id
        if not entity_id:
            return
        self._context_cache[event.context.id] = _CacheEntry(
            SOURCE_AUTOMATION, entity_id, name
        )

    @callback
    def _async_handle_script(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        name = event.data.get("name") or entity_id
        if not entity_id:
            return
        self._context_cache[event.context.id] = _CacheEntry(
            SOURCE_SCRIPT, entity_id, name
        )

    @callback
    def _async_handle_call_service(self, event: Event) -> None:
        """Only scene.turn_on is cached here; other domains are not needed
        because automation/script already emit their own dedicated events,
        and every other action's state changes carry a parent_id pointing
        back to whichever of those cached the actual context.
        """
        if event.data.get("domain") != "scene" or event.data.get("service") != "turn_on":
            return
        target = event.data.get("service_data", {}).get("entity_id")
        if isinstance(target, list):
            target = target[0] if target else None
        if not target:
            return
        state = self.hass.states.get(target)
        name = state.attributes.get("friendly_name", target) if state else target
        self._context_cache[event.context.id] = _CacheEntry(SOURCE_SCENE, target, name)

    @callback
    def _async_cleanup(self, _now) -> None:
        cutoff = time.monotonic() - CONTEXT_CACHE_TTL
        expired = [cid for cid, entry in self._context_cache.items() if entry.created_at < cutoff]
        for cid in expired:
            del self._context_cache[cid]

    # ------------------------------------------------------------------
    # Entity setup helpers
    # ------------------------------------------------------------------
    def async_register_bleed_check(self, entity_id: str, is_esphome: bool) -> None:
        """Cache once, at sensor setup, whether the entity's device is
        managed by the esphome integration (spec: bleed check is resolved
        once at setup rather than on every state change)."""
        self._esphome_flag[entity_id] = is_esphome

    def async_forget_entity(self, entity_id: str) -> None:
        self._esphome_flag.pop(entity_id, None)
        self._last_ui_command.pop(entity_id, None)

    # ------------------------------------------------------------------
    # Classification cascade (spec: "Detection Logic")
    # ------------------------------------------------------------------
    async def async_classify(self, entity_id: str, context: Context) -> ClassificationResult:
        """Run the 4-step detection cascade for a state/attribute change."""
        cache_debug: dict = {
            "total_cache_entries": len(self._context_cache),
            "matched_entry": None,
        }

        # Step 1: direct cache hit on the context ID -> High confidence.
        entry = self._context_cache.get(context.id)
        if entry is not None:
            age = time.monotonic() - entry.created_at
            cache_debug["matched_entry"] = {
                "type": entry.source_type,
                "source_id": entry.source_id,
                "context_id": context.id[:8],
                "age_at_match_seconds": round(age, 2),
            }
            if entry.source_type == SOURCE_USER:
                self._maybe_flag_bleed(entity_id, context.id)
            return ClassificationResult(
                entry.source_type, entry.source_id, entry.source_name, "high", cache_debug
            )

        # Step 2: no cache hit, but a user_id is present -> UI or service
        # account trigger.
        if context.user_id:
            name, is_service = await self._async_resolve_user(context.user_id)
            source_type = SOURCE_SERVICE if is_service else SOURCE_USER
            confidence = "high"

            # ESPHome context-bleed heuristic (spec: "Caveats and
            # Limitations" -> ESPHome Context Bleed). A physical press
            # can inherit the previous UI context for ~5s after a
            # command was sent to an esphome-backed entity.
            if self._esphome_flag.get(entity_id) and source_type == SOURCE_USER:
                last = self._last_ui_command.get(entity_id)
                if last is not None and last[0] == context.id:
                    # Same context reused a second time on this entity
                    # within the bleed window -> likely a physical press
                    # that inherited the stale UI context.
                    if time.monotonic() - last[1] < ESPHOME_BLEED_WINDOW_SECONDS:
                        confidence = "low"
                        cache_debug["matched_entry"] = {
                            "type": "device (esphome bleed suspected)",
                            "source_id": entity_id,
                            "context_id": context.id[:8],
                            "seen": True,
                        }
                else:
                    self._last_ui_command[entity_id] = (context.id, time.monotonic())

            return ClassificationResult(source_type, context.user_id, name, confidence, cache_debug)

        # Step 3: no user, no direct cache hit, but a parent_id exists ->
        # something in HA caused it. Try to resolve the parent context.
        if context.parent_id:
            parent_entry = self._context_cache.get(context.parent_id)
            if parent_entry is not None:
                cache_debug["matched_entry"] = {
                    "type": parent_entry.source_type,
                    "source_id": parent_entry.source_id,
                    "context_id": context.parent_id[:8],
                    "resolved_via": "parent_id",
                }
                return ClassificationResult(
                    parent_entry.source_type,
                    parent_entry.source_id,
                    parent_entry.source_name,
                    "high",
                    cache_debug,
                )
            # Parent exists but is not cached (deep chain / 3rd-party
            # integration) -> Automation (Indirect), Medium confidence.
            return ClassificationResult(
                SOURCE_AUTOMATION, "whodidit.indirect", "Automation (Indirect)", "medium", cache_debug
            )

        # Step 4: no user, no parent, no cache hit -> the device itself.
        return ClassificationResult(SOURCE_DEVICE, entity_id, "Device", "high", cache_debug)

    def _maybe_flag_bleed(self, entity_id: str, context_id: str) -> None:
        """Record that this context was used for a cached-hit classification
        too, so a later step-2 reuse on the same entity can be detected."""
        if self._esphome_flag.get(entity_id):
            self._last_ui_command[entity_id] = (context_id, time.monotonic())

    async def _async_resolve_user(self, user_id: str) -> tuple[str, bool]:
        """Resolve a HA user_id to a display name, distinguishing regular
        persons from service accounts (spec: cascade step 2).

        `hass.auth.async_get_user()` returns a `homeassistant.auth.models.User`
        with the documented `name` and `system_generated` attributes.
        `system_generated` is HA's own stable flag for non-interactive,
        internally-created accounts, which is the documented signal closest
        to "service account". Long-lived-token integrations such as
        Node-RED or AppDaemon are regular, non-system-generated users but
        typically have no linked `person` entity, checked here as a second
        signal via the state machine (there is no dedicated public helper
        to look up a person by user_id).
        """
        cached = self._user_cache.get(user_id)
        if cached is not None and time.monotonic() - cached.resolved_at < USER_IDENTITY_CACHE_TTL:
            return cached.name, cached.is_service_account

        user = await self.hass.auth.async_get_user(user_id)
        if user is None:
            name, is_service = user_id, True
        else:
            has_person = any(
                state.attributes.get("user_id") == user_id
                for state in self.hass.states.async_all("person")
            )
            is_service = bool(user.system_generated) or not has_person
            name = user.name or user_id

        self._user_cache[user_id] = _UserIdentity(name, is_service, time.monotonic())
        return name, is_service


def timedelta_seconds(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)
