"""Constants for the Whodidit integration.

Spec ref: "Purpose & context" + "Sensor States" + "Sensor Attributes"
sections of the consolidated requirement (dvbit/whodidit, feature-parity
reimplementation inspired by sfox38/whodunnit, MIT licensed).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from homeassistant.const import Platform

DOMAIN = "whodidit"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

# --- Frontend card distribution (spec v1.2.0) -------------------------------
# Version is read from manifest.json so the card resource URL is
# cache-busted (?v=...) on every release automatically.
_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
with open(_MANIFEST_PATH, encoding="utf-8") as _mf:
    INTEGRATION_VERSION: Final[str] = json.load(_mf).get("version", "0.0.0")

# Base URL under which the card JS is served (registered as a static path).
URL_BASE: Final[str] = "/whodidit"
# JavaScript modules bundled with the integration and auto-registered as
# Lovelace resources (storage mode).
JSMODULES: Final[list[dict[str, str]]] = [
    {
        "name": "Whodidit Card",
        "filename": "whodidit-card.js",
        "version": INTEGRATION_VERSION,
    }
]

# --- Event bus -------------------------------------------------------------
# Spec: "Evento" -> whodidit_trigger_detected, fired on every classification
# (not only on state.value change) so repeated same-source triggers are not
# lost to a plain `state` automation trigger.
EVENT_TRIGGER_DETECTED = "whodidit_trigger_detected"

# --- Config entry data keys --------------------------------------------------
CONF_TRACKED_ENTITY_ID = "entity_id"

# --- Config entry options (spec v2.0.0) -------------------------------------
# Physical-interaction options. The binary sensor is ON during a click
# train and OFF at the end of the detection window; there is no motion/
# occupancy sensor and no separate reset lapse anymore.
# Physical-interaction option. The binary sensor is always enabled
# (v2.2.0); only the click-detection window is configurable.
CONF_CLICK_WINDOW_SECONDS = "click_window_seconds"

DEFAULT_CLICK_WINDOW_SECONDS = 3

# --- Services (spec: "Servizio") --------------------------------------------
SERVICE_RESET_PHYSICAL = "reset_physical_interaction"
# v1.2.0: used by the card's settings dialog to update entry options.
SERVICE_UPDATE_OPTIONS = "update_options"

# --- Cache tuning (spec: "Advanced Tuning" equivalent) ----------------------
# Time-to-live of a cached automation/script/scene context, in seconds.
CONTEXT_CACHE_TTL = 120
# Interval between cache cleanup sweeps.
CACHE_CLEANUP_INTERVAL = 30
# Time-to-live of the resolved person / service-account identity cache.
USER_IDENTITY_CACHE_TTL = 300
# History log length kept per tracked entity (spec: "History Log Attribute").
HISTORY_LOG_SIZE = 25
# Debounce window for attribute-only changes on the same entity (spec:
# "Note on attribute-only changes").
ATTRIBUTE_DEBOUNCE_SECONDS = 2
# ESPHome context-reuse window (spec: "ESPHome Context Bleed").
ESPHOME_BLEED_WINDOW_SECONDS = 5

# --- Source types (sensor attribute `source_type`) --------------------------
SOURCE_AUTOMATION = "automation"
SOURCE_SCRIPT = "script"
SOURCE_SCENE = "scene"
SOURCE_USER = "user"
SOURCE_SERVICE = "service"
SOURCE_DEVICE = "device"
SOURCE_UNKNOWN = "unknown"

# --- Sensor states (spec: "Sensor States" table) -----------------------------
STATE_MONITORING = "monitoring"
STATE_AUTOMATION = "automation"
STATE_SCRIPT = "script"
STATE_SCENE = "scene"
STATE_UI = "ui"
STATE_SERVICE = "service"
STATE_DEVICE = "device"

VALID_SENSOR_STATES = {
    STATE_MONITORING,
    STATE_AUTOMATION,
    STATE_SCRIPT,
    STATE_SCENE,
    STATE_UI,
    STATE_SERVICE,
    STATE_DEVICE,
}

# --- Confidence levels (spec: "Confidence Levels") ---------------------------
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# --- Attributes exposed on the sensor (spec: "Sensor Attributes") -----------
ATTR_SOURCE_TYPE = "source_type"
ATTR_SOURCE_ID = "source_id"
ATTR_SOURCE_NAME = "source_name"
ATTR_CONTEXT_ID = "context_id"
ATTR_USER_ID = "user_id"
ATTR_EVENT_TIME = "event_time"
ATTR_CONFIDENCE = "confidence"
ATTR_HISTORY_LOG = "history_log"
ATTR_CACHE_DEBUG = "cache_debug"

# --- Supported domains (spec: "Domini supportati", 21 domains) -------------
PHYSICAL_DEVICE_DOMAINS = {
    "switch",
    "light",
    "fan",
    "media_player",
    "cover",
    "lock",
    "vacuum",
    "siren",
    "humidifier",
    "climate",
    "remote",
    "water_heater",
    "valve",
}
DEVICE_SIDE_CONTROL_DOMAINS = {"number", "select", "button"}
HELPER_DOMAINS = {
    "input_boolean",
    "input_button",
    "input_number",
    "input_select",
    "input_text",
}
OTHER_TRACKABLE_DOMAINS = {"alarm_control_panel", "timer"}

SUPPORTED_DOMAINS: set[str] = (
    PHYSICAL_DEVICE_DOMAINS
    | DEVICE_SIDE_CONTROL_DOMAINS
    | HELPER_DOMAINS
    | OTHER_TRACKABLE_DOMAINS
)

# Domains that never attach to a physical HA device and therefore need a
# virtual device created to host the diagnostic sensor (spec: "Helper and
# Virtual Devices").
VIRTUAL_DEVICE_DOMAINS: set[str] = HELPER_DOMAINS | OTHER_TRACKABLE_DOMAINS

# --- Monitored attributes per domain (spec: "Monitored attributes") --------
MONITORED_ATTRIBUTES: dict[str, set[str]] = {
    "light": {
        "brightness",
        "rgb_color",
        "rgbw_color",
        "xy_color",
        "color_temp",
        "hs_color",
        "effect",
    },
    "climate": {
        "temperature",
        "target_temp_high",
        "target_temp_low",
        "fan_mode",
        "swing_mode",
        "preset_mode",
        "humidity",
    },
    "media_player": {"volume_level", "source", "sound_mode"},
    "fan": {"percentage", "preset_mode", "direction", "oscillating"},
    "cover": {"current_position", "current_tilt_position"},
    "water_heater": {"temperature", "operation_mode"},
    "humidifier": {"humidity"},
    "vacuum": {"fan_speed"},
}
