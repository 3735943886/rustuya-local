// The Rustuya sidebar panel (panel.py): the files of <config>/rustuya_converters and the override pack, a simpler
// cut of the rustuya-manager plugin tab (src/rustuya_local/manager_plugin/static/index.js) on the same file API.
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
  textarea { width: 100%; min-height: 420px; margin-top: 8px; resize: vertical; tab-size: 2;
             font-family: var(--code-font-family, ui-monospace, monospace); font-size: 13px; }
  button { font: inherit; font-size: 14px; padding: 6px 14px; border-radius: 6px; cursor: pointer;
           border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  button.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  button:disabled { opacity: .5; cursor: default; }
  .note { margin-top: 8px; }
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
    }
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
        el("div", { class: "card" },
          el("h2", {}, "Override pack"),
          el("div", { class: "muted" },
            "Fixes for non-standard devices, published between releases and copied into the converters directory."),
          el("div", { class: "row note" }, this._pack, this._syncBtn)),
        el("div", { class: "card" },
          el("h2", {}, "Custom converters"),
          el("div", { class: "muted" },
            "Override blocks (*.json) and code converters (*.py) in rustuya_converters/. Saved files apply within ",
            "seconds. Code converters run inside Home Assistant. See tuya2ildevice's README, \"User overrides\"."),
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
