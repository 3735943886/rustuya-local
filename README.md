# rustuya-local

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Local control of Tuya devices through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge), as IL
([ildevice](../ildevice)) devices, and in Home Assistant. It replaces rustuya-homeassistant.

```
rustuya-bridge ──MQTT──► rustuya-local ──MQTT (il/…)──► il-ha (Home Assistant integration), or any IL consumer
 raw LAN dps             Service:
                         BridgeClient + tuya2ildevice.Hub
```

- **tuya2ildevice** (sibling repo) is the only place that interprets Tuya: Home Assistant core's `tuya` behaviour, the
  user overrides for non-standard devices, and the host parts (runner, transports, file watchers).
- **rustuya-local** is one `Service` (`src/rustuya_local/service.py`) and three thin shells that run it:
  the `rustuya-local run` daemon, the `custom_components/rustuya` Home Assistant integration, and a rustuya-manager
  plugin. It only produces IL and knows nothing of Home Assistant entities.
- **il-ha** (sibling repo) turns IL descriptors into Home Assistant entities.

## Daemon

```
uv venv && uv pip install -e .        # sibling checkouts: see pyproject.toml's [tool.uv.sources]
rustuya-local run --config config.json
```

`config.json` (see `src/rustuya_local/config.py`): the bridge and IL brokers, the IL prefix, the device list, a
`custom_converters` directory, and options for the Hub (`allow_hazardous`, `expose_unused`, `overrides`).
The device list is the `tuyadevices.json` that [rustuya-manager](../rustuya-manager) keeps (its QR-login wizard writes
it, with each device's cloud `category`/`function`/`status_range`/`local_strategy`); rustuya-local follows it as it
changes and never writes it. IL carries only the devices that are also registered on the bridge. The bridge's own
topic templates are read from its retained `{root}/bridge/config`.

## Home Assistant

Install il-ha, and run one IL producer per bridge: the daemon, the integration below, or the manager plugin (two
producers show every device twice; the service warns when another one is already online).

**The `rustuya` integration** (HACS: add this repository as a custom repository of type *Integration*, install
**Rustuya**, restart, then *Add Integration* → **Rustuya**) is the service tied to a config entry, with the QR login
wizard, bridge registration and an optional embedded bridge. It creates no entities itself.

### Overrides for non-standard devices

Tuya's cloud schemas are fragmented; fixes go in a `custom_converters/` directory (daemon: `custom_converters` in the
config; integration: `<config>/rustuya_converters`; manager plugin: its data dir), followed live:

- `*.json`: tuya2ildevice override blocks by product or device id: rename a dp, define one the schema lacks, map the
  device's words to the standard ones (`remap.alias`), invert a direction (`remap.invert`), fix labels and classes,
  turn on a code converter. rustuya-homeassistant's `custom_converters` JSON files load as they are.
- `*.py`: code converters (`CONVERTERS = {"name": factory}`).

See tuya2ildevice's README ("User overrides") for the format. A curated set ships in tuya2ildevice.

## rustuya-manager plugin

Installed next to rustuya-manager, as a pip package (`pip install rustuya-local` in the manager's environment) or as
the drop-in zip each release carries (`rustuya_local-<version>-dropin.zip`, tuya2ildevice vendored inside, built by
`scripts/build_dropin.py`; unpack it into the manager's plugin directory, or install it from the manager's plugin
catalog once listed there), it appears as a **Tuya (IL)** tab and runs the service under the manager: the manager's broker and bridge root, the manager's device
list (followed). Its data dir (`rustuya-local/` next to the manager's plugins) holds an optional `settings.json`
(`il`, `options`) and `custom_converters/`.

## Tests

```
.venv/bin/python -m pytest
```

Everything here needs the sibling repos and, for the last two groups, `mosquitto`, `pyrustuya-bridge` and `tuyamock`
(`pip install -e ".[test,fullstack]"`); those are skipped when missing. Parity with Home Assistant core's fixtures lives in
`tuya2ildevice/tests/chain`, the wire and topic vectors in `ildevice/vectors`.

- `tests/unit/`: the configuration.
- `tests/e2e/test_service.py`: the service on in-memory brokers (overrides directory, a failed start).
- `tests/e2e/test_chain_*.py`, `test_daemon.py`: the runner with il-ha's consumer model on the other side, in one process
  and over a real broker; the daemon as a subprocess.
- `tests/e2e/test_manager_plugin.py`: the plugin under rustuya-manager's real plugin host (skipped without it).
- `tests/ha/`: the integration (config flow, setup).
- `tests/e2e/test_full_stack*.py`: the real rustuya-bridge (`pyrustuyabridge`, in process) and Tuya device emulators
  (`tuyamock`) around the daemon: state, commands, rejections, a device dropping off and returning, a daemon restart, and a
  differential check that 38 real device shapes come out of the whole chain exactly as `tuya2ildevice` computes them.

## Status

Planning, progress and open decisions: `docs/REDESIGN.md`.

Licence: `LICENSE` (MIT). Third-party notices: `THIRD_PARTY_NOTICES.md`.
