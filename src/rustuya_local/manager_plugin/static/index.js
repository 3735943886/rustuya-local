// rustuya-local plugin page: what the service is doing (the plugin's state namespace, snapshot.plugins["rustuya-local"],
// pushed by the manager's WebSocket), its settings, and the custom_converters files (/api/rustuya-local/..., api.py).
//
// Mounted by rustuya-manager's plugin host as mount(rootEl, ctx); ctx.getState() / ctx.onState(cb) / ctx.api(path, opts)
// / ctx.toast / ctx.confirm.

const NS = "rustuya-local";
const API = "/api/rustuya-local";
const BTN = "px-3 py-1.5 text-sm rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700";
const INPUT = "px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800";

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

const section = (title, ...body) =>
  el("section", { class: "mb-6" }, el("h2", { class: "text-lg font-semibold mb-2" }, title), ...body);

// ---- status ----------------------------------------------------------------------------------------------------

function paintStatus(box, data) {
  box.replaceChildren();
  if (!data) {
    box.append(el("p", {}, "Waiting for the service to start..."));
    return;
  }
  if (data.error) box.append(el("p", { class: "text-red-600" }, `Error: ${data.error}`));
  if (!data.running) {
    box.append(el("p", {}, "The service is not running."));
    return;
  }
  box.append(el("p", { class: "mb-2" },
    `Bridge root "${data.bridge_root}", IL prefix "${data.il_prefix}". Home Assistant shows these devices through il-ha.`));
  const rows = (data.devices || []).map((d) =>
    el("tr", {}, el("td", {}, d.name), el("td", {}, d.id), el("td", {}, d.kind || "-"),
      el("td", {}, d.online ? "online" : "offline"), el("td", {}, d.props)));
  box.append(el("table", { class: "grid text-sm" },
    el("thead", {}, el("tr", {}, ...["Device", "ID", "Kind", "Link", "Properties"].map((h) => el("th", { class: "text-left pr-4" }, h)))),
    el("tbody", {}, ...rows)));
  if (!rows.length) box.append(el("p", {}, "No device is both in the device list and registered on the bridge."));
  const p = data.pack;
  if (p) {
    const when = new Date(p.at * 1000).toLocaleString();
    const text = p.error ? `Override pack: not synced (${p.error}), ${when}`
      : `Override pack: synced ${when}` + ["added", "updated", "removed", "kept"]
        .filter((k) => p[k] && p[k].length).map((k) => `; ${k} ${p[k].join(", ")}`).join("");
    box.append(el("p", { class: `text-sm mt-2 ${p.error || (p.failed && p.failed.length) ? "text-amber-600" : "text-gray-500"}` }, text));
    for (const f of p.failed || []) box.append(el("p", { class: "text-sm text-amber-600" }, f));
  }
}

// ---- settings --------------------------------------------------------------------------------------------------

const OPTIONS = [
  ["use_quirks", "Apply the built-in fixes for known non-standard devices"],
  ["expose_unused", "Show dps no entity uses, as plain properties"],
  ["allow_hazardous", "Allow controlling hazardous covers (garage doors, gates)"],
  ["pack", "Download fixes for non-standard devices published between releases (into custom converters)"],
];

async function settingsBox(ctx) {
  const box = el("div");
  let s;
  try {
    s = await ctx.api(`${API}/settings`);
  } catch (e) {
    box.append(el("p", { class: "text-red-600" }, `Cannot read the settings: ${e.message}`));
    return box;
  }
  const prefix = el("input", { class: INPUT, value: s.il.prefix });
  const source = el("input", { class: INPUT, value: s.il.source });
  const checks = Object.fromEntries(OPTIONS.map(([k]) => [k, el("input", { type: "checkbox", checked: !!s.options[k] })]));
  const save = async () => {
    const body = { il: { prefix: prefix.value.trim(), source: source.value.trim() },
                   options: Object.fromEntries(OPTIONS.map(([k]) => [k, checks[k].checked])) };
    try {
      await ctx.api(`${API}/settings`, { method: "PUT", body });
      ctx.toast && ctx.toast("Saved; the service restarts with the new settings", "ok");
    } catch (e) {
      ctx.toast && ctx.toast(`Not saved: ${e.message}`, "error");
    }
  };
  box.append(
    el("div", { class: "flex gap-4 mb-2 items-center" },
      el("label", {}, "IL prefix ", prefix), el("label", {}, "Source ", source)),
    el("p", { class: "text-sm text-gray-500 mb-2" },
      "il-ha's integration entry must use the same prefix. Changing it publishes the devices under the new one."),
    ...OPTIONS.map(([k, label]) => el("label", { class: "block text-sm" }, checks[k], " ", label)),
    el("button", { class: `${BTN} mt-2`, onclick: save }, "Save settings"));
  return box;
}

// ---- custom converters -----------------------------------------------------------------------------------------

// f.origin from api.list_converters; a user's own file gets no tag
const ORIGIN = { pack: "(pack)", pack_edited: "(pack, edited)" };

async function convertersBox(ctx) {
  const box = el("div");
  const list = el("div", { class: "mb-2" });
  const warnings = el("div", { class: "text-sm text-amber-600 mb-2" });
  const name = el("input", { class: INPUT, placeholder: "10_mine.json" });
  const text = el("textarea", { class: `${INPUT} w-full font-mono`, rows: "14", spellcheck: "false" });

  const showWarnings = (ws) => {
    warnings.replaceChildren(...(ws || []).map((w) => el("div", {}, w)));
  };
  const open = async (file) => {
    try {
      const r = await ctx.api(`${API}/converters/${encodeURIComponent(file)}`);
      name.value = r.name;
      text.value = r.content;
    } catch (e) {
      ctx.toast && ctx.toast(e.message, "error");
    }
  };
  let origins = {};
  const refresh = async () => {
    try {
      const r = await ctx.api(`${API}/converters`);
      origins = Object.fromEntries(r.files.map((f) => [f.name, f.origin]));
      list.replaceChildren(...(r.files.length
        ? r.files.map((f) => el("button", { class: `${BTN} mr-2 mb-1`, onclick: () => open(f.name) },
            f.name, ORIGIN[f.origin] ? el("span", { class: "ml-1 text-xs text-gray-500" }, ORIGIN[f.origin]) : ""))
        : [el("p", { class: "text-sm" }, "No files yet. Built-in fixes apply without any.")]));
      showWarnings(r.warnings);
    } catch (e) {
      list.replaceChildren(el("p", { class: "text-red-600" }, `Cannot list the files: ${e.message}`));
    }
  };
  const save = async () => {
    const file = name.value.trim();
    if (origins[file] === "pack" && ctx.confirm && !(await ctx.confirm({ title: `Edit ${file}?`,
      body: "It comes from the override pack. Once edited it is yours: the pack no longer updates or removes it." }))) return;
    try {
      const r = await ctx.api(`${API}/converters/${encodeURIComponent(file)}`, { method: "PUT", body: { content: text.value } });
      showWarnings(r.warnings);
      ctx.toast && ctx.toast(`Saved ${file}; the service picks it up within seconds`, "ok");
      await refresh();
    } catch (e) {
      ctx.toast && ctx.toast(`Not saved: ${e.message}`, "error");
    }
  };
  const remove = async () => {
    const file = name.value.trim();
    if (!file) return;
    if (ctx.confirm && !(await ctx.confirm({ title: `Delete ${file}?`, body: "The file is removed from custom_converters." }))) return;
    try {
      await ctx.api(`${API}/converters/${encodeURIComponent(file)}`, { method: "DELETE" });
      name.value = "";
      text.value = "";
      await refresh();
    } catch (e) {
      ctx.toast && ctx.toast(`Not deleted: ${e.message}`, "error");
    }
  };
  // each dropped file saved under its own name through the same API and checks as Save (a *.json must parse)
  const importFiles = async (files) => {
    const saved = [];
    for (const file of files) {
      const fname = file.name;
      if (!/\.(json|py)$/.test(fname)) {
        ctx.toast && ctx.toast(`${fname}: only *.json and *.py files are converters`, "error");
        continue;
      }
      if (fname in origins && ctx.confirm && !(await ctx.confirm(origins[fname] === "pack"
        ? { title: `Replace ${fname}?`, body: "It comes from the override pack. Replaced, it is yours: the pack no longer updates or removes it." }
        : { title: `Replace ${fname}?`, body: "A file of that name is already in custom_converters." }))) continue;
      try {
        await ctx.api(`${API}/converters/${encodeURIComponent(fname)}`, { method: "PUT", body: { content: await file.text() } });
        saved.push(fname);
      } catch (e) {
        ctx.toast && ctx.toast(`${fname} not copied: ${e.message}`, "error");
      }
    }
    if (!saved.length) return;
    await refresh();
    if (saved.length === 1) await open(saved[0]);
    ctx.toast && ctx.toast(`Copied ${saved.join(", ")}; the service picks them up within seconds`, "ok");
  };
  // files dragged from the desktop onto this box are copied in; the outline is inline style, not a Tailwind class the
  // manager's stylesheet may not carry
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  let depth = 0;                                                      // enter/leave fire for every child crossed
  const highlight = (on) => {
    box.style.outline = on ? "2px dashed #3b82f6" : "";
    box.style.outlineOffset = on ? "4px" : "";
  };
  box.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth += 1;
    highlight(true);
  });
  box.addEventListener("dragleave", (e) => {
    if (hasFiles(e) && --depth <= 0) {
      depth = 0;
      highlight(false);
    }
  });
  box.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  box.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    highlight(false);
    importFiles([...e.dataTransfer.files]);
  });
  box.append(
    el("p", { class: "text-sm text-gray-500 mb-2" },
      "Override blocks (*.json) and code converters (*.py, run in the manager's process) for non-standard devices. ",
      "See tuya2ildevice's README, \"User overrides\". Drop files here to copy them in."),
    list, warnings,
    el("div", { class: "flex gap-2 mb-2 items-center" }, el("label", {}, "File ", name),
      el("button", { class: BTN, onclick: save }, "Save"), el("button", { class: BTN, onclick: remove }, "Delete"),
      el("button", { class: BTN, onclick: () => { name.value = ""; text.value = ""; } }, "New")),
    text);
  await refresh();
  return box;
}

// ---- page ------------------------------------------------------------------------------------------------------

export async function mount(rootEl, ctx) {
  const status = el("div");
  const snap = ctx.getState && ctx.getState();
  paintStatus(status, snap && snap.plugins && snap.plugins[NS]);
  const unsub = ctx.onState((s) => {
    const d = s && s.plugins && s.plugins[NS];
    if (d) paintStatus(status, d);
  });
  rootEl.replaceChildren(section("Status", status));
  if (ctx.api) {
    rootEl.append(section("Settings", await settingsBox(ctx)), section("Custom converters", await convertersBox(ctx)));
  }
  return () => unsub && unsub();
}
