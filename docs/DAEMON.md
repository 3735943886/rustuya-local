# Standalone daemon

`rustuya-local run` is the same service the Home Assistant integration runs, without Home Assistant: it reads
rustuya-bridge over MQTT and publishes IL for any IL consumer. For Home Assistant, use the integration instead
(see the [README](../README.md)); run only one IL producer per bridge.

## Install and run

Requires Python 3.13 or newer.

```bash
uv venv --python 3.13
uv pip install rustuya-local
.venv/bin/rustuya-local run --config config.json
```

## Configuration

Create `config.json` beside the device file:

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

| key | meaning |
|---|---|
| `bridge` | The broker rustuya-bridge uses and its topic root. The bridge's own topic templates are read from its retained `{root}/bridge/config`. |
| `il` | The broker, prefix and source to publish IL under. |
| `devices` | The `tuyadevices.json` that [rustuya-manager](https://github.com/3735943886/rustuya-manager) keeps (its QR-login wizard writes it). The daemon follows it as it changes and never writes it. |
| `custom_converters` | Directory of user rule files, followed live (see below). Without it, cover settings are not offered. |
| `pack` | Download builtin rules into `custom_converters/rules/builtin/` at start and daily. `false` disables it. |
| `watch_interval` | Seconds between checks for edited files and downloaded rules; `0` disables polling. |
| `options` | `allow_hazardous` (commands to locks, alarms and garage doors), `expose_unused`, `device_settings` (cover settings, default on), and inline `overrides`, applied after file layers. |

Paths are relative to the configuration file. The full schema is in `src/rustuya_local/config.py`. IL carries only
the devices that are in the device file and also registered on the bridge.

Rule files and cover settings work as described in the README's
[Non-standard devices](../README.md#non-standard-devices) and [Cover settings](../README.md#cover-settings),
with `custom_converters` in place of `<config>/rustuya_converters`.

## Stopping and retiring

Stopping the daemon only marks it `offline`; its devices stay in IL (retained) for the next start. To retire it,
stop it and run:

```bash
rustuya-local purge --config config.json
```

The retained descriptors, values and presence of its IL prefix and source are cleared (other sources on the prefix
are left alone), so IL consumers drop the devices.

A second producer with the same IL prefix and source refuses to start while the first runs; the daemon exits.
