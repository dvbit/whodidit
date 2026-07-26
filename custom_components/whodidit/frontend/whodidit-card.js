/**
 * Whodidit Card  (spec v1.2.0)
 * ---------------------------------------------------------------------------
 * A single custom Lovelace card that presents, for one tracked entity:
 *   - the last interaction  (state / source_name / event_time / confidence)
 *   - the physical-interaction block (when the binary sensor is enabled):
 *       on-off state, click_count, last_click_time, reference sensor, RESET
 *   - a compact history timeline built from the sensor's history_log
 *   - a settings cog opening a dialog that edits the config-entry options
 *     dynamically via the whodidit.update_options service.
 *
 * Written in framework-free vanilla JS so it needs no build step and can be
 * shipped as-is inside the integration.
 *
 * Config:
 *   type: custom:whodidit-card
 *   entity: sensor.<name>_trigger_source
 */

const CARD_VERSION = "1.2.1";

// Confidence -> colour token (uses HA theme variables where possible).
const CONFIDENCE_COLORS = {
  high: "var(--success-color, #43a047)",
  medium: "var(--warning-color, #ffa600)",
  low: "var(--error-color, #db4437)",
};

// State slug -> icon (mdi) for the "last interaction" header.
const STATE_ICONS = {
  monitoring: "mdi:radar",
  automation: "mdi:robot",
  script: "mdi:script-text",
  scene: "mdi:palette",
  ui: "mdi:gesture-tap",
  service: "mdi:cog-transfer",
  device: "mdi:gesture-double-tap",
};

class WhoditCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._dialogOpen = false;
  }

  // ----- Lovelace lifecycle -------------------------------------------------
  static getStubConfig() {
    return { entity: "" };
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("Whodidit: 'entity' (the *_trigger_source sensor) is required");
    }
    this._config = config;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  // ----- Helpers ------------------------------------------------------------
  /** Resolve the physical-interaction binary sensor that belongs to the same
   *  config entry as the configured trigger-source sensor. We match on the
   *  device: both entities share the same device_id. */
  _findBinarySensor() {
    if (!this._hass) return null;
    const src = this._hass.states[this._config.entity];
    if (!src) return null;
    // Look for a binary_sensor whose attributes.tracked_entity matches this
    // sensor's own tracked entity (the binary sensor exposes tracked_entity).
    // Fallback: match by naming convention.
    const trackedGuess = this._config.entity
      .replace(/^sensor\./, "")
      .replace(/_trigger_source$/, "");
    for (const [eid, st] of Object.entries(this._hass.states)) {
      if (!eid.startsWith("binary_sensor.")) continue;
      const attrs = st.attributes || {};
      if (attrs.click_count === undefined) continue; // not ours
      if (
        eid.includes(trackedGuess) ||
        (attrs.tracked_entity && attrs.tracked_entity === src.attributes.source_id)
      ) {
        return st;
      }
    }
    // Last resort: any whodidit binary sensor referencing this device.
    return null;
  }

  _fmtTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch (e) {
      return iso;
    }
  }

  _localizeState(slug) {
    // Reuse HA's own translated state name if available.
    const src = this._hass?.states[this._config.entity];
    if (src) {
      const key = `component.whodidit.entity.sensor.trigger_source.state.${slug}`;
      const t = this._hass.localize?.(key);
      if (t) return t;
    }
    return slug;
  }

  // ----- Rendering ----------------------------------------------------------
  _render() {
    if (!this._config) return;
    const hass = this._hass;
    const src = hass ? hass.states[this._config.entity] : null;

    if (!src) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        <ha-card>
          <div class="pad">
            <div class="unavailable">Entity <code>${this._config.entity || "—"}</code> not found.</div>
          </div>
        </ha-card>`;
      return;
    }

    const a = src.attributes || {};
    const slug = src.state;
    const confidence = a.confidence || "—";
    const confColor = CONFIDENCE_COLORS[confidence] || "var(--secondary-text-color)";
    const stateIcon = STATE_ICONS[slug] || "mdi:help-circle";
    const bs = this._findBinarySensor();

    const historyRows = (a.history_log || [])
      .slice(0, 25)
      .map((h) => {
        const c = CONFIDENCE_COLORS[h.confidence] || "var(--secondary-text-color)";
        return `
          <div class="tl-row">
            <span class="tl-dot" style="background:${c}"></span>
            <span class="tl-time">${this._fmtTime(h.event_time)}</span>
            <span class="tl-src">${this._localizeState(
              h.source_type === "user" ? "ui" : h.source_type
            )}</span>
            <span class="tl-name">${h.source_name || ""}</span>
          </div>`;
      })
      .join("");

    const physicalBlock = bs
      ? `
        <div class="section">
          <div class="section-title">
            <ha-icon icon="mdi:hand-back-right"></ha-icon> Physical interaction
          </div>
          <div class="grid">
            <div class="lbl">State</div>
            <div class="val">
              <span class="pill ${bs.state === "on" ? "on" : "off"}">${bs.state.toUpperCase()}</span>
            </div>
            <div class="lbl">Click count</div>
            <div class="val big">${bs.attributes.click_count ?? 0}</div>
            <div class="lbl">Last click</div>
            <div class="val">${this._fmtTime(bs.attributes.last_click_time)}</div>
            <div class="lbl">Reference sensor</div>
            <div class="val">${bs.attributes.reference_sensor || "— (time only)"}</div>
          </div>
          <div class="actions">
            <button class="btn" id="reset-btn">
              <ha-icon icon="mdi:restore"></ha-icon> Reset
            </button>
          </div>
        </div>`
      : `
        <div class="section muted">
          <ha-icon icon="mdi:hand-back-right-off"></ha-icon>
          Physical-interaction sensor disabled — enable it from the settings cog.
        </div>`;

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="header">
          <div class="title">
            <ha-icon icon="mdi:magnify-scan"></ha-icon>
            <span>${a.friendly_name || this._config.entity}</span>
          </div>
          <ha-icon-button id="cog" title="Settings">
            <ha-icon icon="mdi:cog"></ha-icon>
          </ha-icon-button>
        </div>

        <div class="section">
          <div class="section-title">
            <ha-icon icon="mdi:clock-outline"></ha-icon> Last interaction
          </div>
          <div class="last">
            <ha-icon class="big-icon" icon="${stateIcon}"></ha-icon>
            <div class="last-main">
              <div class="last-state">${this._localizeState(slug)}</div>
              <div class="last-src">${a.source_name || "—"}</div>
              <div class="last-time">${this._fmtTime(a.event_time)}</div>
            </div>
            <span class="conf" style="background:${confColor}">${confidence}</span>
          </div>
        </div>

        ${physicalBlock}

        ${
          historyRows
            ? `<div class="section">
                 <div class="section-title">
                   <ha-icon icon="mdi:timeline-clock-outline"></ha-icon> History
                 </div>
                 <div class="timeline">${historyRows}</div>
               </div>`
            : ""
        }
      </ha-card>`;

    // Wire up interactions.
    const cog = this.shadowRoot.getElementById("cog");
    if (cog) cog.addEventListener("click", () => this._openSettings());
    const resetBtn = this.shadowRoot.getElementById("reset-btn");
    if (resetBtn)
      resetBtn.addEventListener("click", () => this._callReset(bs.entity_id));

    if (this._dialogOpen) this._renderDialog();
  }

  // ----- Actions ------------------------------------------------------------
  async _callReset(binarySensorId) {
    try {
      await this._hass.callService("whodidit", "reset_physical_interaction", {
        entity_id: binarySensorId,
      });
    } catch (e) {
      console.error("Whodidit: reset failed", e);
    }
  }

  _currentOptions() {
    const bs = this._findBinarySensor();
    return {
      enable_physical_interaction: !!bs,
      reset_lapse_seconds: bs ? bs.attributes.reset_lapse_seconds ?? 300 : 300,
      click_window_seconds: bs ? bs.attributes.click_window_seconds ?? 3 : 3,
      reference_sensor: bs ? bs.attributes.reference_sensor || "" : "",
    };
  }

  _openSettings() {
    this._dialogOpen = true;
    this._renderDialog();
  }

  _closeSettings() {
    this._dialogOpen = false;
    const d = this.shadowRoot.getElementById("wd-dialog");
    if (d) d.remove();
  }

  _renderDialog() {
    // Remove any previous instance.
    const old = this.shadowRoot.getElementById("wd-dialog");
    if (old) old.remove();

    const opts = this._currentOptions();
    const wrap = document.createElement("div");
    wrap.id = "wd-dialog";
    wrap.innerHTML = `
      <div class="backdrop"></div>
      <div class="dialog">
        <div class="dialog-title">
          <ha-icon icon="mdi:cog"></ha-icon> Whodidit settings
        </div>
        <label class="row switch-row">
          <span>Enable physical-interaction sensor</span>
          <input type="checkbox" id="f-enable" ${opts.enable_physical_interaction ? "checked" : ""}/>
        </label>
        <label class="row">
          <span>Reset lapse (seconds)</span>
          <input type="number" min="1" max="86400" id="f-lapse" value="${opts.reset_lapse_seconds}"/>
        </label>
        <label class="row">
          <span>Click window (seconds)</span>
          <input type="number" min="1" max="60" id="f-click" value="${opts.click_window_seconds}"/>
        </label>
        <label class="row">
          <span>Occupancy sensor (priority)</span>
          <input type="text" id="f-occ" placeholder="binary_sensor.…" value="${
            opts.reference_sensor.startsWith("binary_sensor") ? opts.reference_sensor : ""
          }"/>
        </label>
        <label class="row">
          <span>Motion sensor (fallback)</span>
          <input type="text" id="f-motion" placeholder="binary_sensor.…" value=""/>
        </label>
        <div class="dialog-actions">
          <button class="btn ghost" id="cancel-btn">Cancel</button>
          <button class="btn primary" id="save-btn">Save</button>
        </div>
        <div class="hint">Occupancy takes priority over motion. Leave both empty for time-only reset.</div>
      </div>`;

    this.shadowRoot.appendChild(wrap);
    wrap.querySelector(".backdrop").addEventListener("click", () => this._closeSettings());
    wrap.querySelector("#cancel-btn").addEventListener("click", () => this._closeSettings());
    wrap.querySelector("#save-btn").addEventListener("click", () => this._saveSettings());
  }

  async _saveSettings() {
    const d = this.shadowRoot.getElementById("wd-dialog");
    if (!d) return;
    const enable = d.querySelector("#f-enable").checked;
    const lapse = parseInt(d.querySelector("#f-lapse").value, 10);
    const click = parseInt(d.querySelector("#f-click").value, 10);
    const occ = d.querySelector("#f-occ").value.trim();
    const motion = d.querySelector("#f-motion").value.trim();

    const options = {
      enable_physical_interaction: enable,
      reset_lapse_seconds: isNaN(lapse) ? 300 : lapse,
      click_window_seconds: isNaN(click) ? 3 : click,
    };
    if (occ) options.occupancy_sensor_entity_id = occ;
    if (motion) options.motion_sensor_entity_id = motion;

    try {
      await this._hass.callService("whodidit", "update_options", {
        entity_id: this._config.entity,
        options,
      });
      this._closeSettings();
    } catch (e) {
      console.error("Whodidit: update_options failed", e);
      const hint = d.querySelector(".hint");
      if (hint) {
        hint.textContent = "Update failed — check the entity and try again.";
        hint.style.color = "var(--error-color)";
      }
    }
  }

  // ----- Styles -------------------------------------------------------------
  _styles() {
    return `
      <style>
        ha-card { overflow: hidden; }
        .pad { padding: 16px; }
        .unavailable { color: var(--error-color); }
        .header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 12px 12px 4px 16px;
        }
        .title { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 1.1rem; }
        .title ha-icon { color: var(--primary-color); }
        .section { padding: 8px 16px 14px 16px; border-top: 1px solid var(--divider-color); }
        .section.muted { color: var(--secondary-text-color); display:flex; align-items:center; gap:8px; font-size:.9rem; }
        .section-title { display:flex; align-items:center; gap:6px; font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; color: var(--secondary-text-color); margin-bottom: 8px; }
        .last { display:flex; align-items:center; gap:14px; }
        .big-icon { --mdc-icon-size: 40px; color: var(--primary-color); }
        .last-main { flex: 1; }
        .last-state { font-size: 1.15rem; font-weight: 600; text-transform: capitalize; }
        .last-src { color: var(--primary-text-color); }
        .last-time { color: var(--secondary-text-color); font-size: .85rem; }
        .conf { color: #fff; padding: 2px 10px; border-radius: 12px; font-size: .75rem; text-transform: capitalize; }
        .grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; align-items: center; }
        .lbl { color: var(--secondary-text-color); font-size: .85rem; }
        .val { font-size: .95rem; }
        .val.big { font-size: 1.4rem; font-weight: 700; }
        .pill { padding: 2px 10px; border-radius: 12px; font-size: .75rem; color:#fff; }
        .pill.on { background: var(--success-color, #43a047); }
        .pill.off { background: var(--secondary-text-color); }
        .actions { margin-top: 10px; display:flex; justify-content:flex-end; }
        .btn { display:inline-flex; align-items:center; gap:6px; border:none; cursor:pointer;
               padding:6px 14px; border-radius:8px; font-size:.85rem;
               background: var(--secondary-background-color); color: var(--primary-text-color); }
        .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .btn.ghost { background: transparent; color: var(--secondary-text-color); }
        .timeline { max-height: 220px; overflow-y: auto; }
        .tl-row { display:grid; grid-template-columns: 12px 150px 90px 1fr; gap:8px; align-items:center;
                  padding: 3px 0; font-size:.82rem; border-bottom: 1px dashed var(--divider-color); }
        .tl-dot { width:10px; height:10px; border-radius:50%; }
        .tl-time { color: var(--secondary-text-color); }
        .tl-src { text-transform: capitalize; font-weight: 600; }
        .tl-name { color: var(--secondary-text-color); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

        #wd-dialog .backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 8; }
        #wd-dialog .dialog { position: fixed; z-index: 9; top: 50%; left: 50%; transform: translate(-50%,-50%);
              width: min(420px, 92vw); background: var(--card-background-color, #fff); color: var(--primary-text-color);
              border-radius: 14px; padding: 18px; box-shadow: 0 8px 30px rgba(0,0,0,.35); }
        #wd-dialog .dialog-title { display:flex; align-items:center; gap:8px; font-weight:700; font-size:1.05rem; margin-bottom: 12px; }
        #wd-dialog .row { display:flex; align-items:center; justify-content:space-between; gap:12px; margin: 8px 0; }
        #wd-dialog .row span { font-size:.9rem; }
        #wd-dialog input[type=number], #wd-dialog input[type=text] {
              flex: 0 0 auto; width: 180px; padding: 6px 8px; border-radius:8px;
              border:1px solid var(--divider-color); background: var(--secondary-background-color); color: inherit; }
        #wd-dialog .switch-row input { width: 20px; height: 20px; }
        #wd-dialog .dialog-actions { display:flex; justify-content:flex-end; gap:8px; margin-top: 14px; }
        #wd-dialog .hint { margin-top: 10px; font-size:.78rem; color: var(--secondary-text-color); }
      </style>`;
  }
}

customElements.define("whodidit-card", WhoditCard);

// Card picker registration.
window.customCards = window.customCards || [];
window.customCards.push({
  type: "whodidit-card",
  name: "Whodidit Card",
  preview: true,
  description: "Shows who/what last triggered an entity, physical clicks and history, with a settings cog.",
});

console.info(
  `%c WHODIDIT-CARD %c v${CARD_VERSION} `,
  "color:#fff;background:#0288d1;font-weight:700",
  "color:#0288d1;background:#e3f2fd"
);
