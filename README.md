# rustuya-local

Local control of Tuya devices through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge), as IL
([ildevice](../ildevice)) devices. It runs without Home Assistant; a Home Assistant integration is a thin layer on top
(not written yet, see `docs/REDESIGN.md`).

```
rustuya-bridge ──MQTT──► rustuya-local ──MQTT (il/…)──► il-ha / any IL consumer
 raw LAN dps             Runner + tuya2ildevice.Hub      descriptors, values, commands (il-mqtt.md)
```

- **tuya2ildevice** (sibling repo) is the only place that interprets Tuya: it turns a device's cloud schema and its dps
  into an IL descriptor, values and events, and IL commands back into dps. It does no I/O.
- **rustuya-local** runs it: `Runner` connects a Hub to two transports (the bridge's broker and the IL broker), executes
  its publishes and timers, keeps the Last Will presence (il-mqtt.md M-12) and lets devices be added or removed at
  run time.
- **il-ha** (sibling repo) turns IL descriptors into Home Assistant entities. Its Home Assistant-free `core` is also
  what the tests here use as the consumer.

## Run

```
uv venv && uv pip install -e ".[test]"        # sibling checkouts ../tuya2ildevice and ../il-ha are path dependencies
rustuya-local run --config config.json
```

`config.json` (see `src/rustuya_local/config.py`): the bridge and IL brokers, the IL prefix, the device list and options
for the Hub (`allow_hazardous`, `expose_unused`, `overrides`). The device list is the `tuyadevices.json` that
[rustuya-manager](../rustuya-manager) keeps (its QR-login wizard writes it, with each device's cloud
`category`/`function`/`status_range`/`local_strategy`); rustuya-local follows it as it changes and never writes it. The
bridge's own topic templates are read from its retained `{root}/bridge/config`.

## Tests

```
.venv/bin/python -m pytest
```

- `tests/chain/`: every Home Assistant core tuya fixture (324 devices) through tuya2ildevice and il-ha's planner, compared
  with core's own entity snapshots, per entity (platform, class, category, unit). The differences that remain are listed
  and explained in `test_props.py`.
- `tests/e2e/`: bridge → Runner → IL → consumer model, in one process and over a real `mosquitto` (skipped if it is
  not installed), including the Last Will and reconnect, and the daemon as a subprocess.
- `tests/e2e/test_full_stack*.py`: the real rustuya-bridge (`pyrustuyabridge`, in process) and Tuya device emulators
  (`tuyamock`) around the daemon: state, commands, rejections, a device dropping off and returning, a daemon restart, and a
  differential check that 38 real device shapes come out of the whole chain exactly as `tuya2ildevice` computes them.
- `tests/unit/`: configuration, device file, bridge config.

## Status

Planning, progress and open decisions: `docs/REDESIGN.md`. The earlier Home Assistant-only implementation is kept in
`legacy/` until the new one is verified; `docs/STATUS.md` describes it.

Licence and third-party notices: `THIRD_PARTY_NOTICES.md`.
