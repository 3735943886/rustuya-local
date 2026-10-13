# rustuya-local

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Local control of Tuya devices through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge), as IL
([ildevice](https://github.com/3735943886/ildevice)) devices, and in Home Assistant. It replaces rustuya-homeassistant.

```
rustuya-bridge ──MQTT──► rustuya-local ──MQTT (il/…)──► ildevice-homeassistant (Home Assistant integration), or any IL consumer
 raw LAN dps             Service:
                         BridgeClient + rustuya_local.host.Hub
```

- **[tuya-interpreter](https://github.com/3735943886/tuya-interpreter)** is the only place that interprets Tuya: Home Assistant core's `tuya` behaviour and
  user overrides for non-standard devices, through its Sans-I/O Rust engine and Python wrapper.
- **rustuya-local** is one `Service` (`src/rustuya_local/service.py`) and three thin shells that run it:
  the `rustuya-local run` daemon, the `custom_components/rustuya` Home Assistant integration, and a rustuya-manager
  plugin. Its `host` package owns the runner, transports and file watchers. It only produces IL and knows nothing of Home Assistant entities.
- **[ildevice-homeassistant](https://github.com/3735943886/ildevice-homeassistant)** turns IL descriptors into Home Assistant entities.

## Daemon

```bash
uv venv --python 3.13
uv pip install -e .
.venv/bin/rustuya-local run --config config.json
```

Create `config.json` beside the device file before running:

```json
{
  "bridge": {"host": "localhost", "port": 1883, "root": "rustuya"},
  "il": {"host": "localhost", "port": 1883, "prefix": "il", "source": "tuya"},
  "devices": "tuyadevices.json",
  "custom_converters": "custom_converters",
  "pack": true,
  "watch_interval": 5,
  "options": {"allow_hazardous": false, "expose_unused": false}
}
```

The package requires Python 3.13 or newer. Device and converter paths are relative to the configuration file.
`watch_interval: 0` disables polling; use a positive interval to apply file edits and downloaded rules while running.

`config.json` (see `src/rustuya_local/config.py`): the bridge and IL brokers, the IL prefix, the device list, a
`custom_converters` directory, and options for the Hub (`allow_hazardous`, `expose_unused`, `overrides`).
The device list is the `tuyadevices.json` that [rustuya-manager](https://github.com/3735943886/rustuya-manager) keeps (its QR-login wizard writes
it, with each device's cloud `category`/`function`/`status_range`/`local_strategy`); rustuya-local follows it as it
changes and never writes it. IL carries only the devices that are also registered on the bridge. The bridge's own
topic templates are read from its retained `{root}/bridge/config`.

Stopping the daemon only marks it `offline`; its devices stay in IL (retained) for the next start. To retire it, stop it
and run `rustuya-local purge --config config.json`: the retained descriptors, values and presence of its IL prefix and
source are cleared (other sources on the prefix are left alone), so IL consumers drop the devices.

## tuya-interpreter host boundary

The shared Rust core interprets one device and has no MQTT or rule-file loading API.
`rustuya_local.host.Hub` owns the device registry and maps `TuyaDriver` outputs to IL MQTT wire data;
`host.rules` discovers and loads the bundled builtin rules and user layers, passing decoded rules to Rust for composition.
The service preloads native code and rules in a worker thread before starting devices.
Timers, retained-message policy, bridge `get` requests, presence and settings persistence remain in this host.
MQTT and in-process transport tests exercise the same adapter and the bidirectional Rust driver.

## Home Assistant

Coming from rustuya-homeassistant: [docs/MIGRATING.md](docs/MIGRATING.md).

Install ildevice-homeassistant, and run one IL producer per bridge: the daemon, the integration below, or the manager plugin. A second
one with the same IL prefix and source refuses to start while the first runs (every device would show twice): the
daemon exits, the integration is retried by Home Assistant, the plugin's tab shows it and retries. To move from one to
another, stop the old one first.

**The `rustuya` integration** (HACS: add this repository as a custom repository of type *Integration*, install
**Rustuya**, restart, then *Add Integration* → **Rustuya**) is the service tied to a config entry, with the QR login
wizard, bridge registration and an optional embedded bridge. It creates no entities itself. Setup asks only for the
bridge mode, the broker and the IL prefix (it checks an external bridge answers there, and asks for its topic root if it
is not on the default one), then offers to fetch the devices from Tuya Cloud (a saved login is reused; a QR login
only when there is none), which closing its window skips (it is in *Configure* later, and in the panel); the rest
starts at its defaults and is changed in the panel's *Settings*. Deleting the integration takes its devices out of IL
(so ildevice-homeassistant removes them); with the embedded bridge it also clears that bridge's retained topics and its state file.
The device list and Tuya login it kept under `.storage/rustuya/` are deleted; a device file you pointed elsewhere, and
everything on an external bridge, are left as they are.

### Overrides for non-standard devices

Tuya's cloud schemas are fragmented; fixes go in a `custom_converters/` directory (daemon: `custom_converters` in the
config; integration: `<config>/rustuya_converters`; manager plugin: its data dir), followed live:

- `*.json`: tuya-interpreter override blocks by product or device id: rename a dp, define one the schema lacks, map the
  device's words to the standard ones (`remap.alias`), fix labels and classes, define a declarative converter. A cover's
  direction is not set here but with its settings (below). Each file is a rule object with fixes under `overrides`, for example
  `{"overrides": {"product_id": {"cover": {"invert_position": true}}}}`.
- Converters are JSON programs under `converters.declarative`; Python code plugins are not loaded.

See tuya-interpreter's [device customization guide](https://github.com/3735943886/tuya-interpreter/blob/master/docs/device-customization.md#user-overrides) for the format. Official builtin rules, including `00-default.json`,
ship with tuya-interpreter and update when that package is upgraded. They are applied before your own rule files.

Online updates fetch the latest [builtin rules](https://github.com/3735943886/tuya-interpreter/tree/master/rules/builtin)
into `rules/builtin/` inside the overrides directory at start and daily. In the Rustuya panel, use *Update builtin rules*
to fetch them now or *Automatically update builtin rules* to control automatic updates. Opening the panel checks for
updates: with automatic updates on, available changes are installed immediately without a yellow dot. With automatic
updates off, available changes enable the manual update button with a yellow dot. The button is disabled while checking
or when the installed rules are current. The daemon uses `"pack": false`
to disable automatic updates; the manager plugin has the same option in its settings. Installed builtin rules remain
available when automatic updates are off, and the panel still allows manual updates. Updates only replace or remove
files they downloaded, preserving user edits.

### Cover settings

A curtain or blind whose position runs the other way, or whose *opening* / *closing* state is wrong, can be fixed
from its own configuration switches and motion-source selector. Each appears only where it changes something:

| setting | shown when the cover has | use it when |
|---|---|---|
| *Invert current position* | a position it reports | fully opened, it shows 0% |
| *Invert target position* | a target position, separate from the reported one | *Open* closes it |
| *Invert control* | an open / close command and no target position | *Open* closes it |
| *Use target as current position* | distinct reported and target position sources | the target DP should supply the displayed position |
| *Motion state source* | a position or control DP, with no custom converter owning the motion state | choose `inferred` for motion inference, `control` for control reports, or `none` to disable motion generation; `control` requires a control DP |
| *Invert reported motion* | the `control` motion source | the reported *opening* and *closing* meanings are reversed |

With one position data point for both, *Invert current position* turns both. Motion defaults to `inferred` when
device settings are enabled, unless an override selects another source. A setting changes nothing on the device:
the setting is saved as the device's block in `zz_settings.json` in the converters directory and applied at once, so it
survives a restart and can be edited there too. Without a converters directory (a daemon with no
`custom_converters`) the switches are not offered; `"options": {"device_settings": false}` turns them off.
A block with the same `cover` keys in your own file or the builtin rules sets a model's starting point.
User files, including `zz_settings.json`, are applied in filename order; a later file can override saved settings.
The daemon's inline `options.overrides` is applied after file layers.
Older `cover.infer_motion` and `converters.cover_motion` blocks are upgraded on loading to `cover.state_source`
(`inferred`, or `none` for `infer_motion: false`) and `cover.settle`; see [migration notes](docs/MIGRATING.md).

In Home Assistant the files can be edited from a sidebar panel: the integration's *Configure* → *Add the Rustuya
panel to the sidebar* adds it and links to it. The panel is for administrators. It fetches the device list from Tuya Cloud, shows the
bridge's devices using the running service's complete registry and MQTT connection against the cloud list (missing, bridge only, mismatch, online and offline, each a filter you can
turn off; sub-devices under their gateway; each device's bridge connection, live while the panel is open) with add /
update / remove. Online and offline split devices whose cloud and bridge records match; selecting both shows the
previous synced view. Sub-devices use their own link state, falling back to their gateway when unavailable; an offline
gateway makes its sub-devices offline. Unknown state is grouped under offline. A gateway stays visible for context
when one of its children matches the filters. *Register manually* adds Wi-Fi devices or sub-devices without a cloud login, using the same
fields as rustuya-manager. Devices absent from the cloud list appear under *Bridge only*; their removal is not
preselected in bulk sync. The panel lists, edits and deletes user converter files (drop JSON rule files on it to copy them in),
updates builtin rules on demand, and has the
*Options* (remote control of locks, alarms and garage doors; data points Home Assistant core would not classify; the
automatic builtin rule updates), and the *Settings* setup leaves at their defaults: the bridge topic root, the IL prefix and source, the device file, and
the embedded bridge's state file and log level. Saving restarts the integration; moving the IL prefix or source first
clears what the old one left on the broker. *Hide panel* in its toolbar turns it off again. The integration and the panel follow Home Assistant's
language: Korean, or English.

## rustuya-manager plugin

Installed next to rustuya-manager, as a pip package (`pip install rustuya-local` in the manager's environment) or as
the drop-in zip each release carries (`rustuya_local-<version>-dropin.zip`, tuya-interpreter binding, native library and builtin rules vendored inside, built by
`scripts/build_dropin.py`; unpack it into the manager's plugin directory, or install it from the manager's plugin
catalog once listed there), it appears as a **Tuya (IL)** tab and runs the service under the manager: the manager's broker and bridge root, the manager's device
list (followed). Its data dir (`rustuya-local/` next to the manager's plugins) holds `settings.json` (`il`,
`options`) and `custom_converters/`, and the tab edits both: saved settings restart the service in place, saved
converter files are picked up while it runs. A settings file it cannot use is shown on the tab until it is fixed.

The drop-in zip includes native code for the platform on which it was built; use a matching OS and architecture,
or install with pip to select the appropriate wheel. It freezes the tuya-interpreter it was built with: a tuya-interpreter fix reaches manager users with the next
rustuya-local release (bump the patch version and tag; the manager's catalog follows the release). Installed both ways
(pip and the plugin directory), the plugin runs once.

## Tests

```bash
uv venv --python 3.14
uv pip install -e ../ildevice-homeassistant -e ".[test,test-ha,fullstack,manager]"
.venv/bin/python -m pytest -q
ruff check src tests custom_components scripts
```

The complete suite uses Python 3.14 because the consumer and Home Assistant test dependencies require it.
Install `mosquitto` for broker tests and Node.js for panel JavaScript tests. The command above installs the consumer
from a sibling checkout and other dependencies from the package index; CI follows the same approach.
Missing base or test dependencies can fail collection; optional broker, emulator and fixture tests skip when their
prerequisites are unavailable. The full-stack device comparison uses `../tuya-interpreter/tests/golden`; IL wire and
topic vectors use `../ildevice/vectors` (or the `ILDEVICE` environment variable). Keep those checkouts for these checks.

- `tests/unit/`: configuration validation, host lifecycle, IL vectors, rule loading, settings persistence,
  pack ownership, converter APIs, panel JavaScript and release packaging.
- `tests/e2e/test_service.py`: the service on in-memory brokers (overrides directory, a failed start).
- `tests/e2e/test_chain_*.py`, `test_daemon.py`: the runner with ildevice-homeassistant's consumer model on the other side, in one process
  and over a real broker; the daemon as a subprocess.
- `tests/e2e/test_manager_plugin.py`: the plugin under rustuya-manager's real plugin host (skipped without it).
- `tests/ha/`: the integration (config flow, setup).
- `tests/e2e/test_full_stack*.py`: the real rustuya-bridge (`pyrustuyabridge`, in process) and Tuya device emulators
  (`tuyamock`) around the daemon: state, commands, rejections, a device dropping off and returning, a daemon restart, and a
  differential check that 38 real device shapes come out of the whole chain exactly as `tuya-interpreter` computes them.

## Documentation

- [Current status](docs/STATUS.md): version, document index and verification boundaries.
- [Architecture](docs/ARCHITECTURE.md): current components, lifecycle and rule precedence.
- [Migration](docs/MIGRATING.md): upgrading old converters and rustuya-homeassistant installations.
- [Code review](docs/REVIEW.md): findings and local verification from 2026-10-13.
- [Roadmap](docs/ROADMAP.md): outstanding work and real-device checks.
- [Design history](docs/REDESIGN.md): the September redesign record; historical APIs and versions.
- [Changelog](CHANGELOG.md): changes grouped by release.

Licence: [MIT](LICENSE).
