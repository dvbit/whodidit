/**
 * Whodidit Card  (spec v1.3.0)
 * ---------------------------------------------------------------------------
 * Minimalist Lovelace card for one tracked entity. Layout:
 *
 *   ┌───────────────────────────────────────────────┐
 *   │ <icon>  Last interaction                       │
 *   │         Device                         ● (conf)│   <- click row -> history popup
 *   │         2 min ago                               │
 *   │ ───────────────────────────────────────────────│
 *   │ <icon>  Physical    ● ON   ·   3 clicks         │   (only if binary enabled)
 *   │         last click 12:03                         │
 *   │                                        ↻   ⚙    │   <- reset + settings, bottom-right
 *   └───────────────────────────────────────────────┘
 *
 * - Confidence is shown as a small coloured dot (green/amber/red), no text.
 * - Clicking the last-interaction row opens a history popup (history_log).
 * - Reset and the settings cog live at the bottom-right of the card.
 *
 * Framework-free vanilla JS (no build step).
 *
 * Config:
 *   type: custom:whodidit-card
 *   entity: sensor.<name>_trigger_source
 */

const CARD_VERSION = "1.4.0";

const CONFIDENCE_COLORS = {
  high: "var(--success-color, #43a047)",
  medium: "var(--warning-color, #ffa600)",
  low: "var(--error-color, #db4437)",
};

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
    this._historyOpen = false;
    this._settingsOpen = false;
  }

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
    return 3;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  // ----- Helpers ------------------------------------------------------------
  _findBinarySensor() {
    if (!this._hass) return null;
    const src = this._hass.states[this._config.entity];
    if (!src) return null;
    const trackedGuess = this._config.entity
      .replace(/^sensor\./, "")
      .replace(/_trigger_source$/, "");
    for (const [eid, st] of Object.entries(this._hass.states)) {
      if (!eid.startsWith("binary_sensor.")) continue;
      const attrs = st.attributes || {};
      if (attrs.click_count === undefined) continue;
      if (
        eid.includes(trackedGuess) ||
        (attrs.tracked_entity && attrs.tracked_entity === src.attributes.source_id)
      ) {
        return st;
      }
    }
    return null;
  }

  _relTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const secs = Math.round((Date.now() - d.getTime()) / 1000);
    if (secs < 5) return "just now";
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} h ago`;
    return d.toLocaleString();
  }

  _absTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString();
  }

  _localizeState(slug) {
    const key = `component.whodidit.entity.sensor.trigger_source.state.${slug}`;
    const t = this._hass?.localize?.(key);
    return t || slug;
  }

  /** Compose the subtitle line under the state.
   *  - device / monitoring: backend source_name duplicates the state, so
   *    show only the relative time.
   *  - ui: show "by <user>" (source_name holds the person name).
   *  - service: show "by <service account>".
   *  - automation / script / scene: show the specific entity name. */
  _buildSubtitle(slug, a) {
    const time = this._relTime(a.event_time);
    const name = a.source_name;
    // Names to treat as "no meaningful name" (generic placeholders).
    const generic = ["device", "monitoring", ""];
    const isGeneric =
      !name || generic.includes(String(name).toLowerCase());

    if (slug === "device" || slug === "monitoring") {
      return time;
    }
    if (slug === "ui" || slug === "service") {
      // Prefer the resolved person/account name; fall back to user_id.
      const who = !isGeneric ? name : a.user_id ? `user ${String(a.user_id).slice(0, 8)}` : null;
      return who ? `by ${who} · ${time}` : time;
    }
    // automation / script / scene
    return !isGeneric ? `${name} · ${time}` : time;
  }

  // ----- Render -------------------------------------------------------------
  _render() {
    if (!this._config) return;
    const hass = this._hass;
    const src = hass ? hass.states[this._config.entity] : null;

    if (!src) {
      this.shadowRoot.innerHTML = `${this._styles()}
        <ha-card><div class="empty">Entity <code>${this._config.entity || "—"}</code> not found.</div></ha-card>`;
      return;
    }

    const a = src.attributes || {};
    const slug = src.state;
    const conf = a.confidence;
    const confColor = CONFIDENCE_COLORS[conf] || "var(--disabled-text-color, #9e9e9e)";
    const icon = STATE_ICONS[slug] || "mdi:help-circle";
    const bs = this._findBinarySensor();

    const physicalRow = bs
      ? `
        <div class="divider"></div>
        <div class="row physical">
          <ha-icon class="row-icon" icon="mdi:hand-back-right"></ha-icon>
          <div class="row-main">
            <div class="row-line">
              <span class="pstate ${bs.state === "on" ? "on" : "off"}">
                <span class="pdot"></span>${bs.state === "on" ? "Active" : "Idle"}
              </span>
              <span class="sep">·</span>
              <span class="clicks">${bs.attributes.click_count ?? 0} ${
          (bs.attributes.click_count ?? 0) === 1 ? "click" : "clicks"
        }</span>
            </div>
            <div class="row-sub">last click ${this._relTime(bs.attributes.last_click_time)}</div>
          </div>
        </div>`
      : "";

    // Build a clean subtitle. For device/monitoring the backend sets a
    // generic source_name ("Device"/"") that duplicates the state label,
    // so we suppress it. For ui/service we prefix the user with a label so
    // it reads "by <name>".
    const subtitle = this._buildSubtitle(slug, a);
    const trackedName =
      a.tracked_entity_name || a.friendly_name || this._config.entity;

    this.shadowRoot.innerHTML = `${this._styles()}
      <ha-card>
        <div class="head">
          <ha-icon class="head-icon" icon="mdi:magnify-scan"></ha-icon>
          <span class="head-name" title="${trackedName}">${trackedName}</span>
        </div>
        <div class="body">
          <div class="row state-row" id="state-row" title="Show history">
            <ha-icon class="row-icon" icon="${icon}"></ha-icon>
            <div class="row-main">
              <div class="row-line">
                <span class="state">${this._localizeState(slug)}</span>
                <span class="conf-dot" style="background:${confColor}" title="${conf || "unknown"}"></span>
              </div>
              <div class="row-sub">${subtitle}</div>
            </div>
            <ha-icon class="chevron" icon="mdi:chevron-right"></ha-icon>
          </div>
          ${physicalRow}
        </div>
        <div class="footer">
          ${
            bs
              ? `<ha-icon-button id="reset-btn" title="Reset physical interaction"><ha-icon icon="mdi:restore"></ha-icon></ha-icon-button>`
              : ""
          }
          <ha-icon-button id="cog-btn" title="Settings"><ha-icon icon="mdi:cog-outline"></ha-icon></ha-icon-button>
        </div>
      </ha-card>`;

    const stateRow = this.shadowRoot.getElementById("state-row");
    if (stateRow) stateRow.addEventListener("click", () => this._openHistory());
    const cog = this.shadowRoot.getElementById("cog-btn");
    if (cog) cog.addEventListener("click", () => this._openSettings());
    const reset = this.shadowRoot.getElementById("reset-btn");
    if (reset && bs) reset.addEventListener("click", () => this._callReset(bs.entity_id));

    if (this._historyOpen) this._renderHistory();
    if (this._settingsOpen) this._renderSettings();
  }

  // ----- History popup ------------------------------------------------------
  _openHistory() {
    this._historyOpen = true;
    this._renderHistory();
  }
  _closeHistory() {
    this._historyOpen = false;
    const d = this.shadowRoot.getElementById("wd-history");
    if (d) d.remove();
  }
  _renderHistory() {
    const old = this.shadowRoot.getElementById("wd-history");
    if (old) old.remove();
    const src = this._hass.states[this._config.entity];
    const log = (src?.attributes?.history_log || []).slice(0, 25);

    const rows =
      log
        .map((h) => {
          const c = CONFIDENCE_COLORS[h.confidence] || "var(--disabled-text-color)";
          const type = h.source_type === "user" ? "ui" : h.source_type;
          const label = this._localizeState(type);
          // Suppress the redundant generic source_name for device/monitoring;
          // for ui/service prefix with "by".
          const generic = ["device", "monitoring", ""];
          const nm = h.source_name;
          const isGeneric = !nm || generic.includes(String(nm).toLowerCase());
          let nameHtml = "";
          if (!isGeneric) {
            const prefix = type === "ui" || type === "service" ? "by " : "";
            nameHtml = `<span class="h-name">${prefix}${nm}</span>`;
          }
          return `
            <div class="h-row">
              <span class="h-dot" style="background:${c}"></span>
              <div class="h-main">
                <div class="h-top"><span class="h-src">${label}</span>${nameHtml}</div>
                <div class="h-time">${this._absTime(h.event_time)}</div>
              </div>
            </div>`;
        })
        .join("") || `<div class="h-empty">No history yet.</div>`;

    const wrap = document.createElement("div");
    wrap.id = "wd-history";
    wrap.innerHTML = `
      <div class="backdrop"></div>
      <div class="sheet">
        <div class="sheet-head">
          <span><ha-icon icon="mdi:timeline-clock-outline"></ha-icon> History</span>
          <ha-icon-button id="h-close"><ha-icon icon="mdi:close"></ha-icon></ha-icon-button>
        </div>
        <div class="sheet-body">${rows}</div>
      </div>`;
    this.shadowRoot.appendChild(wrap);
    wrap.querySelector(".backdrop").addEventListener("click", () => this._closeHistory());
    wrap.querySelector("#h-close").addEventListener("click", () => this._closeHistory());
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
      enable: !!bs,
      lapse: bs ? bs.attributes.reset_lapse_seconds ?? 300 : 300,
      click: bs ? bs.attributes.click_window_seconds ?? 3 : 3,
      ref: bs ? bs.attributes.reference_sensor || "" : "",
    };
  }

  // ----- Settings dialog ----------------------------------------------------
  _openSettings() {
    this._settingsOpen = true;
    this._renderSettings();
  }
  _closeSettings() {
    this._settingsOpen = false;
    const d = this.shadowRoot.getElementById("wd-settings");
    if (d) d.remove();
  }
  _renderSettings() {
    const old = this.shadowRoot.getElementById("wd-settings");
    if (old) old.remove();
    const o = this._currentOptions();
    const wrap = document.createElement("div");
    wrap.id = "wd-settings";
    wrap.innerHTML = `
      <div class="backdrop"></div>
      <div class="sheet">
        <div class="sheet-head">
          <span><ha-icon icon="mdi:cog-outline"></ha-icon> Settings</span>
          <ha-icon-button id="s-close"><ha-icon icon="mdi:close"></ha-icon></ha-icon-button>
        </div>
        <div class="sheet-body form">
          <label class="frow switch">
            <span>Physical-interaction sensor</span>
            <input type="checkbox" id="f-enable" ${o.enable ? "checked" : ""}/>
          </label>
          <label class="frow">
            <span>Reset lapse (s)</span>
            <input type="number" min="1" max="86400" id="f-lapse" value="${o.lapse}"/>
          </label>
          <label class="frow">
            <span>Click window (s)</span>
            <input type="number" min="1" max="60" id="f-click" value="${o.click}"/>
          </label>
          <label class="frow">
            <span>Occupancy sensor</span>
            <input type="text" id="f-occ" placeholder="binary_sensor.…" value="${
              o.ref.startsWith("binary_sensor") ? o.ref : ""
            }"/>
          </label>
          <label class="frow">
            <span>Motion sensor</span>
            <input type="text" id="f-motion" placeholder="binary_sensor.…" value=""/>
          </label>
          <div class="hint">Occupancy takes priority. Leave both empty for time-only reset.</div>
        </div>
        <div class="sheet-actions">
          <button class="btn ghost" id="s-cancel">Cancel</button>
          <button class="btn primary" id="s-save">Save</button>
        </div>
      </div>`;
    this.shadowRoot.appendChild(wrap);
    wrap.querySelector(".backdrop").addEventListener("click", () => this._closeSettings());
    wrap.querySelector("#s-close").addEventListener("click", () => this._closeSettings());
    wrap.querySelector("#s-cancel").addEventListener("click", () => this._closeSettings());
    wrap.querySelector("#s-save").addEventListener("click", () => this._saveSettings());
  }

  async _saveSettings() {
    const d = this.shadowRoot.getElementById("wd-settings");
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
    return `<style>
      ha-card { display: flex; flex-direction: column; overflow: hidden; }
      .empty { padding: 16px; color: var(--error-color); }
      .head { display: flex; align-items: center; gap: 10px; padding: 14px 16px 6px 16px; }
      .head-icon { --mdc-icon-size: 22px; color: var(--primary-color); flex: 0 0 auto; }
      .head-name { font-size: 1.1rem; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .body { padding: 4px 4px 0 4px; }

      .row { display: flex; align-items: center; gap: 14px; padding: 12px 12px; border-radius: 12px; }
      .state-row { cursor: pointer; transition: background .15s; }
      .state-row:hover { background: var(--secondary-background-color); }
      .row-icon { --mdc-icon-size: 26px; color: var(--primary-color); flex: 0 0 auto; }
      .row-main { flex: 1; min-width: 0; }
      .row-line { display: flex; align-items: center; gap: 8px; }
      .state { font-size: 1.05rem; font-weight: 600; text-transform: capitalize; }
      .conf-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; box-shadow: 0 0 0 2px var(--card-background-color); }
      .row-sub { color: var(--secondary-text-color); font-size: .82rem; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .chevron { --mdc-icon-size: 20px; color: var(--secondary-text-color); flex: 0 0 auto; }

      .divider { height: 1px; background: var(--divider-color); margin: 2px 12px; }

      .physical .row-line { gap: 6px; font-size: .92rem; }
      .pstate { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
      .pstate .pdot { width: 8px; height: 8px; border-radius: 50%; }
      .pstate.on { color: var(--success-color, #43a047); }
      .pstate.on .pdot { background: var(--success-color, #43a047); }
      .pstate.off { color: var(--secondary-text-color); }
      .pstate.off .pdot { background: var(--disabled-text-color, #9e9e9e); }
      .sep { color: var(--secondary-text-color); }
      .clicks { color: var(--primary-text-color); }

      .footer { display: flex; justify-content: flex-end; align-items: center; gap: 2px; padding: 2px 6px 6px 6px; }
      .footer ha-icon-button { --mdc-icon-size: 20px; color: var(--secondary-text-color); }

      .backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 8; }
      .sheet { position: fixed; z-index: 9; left: 50%; bottom: 0; transform: translateX(-50%);
               width: min(460px, 100vw); background: var(--card-background-color, #fff);
               color: var(--primary-text-color); border-radius: 16px 16px 0 0;
               box-shadow: 0 -6px 30px rgba(0,0,0,.35); max-height: 80vh; display: flex; flex-direction: column; }
      @media (min-width: 620px) {
        .sheet { top: 50%; bottom: auto; transform: translate(-50%,-50%); border-radius: 16px; }
      }
      .sheet-head { display: flex; align-items: center; justify-content: space-between;
                    padding: 14px 8px 14px 18px; font-weight: 700; border-bottom: 1px solid var(--divider-color); }
      .sheet-head span { display: inline-flex; align-items: center; gap: 8px; }
      .sheet-body { padding: 10px 16px; overflow-y: auto; }
      .sheet-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--divider-color); }

      .h-row { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px dashed var(--divider-color); }
      .h-dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex: 0 0 auto; }
      .h-main { flex: 1; min-width: 0; }
      .h-top { display: flex; gap: 8px; align-items: baseline; }
      .h-src { font-weight: 600; text-transform: capitalize; }
      .h-name { color: var(--secondary-text-color); font-size: .85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .h-time { color: var(--secondary-text-color); font-size: .78rem; }
      .h-empty { color: var(--secondary-text-color); padding: 16px 0; text-align: center; }

      .form .frow { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 10px 0; }
      .form .frow span { font-size: .9rem; }
      .form input[type=number], .form input[type=text] { width: 190px; padding: 7px 9px; border-radius: 8px;
              border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: inherit; }
      .form .switch input { width: 20px; height: 20px; }
      .hint { margin-top: 8px; font-size: .78rem; color: var(--secondary-text-color); }
      .btn { border: none; cursor: pointer; padding: 8px 16px; border-radius: 8px; font-size: .88rem; }
      .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .btn.ghost { background: transparent; color: var(--secondary-text-color); }
    </style>`;
  }
}

customElements.define("whodidit-card", WhoditCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "whodidit-card",
  name: "Whodidit Card",
  preview: true,
  description: "Minimal card: who/what last triggered an entity, physical clicks, history popup and live settings.",
});

console.info(
  `%c WHODIDIT-CARD %c v${CARD_VERSION} `,
  "color:#fff;background:#0288d1;font-weight:700",
  "color:#0288d1;background:#e3f2fd"
);
