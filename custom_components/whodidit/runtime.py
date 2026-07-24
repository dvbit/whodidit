"""Per-config-entry runtime coordination.

Spec ref: v1.1.0 – "Binary sensor 'physically interacted'" needs to react
to physical clicks *classified by the main whodidit sensor*. The two
platforms (`sensor` and `binary_sensor`) live in the same config entry and
need a common in-memory hand-off point without going through the HA bus
(the bus is used only for the public `whodidit_trigger_detected` event).

Memory pattern ("set_update_callback / multi-entity callbacks and
coordinator pattern"): use append-style listener registration so future
consumers can subscribe without displacing each other.
"""
from __future__ import annotations

from typing import Callable


class WhoditEntryRuntime:
    """In-memory hub shared by the two platforms of a single config entry."""

    def __init__(self, tracked_entity_id: str) -> None:
        self.tracked_entity_id = tracked_entity_id
        self._device_click_listeners: list[Callable[[str, str], None]] = []

    # ------------------------------------------------------------------
    # Listener plumbing
    # ------------------------------------------------------------------
    def register_device_click_listener(
        self, listener: Callable[[str, str], None]
    ) -> Callable[[], None]:
        """Called by binary_sensor.async_added_to_hass to subscribe. Returns
        an unsubscribe callable (used on entity removal)."""
        self._device_click_listeners.append(listener)

        def _unsub() -> None:
            if listener in self._device_click_listeners:
                self._device_click_listeners.remove(listener)

        return _unsub

    # ------------------------------------------------------------------
    # Emission (called by sensor.py when a classification lands as `device`)
    # ------------------------------------------------------------------
    def notify_device_click(self, context_id: str, event_time_iso: str) -> None:
        for listener in list(self._device_click_listeners):
            listener(context_id, event_time_iso)
