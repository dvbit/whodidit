"""Per-config-entry runtime coordination.

Spec ref: v1.1.0 - the `sensor` and `binary_sensor` platforms of one config
entry need an in-memory hand-off point (not the HA bus, which carries only
the public `whodidit_trigger_detected` event).

v2.4.0: the MAIN SENSOR is the click-train authority (it counts consecutive
physical clicks within the click window from its own event timestamps). The
binary sensor merely mirrors that state. So the channel is one-way,
sensor -> binary_sensor:
  - device_click(context_id, event_time, click_index): a physical click,
    with its 1-based position in the current train.
  - train_closed(): the click window elapsed with no further click; the
    binary sensor should turn OFF (its click_count value persists).
"""
from __future__ import annotations

from typing import Callable


class WhoditEntryRuntime:
    """In-memory hub shared by the two platforms of a single config entry."""

    def __init__(self, tracked_entity_id: str) -> None:
        self.tracked_entity_id = tracked_entity_id
        # listeners receive (context_id, event_time_iso, click_index)
        self._device_click_listeners: list[Callable[[str, str, int], None]] = []
        # listeners receive no args
        self._train_closed_listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # device_click channel: sensor -> binary_sensor
    # ------------------------------------------------------------------
    def register_device_click_listener(
        self, listener: Callable[[str, str, int], None]
    ) -> Callable[[], None]:
        """Subscribe (binary_sensor). Returns an unsubscribe callable."""
        self._device_click_listeners.append(listener)

        def _unsub() -> None:
            if listener in self._device_click_listeners:
                self._device_click_listeners.remove(listener)

        return _unsub

    def notify_device_click(
        self, context_id: str, event_time_iso: str, click_index: int
    ) -> None:
        """Called by sensor.py on a physical (device) click. `click_index`
        is the 1-based position in the current train."""
        for listener in list(self._device_click_listeners):
            listener(context_id, event_time_iso, click_index)

    # ------------------------------------------------------------------
    # train_closed channel: sensor -> binary_sensor
    # ------------------------------------------------------------------
    def register_train_closed_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Subscribe (binary_sensor). Returns an unsubscribe callable."""
        self._train_closed_listeners.append(listener)

        def _unsub() -> None:
            if listener in self._train_closed_listeners:
                self._train_closed_listeners.remove(listener)

        return _unsub

    def notify_train_closed(self) -> None:
        """Called by sensor.py when the click window elapses."""
        for listener in list(self._train_closed_listeners):
            listener()
