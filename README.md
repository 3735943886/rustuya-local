# rustuya-local

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Local control of Tuya devices through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge), as IL
([ildevice](../ildevice)) devices. It runs without Home Assistant as the standalone `rustuya-local` daemon
(this package); `custom_components/rustuya` is a thin Home Assistant integration on top of the same core, installable
through HACS.

```
rustuya-bridge ──MQTT──► rustuya-local ──MQTT (il/…)──► il-ha / any IL consumer
 raw LAN dps             Runner + tuya2ildevice.Hub      descriptors, values, commands (il-mqtt.md)
```

- **tuya2ildevice** (sibling repo) is the only place that interprets Tuya *and* the host around it: `tuya2ildevice.host`
  runs the Hub on MQTT (Last Will presence, reconnects, runtime add/remove, following `tuyadevices.json`, the bridge's
  own topic templates).
- **rustuya-local** is only the deployment: a configuration file and the `rustuya-local run` daemon on top of that host
  (about 150 lines). It does not depend on il-ha.
- **il-ha** (sibling repo) turns IL descriptors into Home Assistant entities; it never sees Tuya.

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

## Home Assistant (HACS)

`custom_components/rustuya` runs the same `Hub`/`Runner` as the standalone daemon, as a background task tied to a
config entry, with an optional embedded rustuya-bridge. It creates no entities itself — install
[il-ha](../il-ha) (or any IL consumer) separately to turn its devices into entities.

1. HACS → the three-dot menu → **Custom repositories** → add this repository URL with category **Integration**.
2. Install **Rustuya**, restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → **Rustuya**, and follow the config flow.

## Tests

```
.venv/bin/python -m pytest
```

Everything here needs the sibling repos and, for the last two groups, `mosquitto`, `pyrustuya-bridge` and `tuyamock`
(`pip install -e ".[test,fullstack]"`); those are skipped when missing. Parity with Home Assistant core's fixtures lives in
`tuya2ildevice/tests/chain`, the wire and topic vectors in `ildevice/vectors`.

- `tests/unit/`: the configuration.
- `tests/e2e/test_chain_*.py`, `test_daemon.py`: the runner with il-ha's consumer model on the other side, in one process
  and over a real broker; the daemon as a subprocess.
- `tests/e2e/test_full_stack*.py`: the real rustuya-bridge (`pyrustuyabridge`, in process) and Tuya device emulators
  (`tuyamock`) around the daemon: state, commands, rejections, a device dropping off and returning, a daemon restart, and a
  differential check that 38 real device shapes come out of the whole chain exactly as `tuya2ildevice` computes them.

## Status

Planning, progress and open decisions: `docs/REDESIGN.md`. The earlier Home Assistant-only implementation is kept in
`legacy/` (not in git) until the new one is verified; `docs/STATUS.md` describes it.

Licence: `LICENSE` (MIT). Third-party notices: `THIRD_PARTY_NOTICES.md`.
