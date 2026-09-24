# rustuya-local

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Local control of Tuya devices through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge), as IL
([ildevice](../ildevice)) devices, and in Home Assistant. It replaces rustuya-homeassistant.

```
rustuya-bridge ──MQTT──► rustuya-local ──MQTT (il/…)──┬─► il-ha (Home Assistant integration)
 raw LAN dps             Service:                      └─► Home Assistant MQTT discovery (optional, no integration)
                         BridgeClient + tuya2ildevice.Hub
```

- **tuya2ildevice** (sibling repo) is the only place that interprets Tuya: Home Assistant core's `tuya` behaviour, the
  user overrides for non-standard devices, and the host parts (runner, transports, file watchers).
- **rustuya-local** is one `Service` (`src/rustuya_local/service.py`) and three thin shells that run it:
  the `rustuya-local run` daemon, the `custom_components/rustuya` Home Assistant integration, and a rustuya-manager
  plugin. Its `discovery` module turns IL into Home Assistant MQTT discovery.
- **il-ha** (sibling repo) turns IL descriptors into Home Assistant entities; discovery plans entities with its core, so
  a device looks the same both ways.

## Daemon

```
uv venv && uv pip install -e ".[discovery]"        # sibling checkouts: see pyproject.toml's [tool.uv.sources]
rustuya-local run --config config.json
```

`config.json` (see `src/rustuya_local/config.py`): the bridge and IL brokers, the IL prefix, the device list, a
`custom_converters` directory, `discovery`, and options for the Hub (`allow_hazardous`, `expose_unused`, `overrides`).
The device list is the `tuyadevices.json` that [rustuya-manager](../rustuya-manager) keeps (its QR-login wizard writes
it, with each device's cloud `category`/`function`/`status_range`/`local_strategy`); rustuya-local follows it as it
changes and never writes it. IL carries only the devices that are also registered on the bridge. The bridge's own
topic templates are read from its retained `{root}/bridge/config`.

## Home Assistant

Three ways, pick one per bridge (two producers, or discovery next to il-ha, show every device twice; the service warns
when another producer is already online):

1. **Discovery, no custom integration**: add `"discovery": {}` to the daemon's config (or run the manager plugin, where
   it is on by default). Home Assistant's own MQTT integration creates the entities.
2. **il-ha**: run the daemon (or the integration below) without discovery and install il-ha.
3. **The `rustuya` integration** (HACS: add this repository as a custom repository of type *Integration*, install
   **Rustuya**, restart, then *Add Integration* → **Rustuya**): the same service tied to a config entry, with the QR
   login wizard, bridge registration and an optional embedded bridge. It creates no entities itself: install il-ha.

### Discovery

`rustuya_local.discovery` keeps one retained config per entity under `<prefix>/<platform>/<node_id>/…/config`, for
the entities il-ha would create (`tests/ha/test_discovery_parity.py` checks this over all 324 core fixtures, and that
the same service calls end in the same IL writes). The configs read and write the IL topics directly. Where Home
Assistant's MQTT platform wants one topic for what IL keeps apart (a cover's open/close/stop, a lock, an alarm panel,
a vacuum, a climate mode with its power, a fan percentage, a light on the JSON schema), the config points at a relay
topic under `relay_prefix` and the service turns those messages into IL writes, so **the service must be running for
those controls**. Configs no device produces any more are cleared shortly after start.

```
rustuya-local discovery status --config config.json [--detail]     # retained configs against the IL devices
rustuya-local discovery clear  --config config.json [--stale-only] --yes     # backs up first
rustuya-local discovery restore --config config.json [--file F] --yes
```

Differences from il-ha that come from Home Assistant's MQTT platforms, not the configs: an MQTT climate always offers
turn on/off, an MQTT select shows an option named `none` as unknown, an MQTT number shows `-30` as `-30.0`, and a value
that goes absent keeps its last value on the platforms that cannot show unknown (light, cover position, select).

### Overrides for non-standard devices

Tuya's cloud schemas are fragmented; fixes go in a `custom_converters/` directory (daemon: `custom_converters` in the
config; integration: `<config>/rustuya_converters`; manager plugin: its data dir), followed live:

- `*.json`: tuya2ildevice override blocks by product or device id: rename a dp, define one the schema lacks, map the
  device's words to the standard ones (`remap.alias`), invert a direction (`remap.invert`), fix labels and classes,
  turn on a code converter. rustuya-homeassistant's `custom_converters` JSON files load as they are.
- `*.py`: code converters (`CONVERTERS = {"name": factory}`).

See tuya2ildevice's README ("User overrides") for the format. A curated set ships in tuya2ildevice.

## rustuya-manager plugin

Installed next to rustuya-manager (`pip install rustuya-local[discovery]` in the manager's environment), it appears as
a **Home Assistant** tab and runs the service under the manager: the manager's broker and bridge root, the manager's
device list (followed), discovery on. Its data dir (`rustuya-local/` next to the manager's plugins) holds an optional
`settings.json` (`il`, `discovery` — `null` for IL only —, `options`) and `custom_converters/`.

## Tests

```
.venv/bin/python -m pytest
```

Everything here needs the sibling repos and, for the last two groups, `mosquitto`, `pyrustuya-bridge` and `tuyamock`
(`pip install -e ".[test,fullstack]"`); those are skipped when missing. Parity with Home Assistant core's fixtures lives in
`tuya2ildevice/tests/chain`, the wire and topic vectors in `ildevice/vectors`.

- `tests/unit/`: the configuration.
- `tests/e2e/test_service.py`: the service on in-memory brokers (overrides directory, discovery, a failed start).
- `tests/e2e/test_chain_*.py`, `test_daemon.py`: the runner with il-ha's consumer model on the other side, in one process
  and over a real broker; the daemon as a subprocess.
- `tests/e2e/test_discovery_*.py`: the discovery publisher (configs, relays, mirrors, sweep) and, through the daemon on
  a real broker, the `discovery status|clear|restore` commands.
- `tests/e2e/test_manager_plugin.py`: the plugin under rustuya-manager's real plugin host (skipped without it).
- `tests/ha/`: the integration (config flow, setup); `test_discovery_parity.py` shows every core fixture through il-ha
  and through discovery in one test Home Assistant and compares states, attributes and the IL writes of service calls.
- `tests/e2e/test_full_stack*.py`: the real rustuya-bridge (`pyrustuyabridge`, in process) and Tuya device emulators
  (`tuyamock`) around the daemon: state, commands, rejections, a device dropping off and returning, a daemon restart, and a
  differential check that 38 real device shapes come out of the whole chain exactly as `tuya2ildevice` computes them.

## Status

Planning, progress and open decisions: `docs/REDESIGN.md`.

Licence: `LICENSE` (MIT). Third-party notices: `THIRD_PARTY_NOTICES.md`.
