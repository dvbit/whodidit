/**
 * Whodidit Card  (spec v2.1.0)
 * ---------------------------------------------------------------------------
 * Minimalist Lovelace card for one tracked entity, everything INLINE (no
 * popups). Layout, top to bottom:
 *
 *   <icon>  <monitored entity name>                       (header)
 *   ------------------------------------------------------
 *   <icon>  Last interaction
 *           <state>                              ● (conf)
 *           by <user> · 2 min ago
 *   ------------------------------------------------------
 *   <icon>  Physical    ● Active   ·   3 clicks           (only if enabled)
 *           last click 12:03
 *   ------------------------------------------------------
 *   History
 *   ● Device                                  16:49:25
 *   ● UI      by Nicola                        16:48:10
 *   … (all 25 entries, scrollable)
 *   ------------------------------------------------------
 *                                              ↻    ⚙     (footer)
 *
 * History source: the trigger-source SENSOR's `history_log` attribute.
 * The user (source_name) is shown as "by <name>" for ui/service entries.
 *
 * Framework-free vanilla JS (no build step).
 *
 * Config:
 *   type: custom:whodidit-card
 *   entity: sensor.<name>_trigger_source
 */

const CARD_VERSION = "2.1.1";

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
    return 5;
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
        (attrs.tracked_entity && attrs.tracked_entity === src.attributes.tracked_entity)
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

  _timeShort(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  _localizeState(slug) {
    const key = `component.whodidit.entity.sensor.trigger_source.state.${slug}`;
    const t = this._hass?.localize?.(key);
    return t || slug;
  }

  /** Subtitle under the main state (device/monitoring: time only;
   *  ui/service: "by <user>"; automation/script/scene: source name). */
  _buildSubtitle(slug, a) {
    const time = this._relTime(a.event_time);
    const name = a.source_name;
    const generic = ["device", "monitoring", ""];
    const isGeneric = !name || generic.includes(String(name).toLowerCase());

    if (slug === "device" || slug === "monitoring") return time;
    if (slug === "ui" || slug === "service") {
      const who = !isGeneric ? name : a.user_id ? `user ${String(a.user_id).slice(0, 8)}` : null;
      return who ? `by ${who} · ${time}` : time;
    }
    return !isGeneric ? `${name} · ${time}` : time;
  }

  /** One history row: coloured dot + type + optional user + time. */
  _historyRow(h) {
    const c = CONFIDENCE_COLORS[h.confidence] || "var(--disabled-text-color)";
    const type = h.source_type === "user" ? "ui" : h.source_type;
    const label = this._localizeState(type);
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
        <span class="h-src">${label}</span>
        ${nameHtml}
        <span class="h-time">${this._timeShort(h.event_time)}</span>
      </div>`;
  }

  // ----- Render -------------------------------------------------------------
  _render() {
    if (!this._config) return;
    const hass = this._hass;
    const src = hass ? hass.states[this._config.entity] : null;

    if (!this.shadowRoot.getElementById("root")) {
      this.shadowRoot.innerHTML = `${this._styles()}<div id="root"></div>`;
    }
    const root = this.shadowRoot.getElementById("root");

    if (!src) {
      root.innerHTML = `<ha-card><div class="empty">Entity <code>${
        this._config.entity || "—"
      }</code> not found.</div></ha-card>`;
      return;
    }

    const a = src.attributes || {};
    const slug = src.state;
    const conf = a.confidence;
    const confColor = CONFIDENCE_COLORS[conf] || "var(--disabled-text-color, #9e9e9e)";
    const icon = STATE_ICONS[slug] || "mdi:help-circle";
    const bs = this._findBinarySensor();
    const trackedName = a.tracked_entity_name || a.friendly_name || this._config.entity;

    // Normalize history_log defensively.
    let rawLog = a.history_log;
    if (typeof rawLog === "string") {
      try {
        rawLog = JSON.parse(rawLog);
      } catch (e) {
        rawLog = [];
      }
    }
    const log = Array.isArray(rawLog) ? rawLog.slice(0, 25) : [];

    // TEMP diagnostics (2.1.1): log what the card actually reads so we can
    // see whether the configured entity carries history_log.
    if (!this._diagLogged) {
      this._diagLogged = true;
      console.info(
        "[whodidit-card] entity:",
        this._config.entity,
        "| state:",
        slug,
        "| history_log type:",
        typeof a.history_log,
        "| history_log length:",
        Array.isArray(a.history_log) ? a.history_log.length : "(not array)",
        "| all attribute keys:",
        Object.keys(a)
      );
    }

    const historyRows = log.length
      ? log.map((h) => this._historyRow(h)).join("")
      : `<div class="h-empty">No interactions recorded yet.</div>`;

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

    const settingsPanel = this._settingsOpen
      ? `
        <div class="divider"></div>
        <div class="settings">
          <div class="settings-title">Settings</div>
          <label class="frow switch">
            <span>Physical-interaction sensor</span>
            <input type="checkbox" id="f-enable" ${bs ? "checked" : ""}/>
          </label>
          <label class="frow">
            <span>Click window (s)</span>
            <input type="number" min="1" max="60" id="f-click" value="${
              bs ? bs.attributes.click_window_seconds ?? 3 : 3
            }"/>
          </label>
          <div class="settings-actions">
            <button class="btn ghost" id="s-cancel">Cancel</button>
            <button class="btn primary" id="s-save">Save</button>
          </div>
        </div>`
      : "";

    root.innerHTML = `
      <ha-card>
        <div class="head">
          <ha-icon class="head-icon" icon="mdi:magnify-scan"></ha-icon>
          <span class="head-name" title="${trackedName}">${trackedName}</span>
        </div>

        <div class="divider"></div>
        <div class="row">
          <ha-icon class="row-icon" icon="${icon}"></ha-icon>
          <div class="row-main">
            <div class="row-line">
              <span class="state">${this._localizeState(slug)}</span>
              <span class="conf-dot" style="background:${confColor}" title="${conf || "unknown"}"></span>
            </div>
            <div class="row-sub">${this._buildSubtitle(slug, a)}</div>
          </div>
        </div>

        ${physicalRow}

        <div class="divider"></div>
        <div class="hist-head">History</div>
        <div class="timeline">${historyRows}</div>

        ${settingsPanel}

        <div class="footer">
          ${
            bs
              ? `<ha-icon-button id="reset-btn" title="Reset physical interaction"><ha-icon icon="mdi:restore"></ha-icon></ha-icon-button>`
              : ""
          }
          <ha-icon-button id="cog-btn" title="Settings"><ha-icon icon="mdi:cog-outline"></ha-icon></ha-icon-button>
        </div>
      </ha-card>`;

    const cog = this.shadowRoot.getElementById("cog-btn");
    if (cog) cog.addEventListener("click", () => this._toggleSettings());
    const reset = this.shadowRoot.getElementById("reset-btn");
    if (reset && bs) reset.addEventListener("click", () => this._callReset(bs.entity_id));
    const cancel = this.shadowRoot.getElementById("s-cancel");
    if (cancel) cancel.addEventListener("click", () => this._toggleSettings());
    const save = this.shadowRoot.getElementById("s-save");
    if (save) save.addEventListener("click", () => this._saveSettings());
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

  _toggleSettings() {
    this._settingsOpen = !this._settingsOpen;
    this._render();
  }

  async _saveSettings() {
    const enableEl = this.shadowRoot.getElementById("f-enable");
    const clickEl = this.shadowRoot.getElementById("f-click");
    if (!enableEl || !clickEl) return;
    const enable = enableEl.checked;
    const click = parseInt(clickEl.value, 10);

    try {
      await this._hass.callService("whodidit", "update_options", {
        entity_id: this._config.entity,
        options: {
          enable_physical_interaction: enable,
          click_window_seconds: isNaN(click) ? 3 : click,
        },
      });
      this._settingsOpen = false;
      this._render();
    } catch (e) {
      console.error("Whodidit: update_options failed", e);
    }
  }

  // ----- Styles -------------------------------------------------------------
  _styles() {
    return `<style>
      ha-card { display: flex; flex-direction: column; overflow: hidden; padding-bottom: 2px; }
      .empty { padding: 16px; color: var(--error-color); }
      .head { display: flex; align-items: center; gap: 10px; padding: 14px 16px 8px 16px; }
      .head-icon { --mdc-icon-size: 22px; color: var(--primary-color); flex: 0 0 auto; }
      .head-name { font-size: 1.1rem; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

      .divider { height: 1px; background: var(--divider-color); margin: 2px 12px; }

      .row { display: flex; align-items: center; gap: 14px; padding: 10px 16px; }
      .row-icon { --mdc-icon-size: 26px; color: var(--primary-color); flex: 0 0 auto; }
      .row-main { flex: 1; min-width: 0; }
      .row-line { display: flex; align-items: center; gap: 8px; }
      .state { font-size: 1.05rem; font-weight: 600; text-transform: capitalize; }
      .conf-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; box-shadow: 0 0 0 2px var(--card-background-color); }
      .row-sub { color: var(--secondary-text-color); font-size: .82rem; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

      .physical .row-line { gap: 6px; font-size: .92rem; }
      .pstate { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
      .pstate .pdot { width: 8px; height: 8px; border-radius: 50%; }
      .pstate.on { color: var(--success-color, #43a047); }
      .pstate.on .pdot { background: var(--success-color, #43a047); }
      .pstate.off { color: var(--secondary-text-color); }
      .pstate.off .pdot { background: var(--disabled-text-color, #9e9e9e); }
      .sep { color: var(--secondary-text-color); }

      .hist-head { font-size: .75rem; text-transform: uppercase; letter-spacing: .05em;
                   color: var(--secondary-text-color); padding: 6px 16px 2px 16px; }
      .timeline { max-height: 260px; overflow-y: auto; padding: 0 16px 6px 16px; }
      .h-row { display: flex; align-items: center; gap: 8px; padding: 5px 0;
               border-bottom: 1px dashed var(--divider-color); font-size: .85rem; }
      .h-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; }
      .h-src { font-weight: 600; text-transform: capitalize; flex: 0 0 auto; }
      .h-name { color: var(--secondary-text-color); flex: 1 1 auto; min-width: 0;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .h-time { color: var(--secondary-text-color); font-size: .78rem; margin-left: auto; flex: 0 0 auto; }
      .h-empty { color: var(--secondary-text-color); padding: 12px 0; text-align: center; font-size: .85rem; }

      .settings { padding: 8px 16px 4px 16px; }
      .settings-title { font-size: .75rem; text-transform: uppercase; letter-spacing: .05em;
                        color: var(--secondary-text-color); margin-bottom: 6px; }
      .frow { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 8px 0; }
      .frow span { font-size: .9rem; }
      .frow input[type=number] { width: 90px; padding: 6px 8px; border-radius: 8px;
              border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: inherit; }
      .frow.switch input { width: 20px; height: 20px; }
      .settings-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
      .btn { border: none; cursor: pointer; padding: 7px 14px; border-radius: 8px; font-size: .85rem; }
      .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .btn.ghost { background: transparent; color: var(--secondary-text-color); }

      .footer { display: flex; justify-content: flex-end; align-items: center; gap: 2px; padding: 2px 6px 6px 6px; }
      .footer ha-icon-button { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
    </style>`;
  }
}

customElements.define("whodidit-card", WhoditCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "whodidit-card",
  name: "Whodidit Card",
  preview: true,
  description: "Minimal card: who/what last triggered an entity, physical clicks and inline history.",
});

console.info(
  `%c WHODIDIT-CARD %c v${CARD_VERSION} `,
  "color:#fff;background:#0288d1;font-weight:700",
  "color:#0288d1;background:#e3f2fd"
);
