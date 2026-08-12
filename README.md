<p align="center">
  <img src="assets/icon.png" alt="Whodidit" width="128" height="128"/>
</p>

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

## Physical Interaction (v1.1.0) 🖐️

For every tracked entity you can enable a companion **binary sensor** that turns ON at the first physical click (a classification with `source_type = device`) and stays ON until a reset condition is met. This mirrors the model of [`dvbit/switch_interaction`](https://github.com/dvbit/switch_interaction) and adds room-aware auto-reset.

### Configuration

Enabled during the config flow (step 2) or later via **Configure** on the entry. Fields:

- **Enable physical-interaction binary sensor** — master switch.
- **Reset lapse (seconds)** — default 300.
- **Click count window (seconds)** — default 3 (matches `switch_interaction`).
- **Occupancy sensor** — optional, `binary_sensor` domain.
- **Motion sensor** — optional, `binary_sensor` domain.

### Reset logic

Three modes, decided by which sensors are configured (**occupancy > motion > time-only**):

1. **Occupancy configured:** after a physical click, wait for occupancy to be OFF, then count `reset_lapse_seconds`; if occupancy goes ON during the countdown the timer is cancelled and restarts when it clears again. Manual service reset is always available.
2. **Motion configured (no occupancy):** same as above but on the motion sensor.
3. **No reference sensor:** `reset_lapse_seconds` counted from the moment the binary went ON.

Any additional physical click while the reset countdown is pending cancels the countdown — the user is still interacting.

### Service

`whodidit.reset_physical_interaction` — forces OFF and clears `click_count`. `entity_id` accepts **either** the tracked entity **or** the binary sensor itself.

```yaml
service: whodidit.reset_physical_interaction
data:
  entity_id: light.kitchen
```

### Automation example

```yaml
automation:
  - alias: "Triple physical click -> movie scene"
    trigger:
      - platform: state
        entity_id: binary_sensor.kitchen_light_physical_interaction
        to: "off"
    condition:
      - condition: template
        value_template: "{{ trigger.from_state.attributes.click_count == 3 }}"
    action:
      - service: scene.turn_on
        target:
          entity_id: scene.movie
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

## Whodidit Card (v1.2.0) 🃏

The integration ships a **custom Lovelace card** that is registered automatically — no separate HACS "Frontend" install needed. After updating, hard-refresh the browser (Ctrl/Cmd+Shift+R) or clear the companion-app cache once so the new resource loads.

Add it to a dashboard:

```yaml
type: custom:whodidit-card
entity: sensor.kitchen_light_trigger_source
```

The card shows:

- **Last interaction** — state icon, localized state and a small colour-coded confidence dot (green = high, amber = medium, red = low). Click the row to open a **history popup** with the last 25 entries.
- **Physical interaction** — a discreet Active/Idle indicator plus `click_count` and last-click time (only when the binary sensor is enabled).
- **Bottom-right controls** — a **reset** button (when the binary sensor exists) and a **settings cog** (⚙️) opening a dialog to change, on the fly: enable/disable the physical-interaction sensor, reset lapse, click window and the occupancy/motion reference sensors. Saving calls `whodidit.update_options`, which reloads the entry so changes take effect immediately.

> **Note on distribution.** HACS does not surface cards that live inside an *Integration* repository in its "Frontend" tab. That is expected: Whodidit serves the card as a static asset and registers it as a Lovelace resource itself, so it works without any manual resource entry (in Lovelace *storage* mode). In *YAML* mode add the resource manually: `url: /whodidit/whodidit-card.js`, `type: module`.

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

### 1.4.0
- Card: the header now shows the **monitored entity name**. The trigger-source sensor exposes two new attributes for this, `tracked_entity` and `tracked_entity_name`.
- Card: the history popup now shows the user for UI actions (`by <name>`) and no longer repeats a redundant "Device" label for device/monitoring entries, matching the main row.

### 1.3.6
- Fix: in some cases the whodidit entities were created without a device (or spawned an orphan device). The integration previously tried to merge its entities into the tracked entity's physical device by copying its identifiers and connections — a pattern that is deprecated and could silently fork a duplicate/empty device (HA dev blog 2026-07-21). Whodidit now **always** creates its own named service device and nests it under the physical device via `via_device`, so the entities are reliably grouped on a named device page and shown as a child of the real device when there is one.

### 1.3.5
- The integration now ships its **own brand icon** in a `brand/` folder (`brand/icon.png`, `brand/logo.png` + @2x). Since **Home Assistant 2026.3** custom integrations can provide local brand images, served through HA's `/api/brands/integration/whodidit/…` proxy and taking priority over the CDN — no `home-assistant/brands` PR needed. The icon shows on the **Devices & Services** page, device pages and throughout the HA frontend.
- Note: on HA versions older than 2026.3 this folder is simply ignored (no breakage). A known HACS bug (hacs/integration#5171) means the icon may still appear blank in the HACS store list until HACS adds a fallback to the local brands API; everywhere else in HA it works.

### 1.3.4
- Added the project icon to the top of the README (visible on GitHub and in the HACS Info tab).
- Entities now carry dynamic mdi icons in the Home Assistant UI: the trigger-source sensor changes icon per state (radar/robot/script/palette/tap/cog/double-tap) and the physical-interaction binary sensor shows an active/idle hand.

### 1.3.3
- Fix (card): `device` and `monitoring` no longer show a duplicated "Device" label — the redundant source name is suppressed, leaving just the time.
- Fix (card + backend): UI actions now show the user who performed them (`by <name>`). A UI service-call context cached with an empty name is now resolved to the actual person/service account on a direct cache hit, so `source_name` is populated instead of blank.

### 1.3.2
- Fix: corrected `click_count` behaviour. When the detection window closes the value now **persists** (it keeps showing the last completed train, e.g. 2); it resets to 0 only at the first click of the next train. So a single click shows 1; after the window a double-click shows 2 (not 3), and the previous value stays visible in between. The value is restored across restarts.

### 1.3.1
- Fix: UI/dashboard actions were sometimes misclassified as `device`. Home Assistant frequently emits the resulting `state_changed` event with `user_id = None`, keeping only `parent_id` pointing back to the originating service call (core behaviour, see core issue #90669). Whodidit now caches every user-initiated service-call context and resolves it via `parent_id`, so dashboard taps are correctly reported as `ui` (or `service` for service accounts). Note: a few integrations emit a brand-new context with neither `user_id` nor `parent_id` preserved; those changes remain indistinguishable from a physical `device` event at the context level.

### 1.3.0
- Redesign: the **Whodidit Card** is now minimalist and closer to native Lovelace styling. Confidence is shown as a small coloured dot (green/amber/red) instead of a text badge; clicking the state row opens a **history popup**; the reset and settings-cog controls sit at the bottom-right of the card.

### 1.2.1
- Fix: `click_count` now counts clicks **within the detection window only** (a "click train"). When the window closes the counter resets to 0, so the next physical click starts fresh at 1 — a single click shows 1, then a following double-click shows 2 (not 3). The counter is independent from the binary sensor's reset lapse and is no longer restored across restarts.

### 1.2.0
- New: bundled **Whodidit Card** (`custom:whodidit-card`) auto-registered by the integration — last interaction, physical-interaction block, 25-entry history timeline, and a settings cog to edit options live.
- New: `whodidit.update_options` service backing the card's settings dialog.
- Manifest now declares `frontend` + `http` dependencies for static-asset serving.

### 1.1.1
- New: integration icon (magnifying glass over a `?` — the classic "who did it?" motif) added as `icon.png` / `icon@2x.png` in the component folder. Home Assistant shows it in the Integrations page automatically; on GitHub / HACS the same asset appears in the repo card.

### 1.1.0
- New: optional **Physical Interaction binary sensor** per tracked entity with `click_count` attribute (model inspired by [`dvbit/switch_interaction`](https://github.com/dvbit/switch_interaction)).
- New: three-mode auto-reset (occupancy > motion > time-only) plus manual reset via the new `whodidit.reset_physical_interaction` service.
- New: two-step config flow (entity picker + physical-interaction options) and full Options Flow to edit settings later.

### 1.0.1
- Fix: `HTTP 400` when opening the config flow — the entity selector was passing `exclude_entities=None`, which fails voluptuous schema validation on the frontend. `exclude_entities` is now omitted when no entities are already tracked.
- Fix: `manifest.json` `version` field aligned to full `MAJOR.MINOR.PATCH` form for stricter HA loaders.

### 1.0 — initial release
- Full feature-parity reimplementation as specified above.
