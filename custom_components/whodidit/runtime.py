"""Per-config-entry runtime coordination.

Spec ref: v1.1.0 - the `sensor` and `binary_sensor` platforms of one config
entry need an in-memory hand-off point (not the HA bus, which carries only
the public `whodidit_trigger_detected` event).

v2.2.0 adds a second, reverse channel so the binary sensor (the authority
on the click train) can push train metadata back to the main sensor, which
owns the history_log:
  - `device_click`  : sensor -> binary_sensor (a physical click happened)
  - `train_update`  : binary_sensor -> sensor (this click is index N of the
                       current train; and, on window close, the final
                       train_size to consolidate onto the train's entries)

Append-style listener registration lets future consumers subscribe without
displacing each other.
"""
from __future__ import annotations

from typing import Callable


class WhoditEntryRuntime:
    """In-memory hub shared by the two platforms of a single config entry."""

    def __init__(self, tracked_entity_id: str) -> None:
        self.tracked_entity_id = tracked_entity_id
        self._device_click_listeners: list[Callable[[str, str], None]] = []
        # train_update listeners: (kind, click_index, train_size).
        self._train_update_listeners: list[Callable[[str, int, int], None]] = []

    # ------------------------------------------------------------------
    # device_click channel: sensor -> binary_sensor
    # ------------------------------------------------------------------
    def register_device_click_listener(
        self, listener: Callable[[str, str], None]
    ) -> Callable[[], None]:
        """Subscribe (binary_sensor). Returns an unsubscribe callable."""
        self._device_click_listeners.append(listener)

        def _unsub() -> None:
            if listener in self._device_click_listeners:
                self._device_click_listeners.remove(listener)

        return _unsub

    def notify_device_click(self, context_id: str, event_time_iso: str) -> None:
        """Called by sensor.py when a classification lands as `device`."""
        for listener in list(self._device_click_listeners):
            listener(context_id, event_time_iso)

    # ------------------------------------------------------------------
    # train_update channel: binary_sensor -> sensor
    # ------------------------------------------------------------------
    def register_train_update_listener(
        self, listener: Callable[[str, int, int], None]
    ) -> Callable[[], None]:
        """Subscribe (sensor). Returns an unsubscribe callable."""
        self._train_update_listeners.append(listener)

        def _unsub() -> None:
            if listener in self._train_update_listeners:
                self._train_update_listeners.remove(listener)

        return _unsub

    def notify_train_update(self, kind: str, click_index: int, train_size: int) -> None:
        """Called by binary_sensor.

        kind == "progress": a live click, `click_index` is its 1-based
            position in the current train; `train_size` is provisional
            (equal to click_index at this instant).
        kind == "final": the detection window closed; `train_size` is the
            final total to consolidate onto the last `train_size` device
            entries of the history.
        """
        for listener in list(self._train_update_listeners):
            listener(kind, click_index, train_size)
