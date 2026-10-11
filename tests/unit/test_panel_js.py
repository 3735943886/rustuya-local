"""Run the panel's polling lifecycle against a small DOM/timer stub."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_detached_panel_stops_polling_even_with_an_inflight_reply():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = Path("custom_components/rustuya/www/rustuya-panel.js").read_text()
    cloud = source[source.index("class CloudFetchBox {"):source.index("class Bridge", source.index("class CloudFetchBox {"))]
    script = r'''
const assert = require("node:assert/strict");
let scheduled = 0;
global.setTimeout = () => { scheduled++; return 1; };
global.clearTimeout = () => {};
const t = x => x;
const message = x => String(x);
(async () => {
  let resolve;
  const box = Object.create(CloudFetchBox.prototype);
  Object.assign(box, { active: true, generation: 0, timer: null,
    panel: { _api: () => new Promise(r => resolve = r) },
    root: { close() {} }, qr: {}, msg: {}, form: {}, code: {} });
  const pending = box.poll(false);
  box.close();
  resolve({ state: "working" });
  await pending;
  box.schedule();
  assert.equal(scheduled, 0);
  assert.equal(box.active, false);
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
    result = subprocess.run([node, "-e", cloud + script], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_device_card_preserves_selection_and_detail_clicks():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = Path("custom_components/rustuya/www/rustuya-panel.js").read_text()
    bridge = source[source.index("class BridgeSection {"):source.index("class SavedSection {")]
    script = r'''
const assert = require("node:assert/strict");
const t = x => x;
const side = d => d.bridge;
const nameOf = d => d.bridge.name;
const el = (tag, attrs = {}, ...children) => ({ tag, ...attrs, children,
  append(...items) { this.children.push(...items); } });
let selection = { isCollapsed: false };
global.window = { getSelection: () => selection };
const section = Object.create(BridgeSection.prototype);
let paints = 0;
Object.assign(section, { expanded: new Set(["device"]), link: () => null,
  paint: () => paints++ });
const card = section.card({ id: "device", category: "missing", reasons: [],
  bridge: { name: "Device", type: "Device", key: "secret" } }, false);
const event = (detail, shadow = true) => ({
  currentTarget: { getRootNode: () => shadow ? { getSelection: () => selection } : {} },
  target: { closest: () => detail ? {} : null },
});
card.onclick(event(false));
card.onclick(event(false, false));
assert.equal(paints, 0);
assert.equal(section.expanded.has("device"), true);
selection = { isCollapsed: true };
card.onclick(event(true));
assert.equal(paints, 0);
card.onclick(event(false));
assert.equal(section.expanded.has("device"), false);
card.onclick(event(false));
assert.equal(section.expanded.has("device"), true);
assert.equal(paints, 2);
'''
    result = subprocess.run([node, "-e", bridge + script], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_pack_button_requires_confirmed_update_and_checks_without_sync():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = Path("custom_components/rustuya/www/rustuya-panel.js").read_text()
    panel = source[source.index("class RustuyaPanel extends HTMLElement {"):source.index('// a page that had')]
    script = r'''
const assert = require("node:assert/strict");
(async () => {
  const panel = Object.create(RustuyaPanel.prototype);
  let dot = false;
  Object.assign(panel, { _pack: {}, _syncBtn: { classList: {
    toggle(name, value) { dot = value; }
  } } });
  const state = { enabled: false, status: null };
  panel._paintPack(state);
  assert.equal(panel._syncBtn.disabled, true);
  panel._packCheck = { available: false };
  panel._paintPack(state);
  assert.equal(panel._syncBtn.disabled, true);
  assert.equal(panel._pack.textContent, "pack_current");
  panel._api = async (method, path) => {
    assert.equal(method, "GET"); assert.equal(path, "pack");
    assert.equal(panel._syncBtn.disabled, true);
    return { available: true };
  };
  await panel._checkPack();
  assert.equal(panel._syncBtn.disabled, false);
  assert.equal(dot, true);
  panel._syncing = true;
  panel._paintPack(state);
  assert.equal(panel._syncBtn.disabled, true);
  assert.equal(dot, false);
  panel._syncing = false;
  panel._api = async () => { throw Error("offline"); };
  await panel._checkPack();
  assert.equal(panel._syncBtn.disabled, true);
  assert.equal(dot, false);
  let syncs = 0;
  panel._syncPack = async () => { syncs++; };
  panel._api = async () => ({ available: true });
  panel._paintPack({ enabled: true });
  await panel._checkPack();
  assert.equal(syncs, 1);
  assert.equal(dot, false);
  await panel._checkPack(false);
  assert.equal(syncs, 1);
  panel._paintPack({ enabled: false });
  await panel._checkPack();
  assert.equal(syncs, 1);
  panel._packCheck = { available: true };
  panel._paintPack({ enabled: false });
  assert.equal(panel._syncBtn.disabled, false);
  assert.equal(dot, true);
  panel._packCheck = { available: false };
  panel._paintPack({ enabled: false });
  assert.equal(panel._syncBtn.disabled, true);
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
    result = subprocess.run([node, "-e", 'const HTMLElement = class {}; const t = x => x; const message = String;\n' + panel + script],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_online_offline_filters_resolve_subdevices_and_keep_gateway_context():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = Path("custom_components/rustuya/www/rustuya-panel.js").read_text()
    constants = source[source.index('const CATEGORIES ='):source.index('function stored(')]
    bridge = source[source.index('class BridgeSection {'):source.index('class SavedSection {')]
    script = r'''
const assert = require("node:assert/strict");
const el = () => ({ addEventListener() {} });
const t = x => x;
const stored = (key, fallback) => key === "rustuya.filters" ? ["missing", "synced"] : fallback;
const CloudFetchBox = class { constructor() { this.root = {}; } };
const migrated = new BridgeSection({});
assert.deepEqual([...migrated.filters], ["missing", "online", "offline"]);
const section = Object.create(BridgeSection.prototype);
const device = (id, parent, category = "synced") => ({ id, category, bridge: {
  name: id, type: parent ? "SubDevice" : "WiFi", parent_id: parent
} });
const devices = [device("gw"), device("on", "gw"), device("off", "gw"),
  device("inherited", "gw"), device("unknown"), device("missing", "gw", "missing"),
  device("absent-child", "absent"), device("wifi")];
Object.assign(section, { devices, links: {}, online: { gw: true, on: true, off: false, wifi: true },
  filters: new Set(["online", "offline"]), sort: "category" });
assert.equal(section.count("online"), 4);
assert.equal(section.count("offline"), 3);
assert.equal(section.count("missing"), 1);
assert.equal(section.category(devices[3]), "online");
assert.equal(section.category(devices[4]), "offline");
assert.deepEqual(section.tree().find(e => e.id === "gw").kids.map(d => d.id).sort(),
  ["inherited", "missing", "off", "on"]);
section.filters = new Set(["online"]);
assert.deepEqual(section.tree().find(e => e.id === "gw").kids.map(d => d.id).sort(), ["inherited", "on"]);
assert.equal(section.tree().some(e => e.id === "unknown"), false);
section.filters = new Set(["offline"]);
assert.deepEqual(section.tree().find(e => e.id === "gw").kids.map(d => d.id), ["off"]);
assert.equal(section.tree().find(e => e.id === "absent").device, null);
section.links.gw = { online: false, message: "disconnected" };
assert.equal(section.category(devices[1]), "offline");
assert.equal(section.category(devices[3]), "offline");
assert.equal(section.count("online"), 1);
assert.equal(section.count("offline"), 6);
section.links.gw = { online: true, message: "" };
section.links.off = { online: true, message: "" };
assert.equal(section.category(devices[2]), "online");
section.devices.push(device("cycle-a", "cycle-b"), device("cycle-b", "cycle-a"));
assert.equal(section.category(section.devices.at(-1)), "offline");
section.filters.clear();
assert.deepEqual(section.tree(), []);
'''
    result = subprocess.run([node, "-e", constants + bridge + script], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
