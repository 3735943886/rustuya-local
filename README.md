# rustuya-local

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Local control of Tuya devices through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge), as IL
([ildevice](https://github.com/3735943886/ildevice)) devices, and in Home Assistant. It replaces rustuya-homeassistant.

```
rustuya-bridge ──MQTT──► rustuya-local ──MQTT (il/…)──► il-ha (Home Assistant integration), or any IL consumer
 raw LAN dps             Service:
                         BridgeClient + tuya2ildevice.Hub
```

- **[tuya2ildevice](https://github.com/3735943886/tuya2ildevice)** is the only place that interprets Tuya: Home Assistant core's `tuya` behaviour, the
  user overrides for non-standard devices, and the host parts (runner, transports, file watchers).
- **rustuya-local** is one `Service` (`src/rustuya_local/service.py`) and three thin shells that run it:
  the `rustuya-local run` daemon, the `custom_components/rustuya` Home Assistant integration, and a rustuya-manager
  plugin. It only produces IL and knows nothing of Home Assistant entities.
- **[il-ha](https://github.com/3735943886/ildevice-homeassistant)** turns IL descriptors into Home Assistant entities.

## Daemon

```
uv venv && uv pip install -e .
rustuya-local run --config config.json
```

`config.json` (see `src/rustuya_local/config.py`): the bridge and IL brokers, the IL prefix, the device list, a
`custom_converters` directory, and options for the Hub (`allow_hazardous`, `expose_unused`, `overrides`).
The device list is the `tuyadevices.json` that [rustuya-manager](https://github.com/3735943886/rustuya-manager) keeps (its QR-login wizard writes
it, with each device's cloud `category`/`function`/`status_range`/`local_strategy`); rustuya-local follows it as it
changes and never writes it. IL carries only the devices that are also registered on the bridge. The bridge's own
topic templates are read from its retained `{root}/bridge/config`.

Stopping the daemon only marks it `offline`; its devices stay in IL (retained) for the next start. To retire it, stop it
and run `rustuya-local purge --config config.json`: the retained descriptors, values and presence of its IL prefix and
source are cleared (other sources on the prefix are left alone), so IL consumers drop the devices.

## Home Assistant

Coming from rustuya-homeassistant: [docs/MIGRATING.md](docs/MIGRATING.md).

Install il-ha, and run one IL producer per bridge: the daemon, the integration below, or the manager plugin. A second
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
(so il-ha removes them); with the embedded bridge it also clears that bridge's retained topics and its state file.
The device list and Tuya login it kept under `.storage/rustuya/` are deleted; a device file you pointed elsewhere, and
everything on an external bridge, are left as they are.

### Overrides for non-standard devices

Tuya's cloud schemas are fragmented; fixes go in a `custom_converters/` directory (daemon: `custom_converters` in the
config; integration: `<config>/rustuya_converters`; manager plugin: its data dir), followed live:

- `*.json`: tuya2ildevice override blocks by product or device id: rename a dp, define one the schema lacks, map the
  device's words to the standard ones (`remap.alias`), fix labels and classes, turn on a code converter. A cover's
  direction is not set here but with its settings (below). rustuya-homeassistant's `custom_converters` JSON files load
  as they are.
- `*.py`: code converters (`CONVERTERS = {"name": factory}`).

See tuya2ildevice's README ("User overrides") for the format. A curated set ships in tuya2ildevice, and fixes published
between its releases (its [override pack](https://github.com/3735943886/tuya2ildevice/tree/master/pack)) are copied
into the directory at start and daily, as `00_pack_*` files that your own files refine. Turn that off with `"pack":
false` (daemon), the Rustuya panel's *Options* (integration), or the plugin tab. The pack only replaces or removes the files it
put there, and leaves a file alone once you edit it.

### Cover settings

A curtain or blind whose position runs the other way, or that never shows *opening* / *closing*, is fixed from its
own configuration switches, not a file. Each appears only where it changes something:

| switch | shown when the cover has | turn it on when |
|---|---|---|
| *Invert current position* | a position it reports | fully opened, it shows 0% |
| *Invert target position* | a target position, separate from the reported one | *Open* closes it |
| *Invert control* | an open / close command and no target position | *Open* closes it |
| *Infer motion* (on by default) | a reported position and a target or a command | off: when *opening* / *closing* is wrong |

With one position data point for both, *Invert current position* turns both. A switch changes nothing on the device:
the setting is saved as the device's block in `zz_settings.json` in the converters directory and applied at once, so it
survives a restart and can be edited there too. Without a converters directory (a daemon with no
`custom_converters`) the switches are not offered; `"options": {"device_settings": false}` turns them off.
A block with the same `cover` keys in your own file or the pack sets a model's starting point; the switches win.

In Home Assistant the files can be edited from a sidebar panel: the integration's *Configure* → *Add the Rustuya
panel to the sidebar* adds it and links to it. The panel is for administrators. It fetches the device list from Tuya Cloud, shows the
bridge's devices against the cloud list as rustuya-manager does (missing, bridge only, mismatch, synced, each a filter you can
turn off; sub-devices under their gateway; each device's bridge connection, live while the panel is open) with add /
update / remove. *Register manually* adds Wi-Fi devices or sub-devices without a cloud login, using the same
fields as rustuya-manager. Devices absent from the cloud list appear under *Bridge only*; their removal is not
preselected in bulk sync. The panel lists the converter files with the pack's
marked, edits and deletes them (drop `*.json` / `*.py` files on it to copy them in), runs the pack sync on demand, has the
*Options* (remote control of locks, alarms and garage doors; data points Home Assistant core would not classify; the
override pack), and the *Settings* setup leaves at their defaults: the bridge topic root, the IL prefix and source, the device file, and
the embedded bridge's state file and log level. Saving restarts the integration; moving the IL prefix or source first
clears what the old one left on the broker. *Hide panel* in its toolbar turns it off again. The integration and the panel follow Home Assistant's
language: Korean, or English.

## rustuya-manager plugin

Installed next to rustuya-manager, as a pip package (`pip install rustuya-local` in the manager's environment) or as
the drop-in zip each release carries (`rustuya_local-<version>-dropin.zip`, tuya2ildevice vendored inside, built by
`scripts/build_dropin.py`; unpack it into the manager's plugin directory, or install it from the manager's plugin
catalog once listed there), it appears as a **Tuya (IL)** tab and runs the service under the manager: the manager's broker and bridge root, the manager's device
list (followed). Its data dir (`rustuya-local/` next to the manager's plugins) holds `settings.json` (`il`,
`options`) and `custom_converters/`, and the tab edits both: saved settings restart the service in place, saved
converter files are picked up while it runs. A settings file it cannot use is shown on the tab until it is fixed.

The drop-in zip freezes the tuya2ildevice it was built with: a tuya2ildevice fix reaches manager users with the next
rustuya-local release (bump the patch version and tag; the manager's catalog follows the release). Installed both ways
(pip and the plugin directory), the plugin runs once.

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

Licence: `LICENSE` (MIT).
