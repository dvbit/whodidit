# Whodidit 🕵️

**A Home Assistant custom integration – know exactly what triggered your smart devices.**

Whodidit creates a diagnostic sensor for any entity you choose to monitor. Every time that entity's state changes — or a meaningful attribute changes (brightness, color, volume...) — the sensor updates to record **what** caused it (automation, script, scene, dashboard/UI, service account, or the device itself), **who** did it, **when**, and **how confident** Whodidit is in that answer.

> **Attribution / inspiration.** Whodidit is an independent, from-scratch reimplementation inspired by the (no longer available) [sfox38/whodunnit](https://github.com/sfox38/whodunnit) project (MIT licensed). No original source code was copied — this integration was built purely from the publicly documented behaviour, README and release notes of that project, combined with Home Assistant's own official APIs. Full credit to the original author for the concept.

---

## Table of contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Setup](#setup)
- [Supported domains](#supported-domains)
- [Sensor states](#sensor-states)
- [Sensor attributes](#sensor-attributes)
- [Confidence levels](#confidence-levels)
- [`whodidit_trigger_detected` event](#whodidit_trigger_detected-event)
- [Automation examples](#automation-examples)
- [Caveats and limitations](#caveats-and-limitations)
- [Original specification](#original-specification)
- [Version history](#version-history)

## What it does

Whodidit creates a **diagnostic sensor** for any supported entity. Each state or relevant attribute change updates the sensor with:

- **What** caused the change (automation, script, scene, dashboard, physical press, service account, or the device itself)
- **Who** did it (person name, when triggered via the UI)
- **Which** specific automation/script/scene was responsible
- **When** it happened (ISO 8601 timestamp)
- **How confident** Whodidit is (`high` / `medium` / `low`)
- A **rolling history** of the last 25 trigger events
- A **cache debug** attribute explaining how the classification was reached

All of this persists across Home Assistant restarts (`RestoreEntity`).

## How it works

Home Assistant attaches a `Context` (documented, stable public API: `id`, `parent_id`, `user_id`) to every state change. Whodidit listens — **once, globally, for all tracked entities** — to `automation_triggered`, `script_started` and to `scene.turn_on` service calls (scenes do not fire a dedicated activation event in HA core, so the service-call context is used instead), caches these contexts, and matches them against the context of each subsequent state change.

**Detection cascade:**

1. **Direct cache hit on the context ID** → the change was caused by a cached automation/script/scene run. *Confidence: High.*
2. **No cache hit, but `user_id` is set** → a human (or a service account, if the `user_id` has no linked `person` entity and/or is HA `system_generated`) acted via UI/app. *Confidence: High.*
3. **No cache hit, no user, but `parent_id` exists** → HA was involved upstream; Whodidit tries to resolve the parent context too. Resolved → High confidence with the specific source named. Unresolved (deep chains, third-party integrations) → classified as `whodidit.indirect` / **Automation (Indirect)**, *Confidence: Medium.*
4. **Nothing matches** → the change came straight from the device (physical button, hardware timer, firmware event). *Confidence: High.*

Attribute-only changes (e.g. dimming a light without toggling it) follow the same cascade and are debounced to one update per 2 seconds per entity.

## Installation

### HACS (recommended)

1. Open **HACS** → three-dot menu → **Custom repositories**.
2. Add `https://github.com/dvbit/whodidit`, category **Integration**.
3. Install **Whodidit**, then restart Home Assistant.

### Manual

1. Copy `custom_components/whodidit` into your `config/custom_components/` directory.
2. Restart Home Assistant.

## Setup

**Settings → Devices & Services → + Add Integration → Whodidit** → pick the entity to monitor. Repeat for each entity you want tracked (already-tracked entities are hidden from the picker). Each tracked entity gets its own sensor and its own config entry.

## Supported domains

`switch`, `light`, `fan`, `media_player`, `cover`, `lock`, `vacuum`, `siren`, `humidifier`, `climate`, `remote`, `water_heater`, `valve`, `number`, `select`, `button`, `input_boolean`, `input_button`, `input_number`, `input_select`, `input_text`, `alarm_control_panel`, `timer`.

Helper entities without a physical device get a **virtual device** created automatically to host the sensor; it is removed automatically when the Whodidit entry is deleted.

## Sensor states

| State | Meaning |
|---|---|
| `monitoring` | Active, no change recorded yet |
| `automation` | An automation triggered the change |
| `script` | A script triggered the change |
| `scene` | A scene activation triggered the change |
| `ui` | A human user acted via dashboard/app |
| `service` | A service account (Node-RED, AppDaemon...) triggered it |
| `device` | A physical/device-internal event triggered it |

## Sensor attributes

`source_type`, `source_id`, `source_name`, `context_id`, `user_id`, `event_time`, `confidence`, `history_log` (last 25 entries), `cache_debug`.

## Confidence levels

| Level | Meaning |
|---|---|
| High | Direct context match, or certainty of no HA involvement |
| Medium | HA involvement confirmed but specific source unresolved |
| Low | Possible ESPHome context-reuse misclassification |

## `whodidit_trigger_detected` event

Fired on **every** classification, unlike a plain `state` trigger which would not fire for repeated identical sources. Use this for automations:

```yaml
automation:
  - alias: "Notify of unexpected garage light change"
    trigger:
      - platform: event
        event_type: whodidit_trigger_detected
        event_data:
          entity_id: light.garage_light
    action:
      - service: notify.mobile_app
        data:
          message: >
            The garage light was changed by
            {{ trigger.event.data.source_name }}
            via {{ trigger.event.data.state }}.
```

## Automation examples

**Don't let a motion sensor turn off a light that was manually turned on:**

```yaml
automation:
  - alias: "Smart motion off - respect manual control"
    trigger:
      - platform: state
        entity_id: binary_sensor.office_motion
        to: "off"
    condition:
      - condition: not
        conditions:
          - condition: state
            entity_id: sensor.office_light_trigger_source
            state: "device"
    action:
      - service: light.turn_off
        target:
          entity_id: light.office_light
```

**Alert on low-confidence classification:**

```yaml
automation:
  - alias: "Warn on low confidence Whodidit reading"
    trigger:
      - platform: event
        event_type: whodidit_trigger_detected
        event_data:
          entity_id: light.garage_light
          confidence: low
    action:
      - service: notify.mobile_app
        data:
          message: "Whodidit is uncertain about the garage light trigger source."
```

## Caveats and limitations

- **System restarts:** state changes that occur while HA is offline are not captured.
- **ESPHome context bleed:** ESPHome devices may reuse the previous HA context for ~5s after a command; a physical press in that window can be misclassified as UI with `confidence: low`.
- **Indirect automations:** deeply nested chains or third-party integrations that create their own context chains resolve to `Automation (Indirect)` at Medium confidence.
- **Overloaded networks:** the context cache has a 2-minute TTL; on severely congested systems events may arrive out of order.
- **Physical vs internal events:** HA does not distinguish a genuine physical press from a device-internal firmware event at the context level, so neither can Whodidit.

## Original specification

<details>
<summary>Consolidated requirement used to build this integration</summary>

```
Integrazione custom HA (Python, config_flow), feature-parity completa con
whodunnit v1.3.0, rebrand come "whodidit":

Core detection: sensore diagnostic per entità monitorata, stato =
monitoring/automation/script/scene/ui/service/device. Cascata a 4 livelli:
cache context_id -> user_id (persona/service account) -> parent_id
(risoluzione ricorsiva) -> device. Confidence high/medium/low. Listener
condivisi singoli su eventi automation/script/scene, cache context TTL 2
min con cleanup periodico. Cache identità utente TTL 5 min.

Attributi sensore: source_type, source_id, source_name, context_id,
user_id, event_time, confidence, history_log (ultimi 25, persistente),
cache_debug (matched_entry, age, total_cache_entries).

Evento whodidit_trigger_detected sul bus, payload completo, fired ad ogni
classificazione.

Attribute-only changes: monitoraggio per dominio (light, climate,
media_player, fan, cover, water_heater, humidifier, vacuum), debounce 2s.

ESPHome bleed detection: finestra 5s, confidence low se rilevato riuso
context.

Persistenza & lifecycle: RestoreEntity, virtual device per helper senza
device fisico, availability tracking, diagnostics download,
entity_category diagnostic, SensorDeviceClass.ENUM.

Config flow: picker entità (esclude già tracciate), 1 config entry = 1
sensore = 1 device page.

Domini supportati (21): switch, light, fan, media_player, cover, lock,
vacuum, siren, humidifier, climate, remote, water_heater, valve, number,
select, button, input_boolean, input_button, input_number, input_select,
input_text, alarm_control_panel, timer.

No dashboard card - solo integrazione.

Localizzazione: EN/IT/FR/ES/DE. Output HACS-ready, README EN+IT.
```

</details>

## Version history

### 1.0 — initial release
- Full feature-parity reimplementation as specified above.
