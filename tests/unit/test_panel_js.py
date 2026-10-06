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
