// rustuya-local plugin page: what the service is doing, read from the plugin's state namespace
// (snapshot.plugins["rustuya-local"], pushed by the manager's WebSocket). Read only.
//
// Mounted by rustuya-manager's plugin host as mount(rootEl, ctx); ctx.getState() / ctx.onState(cb).

const NS = "rustuya-local";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c ?? "")));
  return node;
}

function paint(root, data) {
  root.replaceChildren();
  if (!data) {
    root.append(el("p", {}, "Waiting for the service to start..."));
    return;
  }
  if (data.error) root.append(el("p", { class: "error" }, `Error: ${data.error}`));
  if (!data.running) {
    root.append(el("p", {}, "The service is not running."));
    return;
  }
  root.append(
    el("p", {},
      `Bridge root "${data.bridge_root}", IL prefix "${data.il_prefix}". `,
      "Home Assistant shows these devices through il-ha."),
  );
  const rows = (data.devices || []).map((d) =>
    el("tr", {}, el("td", {}, d.name), el("td", {}, d.id), el("td", {}, d.kind || "-"),
      el("td", {}, d.online ? "online" : "offline"), el("td", {}, d.props)));
  root.append(
    el("table", { class: "grid" },
      el("thead", {}, el("tr", {}, ...["Device", "ID", "Kind", "Link", "Properties"].map((h) => el("th", {}, h)))),
      el("tbody", {}, ...rows)),
  );
  if (!rows.length) root.append(el("p", {}, "No device is both in the device list and registered on the bridge."));
}

export async function mount(rootEl, ctx) {
  const snap = ctx.getState && ctx.getState();
  paint(rootEl, snap && snap.plugins && snap.plugins[NS]);
  const unsub = ctx.onState((s) => {
    const d = s && s.plugins && s.plugins[NS];
    if (d) paint(rootEl, d);
  });
  return () => unsub && unsub();
}
