# Code review and fixes — 2026-10-13

Baseline: `rustuya-local 0.1.0.dev7`, starting commit `ed663d1`. The review covered the Python service/host,
HA integration, manager plugin, both JavaScript UIs, tests, packaging, CI and Markdown documentation.
Findings F1–F4 are fixed for `0.1.0.dev8`; F5 was a documentation correction.

## Assessment

The shared Service and transport-independent driver boundaries are sound. All three hosts reuse the same core;
Tuya interpretation and IL consumption are separate responsibilities. Atomic rule-file replacement, serialized
IL settings saves, rule-pack reference management, registry-page validation and failed-device isolation are strengths.

Normal-operation coverage is broad. The original review found gaps in shutdown failures and concurrent API saves;
the fixes below add focused regression coverage. This remains a development version, with real-device validation
and dependency-coupling work outstanding. Passing automated tests does not establish deployed-device correctness.

## Findings and resolutions

### F1 — P1: embedded bridge survived failed HA unload

Original evidence: [`async_unload_entry`](../custom_components/rustuya/__init__.py) removed runtime before awaiting
service shutdown and only stopped the embedded bridge afterward. Command-drain failure or cancellation skipped the
bridge stop. A local AsyncMock reproduction observed zero bridge-stop calls and an already-removed runtime.

**Fixed:** bridge shutdown is attempted in `finally`, then runtime removal runs in a nested `finally`. A stopped
service is not left available for reuse. Errors/cancellation propagate, and resume is marked only after successful
cleanup. Regression tests cover both service errors and cancellation and verify that the bridge still has its
runtime during cleanup.

### F2 — P2: MQTT close left asynchronous handlers running

Original evidence: [`MqttTransport.close`](../src/rustuya_local/host/mqtt.py) only disconnected/stopped paho.
An unfinished async callback remained in `_tasks` after close. Queued dispatch was unguarded and handler exceptions
were discarded without retrieval. Synchronous `loop_stop()` could block the HA event loop.

**Fixed:** closing blocks dispatch and reconnect hooks immediately; shutdown runs disconnect/loop-stop in a worker
thread, cancels/awaits handlers and clears subscriptions/hooks. Repeated close shares one cleanup task. Cancellation
waits for cleanup before propagating. Failed connect also closes its resources, and use of a closed transport raises
an explicit error. [`deliver`](../src/rustuya_local/host/transport.py) retrieves/logs asynchronous handler exceptions.
Regression tests cover queued messages, callback cleanup, repeated/cancelled close, a blocked paho stop, handler
errors, connect timeout and handlers calling close during their own cleanup without a self-wait deadlock.

### F3 — P2: concurrent partial HA saves could lose updates

Original evidence: [`OptionsView.put`, `SettingsView.put`, `_restart`](../custom_components/rustuya/panel.py) merged
current entry values before acquiring the restart lock. Two requests could compute from the same old values, and the
second full replacement could undo the first change. Initially identified by static analysis.

**Fixed:** both request handlers acquire the same settings lock before reading/merging current entry values and hold
it through validation, external-root probing and restart. The restart helper executes under the caller's lock.
Concurrent HTTP regression tests pause the first save, parse the second request while it waits, then confirm that
both options changes or both settings changes survive and that restarts do not overlap.

### F4 — P2: pack ledger could target files outside its directory

Original evidence: [`read_ledger`, `sync`](../src/rustuya_local/host/pack.py) validated manifest names but accepted any
string in the ownership ledger. A temporary ledger containing `../outside.json` and a matching hash caused an
outside file to be deleted. This required a modified/corrupt local ledger; remote exploitation was not established.

**Fixed:** ledger and manifest filenames use full matches, hashes must be 64 hexadecimal characters and bounds must
be integers. Host settings and manifest files cannot be pack-owned. Managed paths must be direct children of the
resolved directory and cannot be symlinks. User links and their targets are preserved rather than adopted.
Regression tests cover relative/absolute escapes, protected files, malformed hashes, trailing newlines, invalid
bounds and symlink preservation during update/removal. Concurrent hostile filesystem replacement is outside this validation contract.

### F5 — documentation: saved cover settings do not always win

[`rule_files`](../src/rustuya_local/host/rules.py) orders all user layers, including `zz_settings.json`, by filename;
inline overrides follow them. A later `zzz_custom.json` changed saved `invert_position=true` to false in the local
reproduction. README, architecture and migration notes now explain actual precedence. Runtime ordering is unchanged.

## Remaining maintenance considerations

- `Hub._same` depends on driver state keys `block`, `delta` and `covers`; check compatibility when dependencies change.
- `manager_registry.ready_registry` patches private manager methods/fields; a public registry contract would reduce coupling.
- Producer probing is a startup check, not atomic exclusion of two simultaneously starting producers.
- Bridge status has no request ID; contiguous-page validation cannot perfectly distinguish all other-client responses.
- Drop-in native libraries match the build host's OS/architecture.
- Atomic replacement prevents partial writes but does not promise power-loss durability through `fsync`.

## Verification

The original review run passed **335 tests with 2 skips** in 178.71 seconds. The skips were host-owned `x-mqtt`
vector cases, not missing test dependencies. The final focused fix suite passed **108 tests**.
The complete post-fix suite passed **351 tests with 2 skips** in 178.19 seconds; the skips are the same `x-mqtt` cases.
Ruff, Markdown language/link checks and `git diff --check` passed after the changes.

The initial system Python lacked dependencies, so tests use an isolated environment outside the repository:

```bash
uv venv /tmp/rustuya-local-review-20261013 --python 3.14
uv pip install --python /tmp/rustuya-local-review-20261013/bin/python \
  -e ../ildevice-homeassistant -e ".[test,test-ha,fullstack,manager]"
/tmp/rustuya-local-review-20261013/bin/python -m pytest -q -rs
ruff check src tests custom_components scripts
```

Environment: Linux aarch64, Python 3.14.4, HA 2026.10.1, tuya-interpreter 0.1.0a3,
pyrustuyabridge 0.4.1, rustuya-manager 0.2.2; mosquitto and Node.js available. The IL consumer is a sibling checkout.
This is not a minimum-dependency-version matrix. The updated wheel was built and installed into an isolated
Python 3.13.13 environment with base dependencies only. Package imports, native preload, CLI help and MQTT callback
cleanup passed without test extras or sibling dependencies. The existing thread-leak test now checks paho connection
threads explicitly, since the event loop's shared executor worker is expected to remain available.

Regression coverage is in [MQTT lifecycle tests](../tests/unit/test_mqtt_lifecycle.py),
[pack tests](../tests/unit/test_pack.py) and [HA panel/unload tests](../tests/ha/test_panel.py).
A deployed HA server, real Tuya devices and external deployment state were not checked.
Follow-up work is in [ROADMAP.md](ROADMAP.md).
