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
