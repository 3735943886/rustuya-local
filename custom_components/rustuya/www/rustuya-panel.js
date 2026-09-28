// The Rustuya sidebar panel (panel.py): the bridge's devices against the cloud list (a simpler cut of rustuya-manager's
// device view: the same categories, order, filters and gateway/sub-device tree), then the files of
// <config>/rustuya_converters and the override pack (the rustuya-manager plugin tab's file API).
//
// Home Assistant sets `hass`, `narrow` and `panel` on the element; every call goes through hass.callApi, which carries
// the user's token. The views are for administrators only (converters run code in Home Assistant's process).

const ORIGIN = { pack: "pack", pack_edited: "pack, edited" };

const STYLE = `
  :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color);
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px;
             background: var(--app-header-background-color, var(--primary-color));
             color: var(--app-header-text-color, var(--text-primary-color)); font-size: 20px; }
  .content { max-width: 1100px; margin: 0 auto; padding: 16px; box-sizing: border-box; }
  .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
          border: 1px solid var(--divider-color); padding: 16px; margin-bottom: 16px; }
  .card.drop { outline: 2px dashed var(--primary-color); outline-offset: -6px; }
  h2 { font-size: 18px; font-weight: 500; margin: 0 0 8px; }
  .muted { color: var(--secondary-text-color); font-size: 14px; }
  .warn { color: var(--warning-color, #b58100); font-size: 14px; }
  .error { color: var(--error-color, #db4437); font-size: 14px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .split { display: grid; grid-template-columns: 260px 1fr; gap: 16px; margin-top: 12px; }
  :host([narrow]) .split { grid-template-columns: 1fr; }
  .files { list-style: none; margin: 0; padding: 0; border: 1px solid var(--divider-color); border-radius: 8px;
           overflow: auto; max-height: 520px; }
  .files li { padding: 8px 10px; cursor: pointer; border-bottom: 1px solid var(--divider-color);
              display: flex; justify-content: space-between; gap: 8px; font-size: 14px; word-break: break-all; }
  .files li:last-child { border-bottom: 0; }
  .files li:hover { background: var(--secondary-background-color); }
  .files li.sel { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.15); }
  .tag { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
  input, textarea { font: inherit; color: var(--primary-text-color); background: var(--card-background-color);
                    border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px 8px; box-sizing: border-box; }
  input { flex: 1; min-width: 160px; }
  input[type=checkbox] { flex: none; min-width: 0; margin: 2px 0 0; }
  textarea { width: 100%; min-height: 420px; margin-top: 8px; resize: vertical; tab-size: 2;
             font-family: var(--code-font-family, ui-monospace, monospace); font-size: 13px; }
  button { font: inherit; font-size: 14px; padding: 6px 14px; border-radius: 6px; cursor: pointer;
           border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  button.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  button:disabled { opacity: .5; cursor: default; }
  .note { margin-top: 8px; }
  /* bridge devices: rustuya-manager's category colors (sky / rose / amber / emerald) */
  .cat-missing { --cat: #0ea5e9; } .cat-orphan { --cat: #f43f5e; } .cat-mismatch { --cat: #f59e0b; } .cat-synced { --cat: #10b981; }
  .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .head h2 { margin: 0; }
  .head .end { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0 8px; }
  .chip { padding: 3px 10px; font-size: 13px; border-radius: 999px; border: 1px solid var(--cat, var(--divider-color));
          background: transparent; color: var(--primary-text-color); }
  .chip.on { background: var(--cat, var(--primary-text-color)); color: #fff; }
  .chip.all.on { background: var(--primary-text-color); color: var(--card-background-color); }
  .chip.zero:not(.on) { opacity: .5; }
  .chip .n { margin-left: 4px; font-variant-numeric: tabular-nums; }
  select { font: inherit; font-size: 13px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color);
           background: var(--card-background-color); color: var(--primary-text-color); }
  .syncbar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
  .syncbar button, .act { border-color: var(--cat, var(--divider-color));
                          background: color-mix(in srgb, var(--cat, transparent) 12%, var(--card-background-color)); }
  .syncbar .all { margin-left: auto; background: var(--primary-text-color); color: var(--card-background-color);
                  border-color: var(--primary-text-color); }
  .devices { display: flex; flex-direction: column; gap: 6px; }
  .dev { border: 1px solid var(--divider-color); border-left: 4px solid var(--cat); border-radius: 8px; padding: 8px 10px;
         background: color-mix(in srgb, var(--cat) 8%, var(--card-background-color)); cursor: pointer; }
  .dev.cat-synced { background: var(--card-background-color); }
  .dev.cat-synced.offline { --cat: #94a3b8; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; box-sizing: border-box; }
  .dot.online { background: #10b981; }
  .dot.offline { border: 2px solid #94a3b8; }
  .dev.child { margin-left: 24px; }
  .dev .top { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .dev .name { font-weight: 500; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .dev .tree { color: var(--secondary-text-color); }
  .dev .acts { margin-left: auto; display: flex; gap: 4px; align-items: center; flex-shrink: 0; }
  .dev .acts button { padding: 2px 8px; font-size: 12px; }
  .dev .id { font-family: var(--code-font-family, ui-monospace, monospace); font-size: 11px; color: var(--secondary-text-color);
             word-break: break-all; }
  .pill { font-size: 11px; padding: 1px 8px; border-radius: 999px; color: #fff; background: var(--cat); white-space: nowrap; }
  .fields { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 2px 12px; margin-top: 6px; font-size: 12px; }
  :host([narrow]) .fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .fields .wide { grid-column: span 2; }
  .fields .full { grid-column: 1 / -1; }
  .dev .err { margin-top: 2px; font-size: 11px; color: var(--error-color, #db4437); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fields b { font-weight: 400; color: var(--secondary-text-color); margin-right: 4px; }
  .fields span { font-family: var(--code-font-family, ui-monospace, monospace); word-break: break-all; }
  .reasons { margin-top: 6px; font-size: 12px; padding: 4px 8px; border-radius: 6px; word-break: break-all;
             border: 1px solid var(--cat); background: color-mix(in srgb, var(--cat) 12%, var(--card-background-color)); }
  .placeholder { border: 2px dashed #0ea5e9; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
  dialog { border: 1px solid var(--divider-color); border-radius: 12px; padding: 16px; width: min(560px, calc(100vw - 32px));
           background: var(--card-background-color); color: var(--primary-text-color); }
  dialog::backdrop { background: rgba(0, 0, 0, .4); }
  dialog h3 { margin: 0 0 8px; font-size: 16px; font-weight: 500; }
  dialog .group { margin: 10px 0 4px; font-size: 13px; font-weight: 500; }
  dialog label { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; padding: 3px 0; word-break: break-all; }
  dialog .plan { max-height: 55vh; overflow: auto; }
  dialog .foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
`;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c ?? "")));
  return node;
}

// hass.callApi rejects with {error, body} (the view's json_message in body.message)
const message = (e) => (e && e.body && e.body.message) || (e && (e.message || e.error)) || String(e);


// ---- bridge devices ------------------------------------------------------------------------------------------------

const CATEGORIES = ["missing", "orphan", "mismatch", "synced"];     // rustuya-manager's order and filter tabs
const RANK = { missing: 0, orphan: 1, mismatch: 2, synced: 3 };
const PLAN_ORDER = ["mismatch", "missing", "orphan"];               // the manager's sync dialog groups
const PLAN = {
  mismatch: { title: "Update on the bridge", verb: "update", button: "Update mismatch" },
  missing: { title: "Add to the bridge", verb: "add", button: "Add missing" },
  orphan: { title: "Remove from the bridge", verb: "remove", button: "Remove orphan" },
};

function stored(key, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem(key));
    return v == null ? fallback : v;
  } catch (e) {
    return fallback;
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) { /* private window or blocked storage: the default next time */ }
}

class BridgeSection {
  constructor(panel) {
    this.panel = panel;
    this.devices = null;                                  // null until the first load
    this.online = {};                                     // the service's link state, with the list
    this.links = {};                                      // live from rustuya/subscribe_links while the panel is open
    this.unsub = null;
    const f = stored("rustuya.filters", CATEGORIES);
    this.filters = new Set(Array.isArray(f) ? f.filter((c) => CATEGORIES.includes(c)) : CATEGORIES);
    this.sort = ["id", "name", "category"].includes(stored("rustuya.sort", "id")) ? stored("rustuya.sort", "id") : "id";
    this.expanded = new Set();
    this.busy = false;

    this.status = el("div", { class: "muted" });
    this.chips = el("div", { class: "chips" });
    this.syncbar = el("div", { class: "syncbar" });
    this.list = el("div", { class: "devices" });
    this.refreshBtn = el("button", { onclick: () => this.load() }, "Refresh");
    const sort = el("select", { title: "Sort devices", onchange: (e) => { this.sort = e.target.value; store("rustuya.sort", this.sort); this.paint(); } },
      ...["id", "name", "category"].map((k) => el("option", { value: k, selected: k === this.sort }, `sort by ${k}`)));
    this.dialog = el("dialog");
    this.root = el("div", { class: "card" },
      el("div", { class: "head" }, el("h2", {}, "Bridge devices"), el("div", { class: "end" }, sort, this.refreshBtn)),
      el("div", { class: "muted" }, "The cloud device list against what rustuya-bridge holds."),
      this.chips, this.syncbar, this.status, this.list, this.dialog);
  }

  async load(selection) {
    if (this.busy) return;
    this.busy = true;
    this.refreshBtn.disabled = true;
    this.status.className = "muted";
    this.status.textContent = selection ? "Sending to the bridge…" : "Reading the bridge…";
    try {
      const r = selection ? await this.panel._api("POST", "bridge", selection) : await this.panel._api("GET", "bridge");
      this.devices = r.devices;
      this.online = r.online || {};
      this.status.textContent = r.cloud_loaded ? "" : "No cloud device list yet: log in to Tuya Cloud from the integration's options.";
      if (selection) this.panel._toast(r.sent ? `Sent ${r.sent} command${r.sent === 1 ? "" : "s"} to the bridge` : "Nothing to send");
    } catch (e) {
      this.status.className = "error";
      this.status.textContent = message(e);
    } finally {
      this.busy = false;
      this.refreshBtn.disabled = false;
    }
    this.paint();
  }

  // Live link state for as long as the panel is on the page: Home Assistant opens its own MQTT listener for this
  // subscription and closes it when we unsubscribe (or the websocket goes). Without it the dots are the service's.
  async watch() {
    if (this.unsub || !this.panel._hass) return;
    this.unsub = "pending";
    let unsub;
    try {
      unsub = await this.panel._hass.connection.subscribeMessage((m) => {
        Object.assign(this.links, m.links || {});
        this.paintSoon();
      }, { type: "rustuya/subscribe_links" });
    } catch (e) {
      this.unsub = null;
      return;
    }
    if (this.unsub === "pending") this.unsub = unsub;
    else unsub();                                         // unwatched while subscribing
  }

  unwatch() {
    if (typeof this.unsub === "function") this.unsub();
    this.unsub = null;
    this.links = {};
  }

  paintSoon() {
    if (this.painting) return;
    this.painting = true;
    requestAnimationFrame(() => { this.painting = false; this.paint(); });
  }

  link(id) {
    if (id in this.links) return this.links[id];
    if (id in this.online) return { online: this.online[id], code: null, message: "" };
    return null;
  }

  count(cat) {
    return (this.devices || []).filter((d) => d.category === cat).length;
  }

  paint() {
    const all = CATEGORIES.every((c) => this.filters.has(c));
    const chip = (key, label, n, on) => el("button", {
      class: `chip ${key === "all" ? "all" : `cat-${key}`}${on ? " on" : ""}${n ? "" : " zero"}`,
      onclick: () => this.toggle(key),
    }, label, el("span", { class: "n" }, n || ""));
    this.chips.replaceChildren(
      chip("all", "all", (this.devices || []).length, all),
      ...CATEGORIES.map((c) => chip(c, c, this.count(c), this.filters.has(c))));

    const pending = PLAN_ORDER.filter((c) => this.count(c));
    this.syncbar.replaceChildren(...(pending.length ? [
      ...["missing", "orphan", "mismatch"].filter((c) => this.count(c)).map((c) =>
        el("button", { class: `cat-${c}`, onclick: () => this.openPlan(c) }, PLAN[c].button)),
      el("button", { class: "all", onclick: () => this.openPlan("all") }, "Apply all")] : []));

    if (!this.devices) {
      this.list.replaceChildren();
      return;
    }
    const entries = this.tree();
    if (!entries.length) {
      this.list.replaceChildren(el("div", { class: "muted" },
        !this.filters.size ? "No category is selected." : this.devices.length ? "No device in the selected categories."
          : "No devices in the cloud list or on the bridge."));
      return;
    }
    const nodes = [];
    for (const e of entries) {
      nodes.push(e.device ? this.card(e.device, false) : this.placeholder(e.id));
      for (const k of e.kids) nodes.push(this.card(k, true));
    }
    this.list.replaceChildren(...nodes);
  }

  toggle(key) {
    if (key === "all") {
      this.filters = CATEGORIES.every((c) => this.filters.has(c)) ? new Set() : new Set(CATEGORIES);
    } else if (this.filters.has(key)) {
      this.filters.delete(key);
    } else {
      this.filters.add(key);
    }
    store("rustuya.filters", [...this.filters]);
    this.paint();
  }

  // Gateways and WiFi devices at the top level with their sub-devices under them; a sub-device whose gateway is in
  // neither list hangs under a placeholder. An entry shows when it or one of its sub-devices passes the filters; a
  // shown gateway shows all its sub-devices, for context (as in rustuya-manager).
  tree() {
    const byId = new Map(this.devices.map((d) => [d.id, d]));
    const side = (d) => d.cloud || d.bridge;
    const kids = new Map();
    const top = [];
    for (const d of this.devices) {
      const s = side(d);
      if (s.type === "SubDevice" && s.parent_id) {
        if (!kids.has(s.parent_id)) kids.set(s.parent_id, []);
        kids.get(s.parent_id).push(d);
      } else {
        top.push(d);
      }
    }
    const entries = top.map((d) => ({ id: d.id, device: d, kids: kids.get(d.id) || [] }));
    for (const d of top) kids.delete(d.id);
    for (const [id, list] of kids) if (!byId.has(id)) entries.push({ id, device: null, kids: list });
    const value = (d) => this.sort === "name" ? (side(d).name || "").toLowerCase()
      : this.sort === "category" ? RANK[d.category] : d.id;
    const cmp = (a, b) => {
      const va = a.device ? value(a.device) : this.sort === "category" ? RANK.missing : a.id;
      const vb = b.device ? value(b.device) : this.sort === "category" ? RANK.missing : b.id;
      return va < vb ? -1 : va > vb ? 1 : 0;
    };
    const out = [];
    for (const e of entries) {
      const shown = e.device ? this.filters.has(e.device.category) : this.filters.has("missing");
      const passing = e.kids.filter((k) => this.filters.has(k.category));
      if (shown || passing.length) out.push({ ...e, kids: shown ? e.kids : passing });
    }
    out.sort(cmp);
    for (const e of out) e.kids.sort((a, b) => cmp({ device: a, id: a.id }, { device: b, id: b.id }));
    return out;
  }

  placeholder(id) {
    return el("div", { class: "placeholder" },
      el("div", { class: "row" }, el("span", { class: "id" }, id), el("span", { class: "pill cat-missing" }, "missing gateway")),
      el("div", { class: "muted" }, "Sub-devices below name this gateway, which is in neither the cloud list nor the bridge."));
  }

  card(d, child) {
    const s = d.cloud || d.bridge;
    const name = s.name && s.name !== "N/A" ? s.name : d.id;
    // the bridge's word on the connection (live while the panel is open, else the service's); none for a missing one
    const ln = d.category === "missing" ? null : this.link(d.id);
    const live = ln ? (ln.online ? "online" : "offline") : null;
    const acts = el("span", { class: "acts" },
      live ? el("span", { class: `dot ${live}`, title: live === "online" ? "Connected to the bridge" : "Not connected to the bridge" }) : "",
      el("span", { class: `pill cat-${d.category}` }, d.category));
    const act = (label, cat, fn) => el("button", { class: `act cat-${cat}`, onclick: (e) => { e.stopPropagation(); fn(); } }, label);
    if (d.category === "missing") acts.append(act("Add", "missing", () => this.one("add", d)));
    if (d.category === "mismatch") acts.append(act("Update", "mismatch", () => this.one("update", d)));
    if (d.category !== "missing") acts.append(act("Remove", "orphan", () => this.one("remove", d)));
    const card = el("div", { class: `dev cat-${d.category}${child ? " child" : ""}${live === "offline" ? " offline" : ""}`,
      title: `${d.category} · ${s.type}${live ? ` · ${live}` : ""}`,
      onclick: () => { this.expanded.has(d.id) ? this.expanded.delete(d.id) : this.expanded.add(d.id); this.paint(); } },
      el("div", { class: "top" }, child ? el("span", { class: "tree" }, "└") : "", el("span", { class: "name" }, name), acts),
      name !== d.id ? el("div", { class: "id" }, d.id) : "");
    const open = this.expanded.has(d.id);
    // a synced card has no other sign of trouble, so its error shows collapsed too (as in rustuya-manager)
    if (!open && d.category === "synced" && live === "offline" && ln.message) card.append(el("div", { class: "err", title: ln.message }, `⚠ ${ln.message}`));
    if (!open) return card;
    // the bridge's value is what it runs with; the cloud's where the bridge does not have the device
    const b = d.bridge || {}, c = d.cloud || {};
    const pick = (k) => (b[k] && b[k] !== "Auto" ? b[k] : c[k] || b[k]) || "—";
    const field = (label, value, wide) => el("div", { class: wide ? "wide" : "" }, el("b", {}, label), el("span", {}, value));
    card.append(el("div", { class: "fields" }, ...(s.type === "SubDevice"
      ? [field("CID", s.cid || "—", true), field("PARENT", s.parent_id || "—", true)]
      : [field("IP", pick("ip")), field("VER", pick("version")), field("KEY", s.key || "—", true),
         ...(ln && ln.message ? [el("div", { class: "full" }, el("b", {}, "MSG"), el("span", {}, ln.code != null ? `${ln.message} (${ln.code})` : ln.message))] : [])])));
    if (d.reasons.length) card.append(el("div", { class: "reasons" }, ...d.reasons.flatMap((r, i) => i ? [el("br"), r] : [r])));
    return card;
  }

  async one(verb, d) {
    const s = d.cloud || d.bridge;
    const who = s.name && s.name !== "N/A" ? `${s.name} (${d.id})` : d.id;
    if (verb === "remove" && !confirm(`Remove ${who} from the bridge?`)) return;
    await this.load({ [verb]: [d.id] });
  }

  openPlan(scope) {
    const groups = PLAN_ORDER.filter((c) => scope === "all" || scope === c)
      .map((c) => [c, this.devices.filter((d) => d.category === c)]).filter(([, list]) => list.length);
    const boxes = [];
    const body = [];
    for (const [c, list] of groups) {
      const all = el("input", { type: "checkbox", checked: true });
      const mine = list.map((d) => {
        const box = el("input", { type: "checkbox", checked: true });
        box.dataset.verb = PLAN[c].verb;
        box.dataset.id = d.id;
        boxes.push(box);
        const s = d.cloud || d.bridge;
        return el("label", {}, box, el("span", {}, `${s.name && s.name !== "N/A" ? s.name : d.id}`,
          el("span", { class: "id" }, ` ${d.id}`), d.reasons.length ? el("div", { class: "muted" }, d.reasons.join("; ")) : ""));
      });
      all.addEventListener("change", () => { for (const b of mine) b.firstChild.checked = all.checked; update(); });
      body.push(el("label", { class: `group cat-${c}` }, all, el("span", { class: "pill" }, PLAN[c].title)), ...mine);
    }
    const apply = el("button", { class: "primary" });
    const update = () => {
      const n = boxes.filter((b) => b.checked).length;
      apply.textContent = n ? `Apply ${n}` : "Apply";
      apply.disabled = !n;
    };
    for (const b of boxes) b.addEventListener("change", update);
    apply.addEventListener("click", async () => {
      const sel = { add: [], update: [], remove: [] };
      for (const b of boxes) if (b.checked) sel[b.dataset.verb].push(b.dataset.id);
      this.dialog.close();
      await this.load(sel);
    });
    update();
    this.dialog.replaceChildren(
      el("h3", {}, scope === "all" ? "Sync with the bridge" : PLAN[scope].title),
      el("div", { class: "plan" }, ...body),
      el("div", { class: "foot" }, el("button", { onclick: () => this.dialog.close() }, "Cancel"), apply));
    this.dialog.showModal();
  }
}

class RustuyaPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._files = [];
    this._selected = null;      // the name of the file in the editor, as loaded (null: a new file)
  }

  set hass(hass) {
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (!this._built) {
      this._built = true;
      this._build();
      this._refresh();
      this._bridge.load();
      if (this.isConnected) this._bridge.watch();
    }
  }

  connectedCallback() {
    if (this._bridge) this._bridge.watch();
  }

  disconnectedCallback() {
    if (this._bridge) this._bridge.unwatch();
  }

  set narrow(narrow) {
    this.toggleAttribute("narrow", !!narrow);
    if (this._menu) this._menu.narrow = narrow;
  }

  _toast(text) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: text }, bubbles: true, composed: true }));
  }

  _api(method, path, body) {
    return this._hass.callApi(method, `rustuya/${path}`, body);
  }

  _build() {
    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this.hasAttribute("narrow");

    this._bridge = new BridgeSection(this);
    this._pack = el("div", { class: "muted" }, "…");
    this._syncBtn = el("button", { onclick: () => this._syncPack() }, "Sync now");
    this._list = el("ul", { class: "files" });
    this._warnings = el("div", { class: "warn note" });
    this._name = el("input", { placeholder: "10_mine.json", spellcheck: "false" });
    this._text = el("textarea", { spellcheck: "false" });
    this._origin = el("div", { class: "muted note" });
    this._text.addEventListener("keydown", (e) => {
      if (e.key === "Tab") {                                          // indent instead of leaving the editor
        e.preventDefault();
        this._text.setRangeText("  ", this._text.selectionStart, this._text.selectionEnd, "end");
      } else if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        this._save();
      }
    });

    this.shadowRoot.replaceChildren(
      el("style", {}, STYLE),
      el("div", { class: "toolbar" }, this._menu, el("span", {}, "Rustuya")),
      el("div", { class: "content" },
        this._bridge.root,
        el("div", { class: "card" },
          el("h2", {}, "Override pack"),
          el("div", { class: "muted" },
            "Fixes for non-standard devices, published between releases and copied into the converters directory."),
          el("div", { class: "row note" }, this._pack, this._syncBtn)),
        this._convertersCard = el("div", { class: "card" },
          el("h2", {}, "Custom converters"),
          el("div", { class: "muted" },
            "Override blocks (*.json) and code converters (*.py) in rustuya_converters/. Saved files apply within ",
            "seconds. Code converters run inside Home Assistant. See tuya2ildevice's README, \"User overrides\". ",
            "Drop files here to copy them in."),
          this._warnings,
          el("div", { class: "split" },
            el("div", {},
              el("div", { class: "row", style: "margin-bottom:8px" },
                el("button", { onclick: () => this._new() }, "New file")),
              this._list),
            el("div", {},
              el("div", { class: "row" },
                this._name,
                el("button", { class: "primary", onclick: () => this._save() }, "Save"),
                el("button", { onclick: () => this._delete() }, "Delete")),
              this._origin,
              this._text)))));
    this._acceptDrops(this._convertersCard);
  }

  // files dragged from the desktop: dropped on the converters card they are copied in (`_import`); dropped anywhere
  // else on the panel they are ignored, rather than the browser leaving Home Assistant to open the file
  _acceptDrops(target) {
    const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
    let depth = 0;                                                    // enter/leave fire for every child crossed
    const done = () => {
      depth = 0;
      target.classList.remove("drop");
    };
    target.addEventListener("dragenter", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      target.classList.add("drop");
    });
    target.addEventListener("dragleave", (e) => {
      if (hasFiles(e) && --depth <= 0) done();
    });
    target.addEventListener("dragover", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    target.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      done();
      this._import([...e.dataTransfer.files]);
    });
    for (const type of ["dragover", "drop"]) {
      this.shadowRoot.addEventListener(type, (e) => {
        if (!hasFiles(e) || e.composedPath().includes(target)) return;
        e.preventDefault();
        if (type === "dragover") e.dataTransfer.dropEffect = "none";
      });
    }
  }

  // each file saved under its own name through the same API and checks as Save (a *.json must parse)
  async _import(files) {
    const saved = [], failed = [];
    for (const file of files) {
      const name = file.name;
      if (!/\.(json|py)$/.test(name)) {
        failed.push(`${name} (only *.json and *.py files are converters)`);
        continue;
      }
      const f = this._files.find((x) => x.name === name);
      if (f && !confirm(f.origin === "pack"
        ? `${name} comes from the override pack. Replaced, it is yours: the pack no longer updates or removes it.`
        : `${name} already exists. Replace it?`)) continue;
      try {
        await this._api("PUT", `converters/${encodeURIComponent(name)}`, { content: await file.text() });
        saved.push(name);
      } catch (e) {
        failed.push(`${name} (${message(e)})`);
      }
    }
    if (saved.length) {
      await this._refresh();
      if (saved.length === 1) await this._open(saved[0]);
    }
    // one notification: Home Assistant shows only the latest
    const parts = [saved.length ? `Copied ${saved.join(", ")}` : "", failed.length ? `Not copied: ${failed.join("; ")}` : ""];
    const text = parts.filter(Boolean).join(". ");
    if (text) this._toast(text);
  }

  async _refresh() {
    let r;
    try {
      r = await this._api("GET", "converters");
    } catch (e) {
      this._list.replaceChildren(el("li", { class: "error" }, `Cannot list the files: ${message(e)}`));
      return null;
    }
    this._files = r.files;
    this._paintList();
    this._warnings.replaceChildren(...(r.warnings || []).map((w) => el("div", {}, w)));
    this._paintPack(r.pack);
    return r;
  }

  _paintList() {
    if (!this._files.length) {
      this._list.replaceChildren(el("li", { class: "muted" }, "No files yet. Built-in fixes apply without any."));
      return;
    }
    this._list.replaceChildren(...this._files.map((f) =>
      el("li", { class: f.name === this._selected ? "sel" : "", onclick: () => this._open(f.name) },
        el("span", {}, f.name), ORIGIN[f.origin] ? el("span", { class: "tag" }, ORIGIN[f.origin]) : "")));
  }

  _paintPack(pack) {
    this._syncBtn.disabled = this._syncing || !pack || !pack.enabled;
    if (this._syncing) {
      this._pack.textContent = "Syncing…";
      this._pack.className = "muted";
      return;
    }
    if (!pack || !pack.enabled) {
      this._pack.textContent = "Off. Turn it on in the integration's options (Tuning).";
      this._pack.className = "muted";
      return;
    }
    const p = pack.status;
    if (!p) {
      this._pack.textContent = "Not synced yet.";
      this._pack.className = "muted";
      return;
    }
    const when = new Date(p.at * 1000).toLocaleString();
    const changes = ["added", "updated", "removed", "kept"].filter((k) => p[k] && p[k].length)
      .map((k) => `${k} ${p[k].join(", ")}`);
    const failed = (p.failed || []).length ? `; failed: ${p.failed.join("; ")}` : "";
    this._pack.textContent = p.error ? `Not synced (${p.error}), ${when}`
      : `Synced ${when}${changes.length ? `: ${changes.join("; ")}` : ""}${failed}`;
    this._pack.className = p.error || failed ? "warn" : "muted";
  }

  _originNote(name) {
    const f = this._files.find((x) => x.name === name);
    const o = f && f.origin;
    this._origin.textContent = o === "pack" ? "From the override pack: the next sync updates it. Saving an edit makes it yours."
      : o === "pack_edited" ? "An edited pack file: the pack no longer updates it. Delete it to get the pack's copy back at the next sync."
      : "";
  }

  async _open(name) {
    try {
      const r = await this._api("GET", `converters/${encodeURIComponent(name)}`);
      this._selected = r.name;
      this._name.value = r.name;
      this._text.value = r.content;
      this._originNote(r.name);
      this._paintList();
    } catch (e) {
      this._toast(message(e));
    }
  }

  _new() {
    this._selected = null;
    this._name.value = "";
    this._text.value = "";
    this._origin.textContent = "";
    this._paintList();
    this._name.focus();
  }

  async _save() {
    const name = this._name.value.trim();
    if (!name) {
      this._toast("Name the file first (*.json or *.py)");
      return;
    }
    const f = this._files.find((x) => x.name === name);
    if (f && f.origin === "pack" &&
        !confirm(`${name} comes from the override pack. Once edited it is yours: the pack no longer updates or removes it.`)) return;
    if (f && name !== this._selected && !confirm(`${name} already exists. Replace it?`)) return;
    try {
      const r = await this._api("PUT", `converters/${encodeURIComponent(name)}`, { content: this._text.value });
      this._selected = name;
      await this._refresh();
      this._warnings.replaceChildren(...(r.warnings || []).map((w) => el("div", {}, w)));
      this._originNote(name);
      this._toast(`Saved ${name}`);
    } catch (e) {
      this._toast(`Not saved: ${message(e)}`);
    }
  }

  async _delete() {
    const name = this._name.value.trim();
    if (!name || !this._files.some((x) => x.name === name)) return;
    if (!confirm(`Delete ${name}?`)) return;
    try {
      await this._api("DELETE", `converters/${encodeURIComponent(name)}`);
      this._new();
      await this._refresh();
      this._toast(`Deleted ${name}`);
    } catch (e) {
      this._toast(`Not deleted: ${message(e)}`);
    }
  }

  async _syncPack() {
    const before = this._files.length, r0 = await this._refresh();
    const at = r0 && r0.pack && r0.pack.status ? r0.pack.status.at : 0;
    try {
      const r = await this._api("POST", "pack");
      if (!r.started) {
        this._toast("The pack is off");
        return;
      }
    } catch (e) {
      this._toast(`Cannot sync: ${message(e)}`);
      return;
    }
    this._syncing = true;
    for (let i = 0; i < 30; i++) {                                   // up to ~30 s; the result lands in pack.status
      await new Promise((ok) => setTimeout(ok, 1000));
      const r = await this._refresh();
      if (!r || (r.pack && r.pack.status && r.pack.status.at > at)) break;
    }
    this._syncing = false;
    await this._refresh();
    if (this._files.length !== before) this._toast("The pack changed the converter files");
  }
}

customElements.define("rustuya-panel", RustuyaPanel);
