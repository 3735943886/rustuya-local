# Rustuya

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/3735943886/rustuya-local)](https://github.com/3735943886/rustuya-local/releases)

Local control of Tuya devices in Home Assistant, with no cloud round trip for state or commands.

Rustuya talks to your devices over the LAN through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge),
interprets them the way Home Assistant's own `tuya` integration does (via
[tuya-interpreter](https://github.com/3735943886/tuya-interpreter)), and publishes them over MQTT as
[IL](https://github.com/3735943886/ildevice) devices. The companion integration
[IL device](https://github.com/3735943886/ildevice-homeassistant) turns them into Home Assistant devices and entities.

```
Tuya devices ──LAN──► rustuya-bridge ──MQTT──► Rustuya ──MQTT (il/…)──► IL device ──► Home Assistant entities
```

Tuya Cloud is only used once, to fetch your device list and local keys (QR code login from the Smart Life / Tuya Smart app).

## Requirements

- Home Assistant **2026.10** or newer.
- An MQTT broker (for example the **Mosquitto broker** add-on) and Home Assistant's **MQTT** integration connected to it.
- The **IL device** integration (in HACS), which creates the entities. Rustuya creates no entities itself.
- rustuya-bridge, either one you already run, or the one Rustuya can run inside Home Assistant (amd64 and aarch64).

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=3735943886&repository=rustuya-local&category=integration)

1. In HACS, search for **Rustuya** and download it. Download **IL device** too.
2. Restart Home Assistant.
3. *Settings → Devices & services → Add integration → IL device*, keeping the topic prefix `il`.
4. *Add integration → Rustuya* (see below).

## Setup

Setup first asks how to reach rustuya-bridge:

| choice | when |
|---|---|
| **An external rustuya-bridge** | You already run rustuya-bridge (or rustuya-manager). Setup checks that the bridge answers on the broker; if it is not on the default topic root `rustuya`, it asks for that root. |
| **Run rustuya-bridge inside Home Assistant** | Nothing else is running. Rustuya starts and owns the bridge itself. It still needs the MQTT broker. |

Then enter the broker (host, port, optional username and password) and the IL topic prefix (`il`, the one IL device reads).

Next, setup offers to **fetch your devices from Tuya Cloud**:

1. Enter the *user code* from Smart Life → Me → Settings → Account and Security (only needed for the first login).
2. Scan the QR code with the Smart Life or Tuya Smart app and submit.
3. Choose which devices to add to, update on, or remove from the bridge. Nothing changes unless you select it.

A saved login is reused next time. You can skip this step by closing the window and run it later from the
integration's **Configure**.

Only devices registered on the bridge are published. They are then discovered by IL device: add them there
(or turn on its *Add discovered devices automatically*).

## The Rustuya panel

*Configure → Add the Rustuya panel to the sidebar* adds an administrator panel. It shows:

- **Devices**: the Tuya Cloud list compared with what the bridge holds. You can filter by *missing*, *bridge only*,
  *mismatch*, *online* and *offline*, see sub-devices under their gateway, add, update or remove devices, and
  *Register manually* (Wi-Fi devices or sub-devices without a cloud login).
- **Converter files**: list, edit, delete or drop in your own rule files (see [Non-standard devices](#non-standard-devices)),
  and *Update builtin rules*.
- **Options**:
  - remote control of locks, alarms and garage doors (off by default)
  - data points Home Assistant core would not classify (off by default)
  - automatic builtin rule updates (on by default)
- **Settings**:
  - bridge topic root
  - IL prefix and source
  - device file
  - embedded bridge's state file and log level

  Saving restarts the integration. Moving the IL prefix or source first clears what the old one left on the broker.

*Hide panel* in its toolbar removes it from the sidebar again. The integration and the panel are available in English and Korean.

## Cover settings

A curtain or blind that moves the wrong way, or reports *opening* / *closing* wrongly, can be fixed from its own
configuration entities. Each appears only where it changes something:

| setting | shown when the cover has | use it when |
|---|---|---|
| *Invert current position* | a position it reports | fully opened, it shows 0% |
| *Invert target position* | a target position, separate from the reported one | *Open* closes it |
| *Invert control* | an open / close command and no target position | *Open* closes it |
| *Use target as current position* | distinct reported and target position sources | the target should supply the displayed position |
| *Motion state source* | a position or control data point | `inferred` (default) infers motion, `control` uses the device's control reports, `none` disables it |
| *Invert reported motion* | the `control` motion source | *opening* and *closing* are reversed |

These change nothing on the device. They are saved to `zz_settings.json` in `<config>/rustuya_converters/` and
survive restarts.

## Non-standard devices

Tuya's cloud schemas are fragmented, and some devices report data points the standard mapping does not know.
Fixes are JSON rule files in `<config>/rustuya_converters/` (or dropped onto the panel), applied live:

```json
{"overrides": {"<product_id>": {"cover": {"invert_position": true}}}}
```

A rule can rename a data point, define one the schema lacks, map the device's words to standard ones, fix labels
and classes, or define a declarative converter. See tuya-interpreter's
[device customization guide](https://github.com/3735943886/tuya-interpreter/blob/master/docs/device-customization.md#user-overrides).
Python code is never loaded.

Official **builtin rules** for known devices are downloaded into `rustuya_converters/rules/builtin/` at start and
daily. A yellow dot on *Update builtin rules* means an update is waiting when automatic updates are off. Updates only
touch files they downloaded, and your own files are applied after them.

If you write a fix for a common device, please contribute it to
[tuya-interpreter](https://github.com/3735943886/tuya-interpreter) so everyone gets it as a builtin rule.

## Service: `rustuya.send_command`

Writes one raw data point to a device, bypassing IL. Fire and forget.

```yaml
action: rustuya.send_command
data:
  device_id: bf0123456789abcdef   # Tuya device id, or the Home Assistant device id
  dp: "1"
  value: true
```

Locks, alarms and garage doors are refused unless their remote control is allowed in the panel's *Options*.

## Removing

Deleting the integration removes its devices from IL (so IL device removes them from Home Assistant) and deletes the
device list and Tuya login it kept under `.storage/rustuya/`. With the embedded bridge it also clears that bridge's
retained topics and state file. A device file you pointed elsewhere and everything on an external bridge are left alone.
Then remove Rustuya in HACS and restart.

## Troubleshooting

- **The integration keeps retrying with "another producer"**: another Rustuya (the standalone daemon or the
  rustuya-manager plugin) already publishes on the same IL prefix and source. Run only one per bridge: stop the
  other one, and this one starts on its next retry.
- **No devices appear**: check the device is registered on the bridge (the panel's *Devices*), that IL device uses the
  same prefix (`il`), and that the device was added in IL device's discovered list.
- **Debug logs**:

  ```yaml
  logger:
    logs:
      custom_components.rustuya: debug
      rustuya_local: debug
  ```

Issues: <https://github.com/3735943886/rustuya-local/issues>

## Other ways to run it

The same service also runs outside Home Assistant, for any IL consumer:

- [Standalone daemon](https://github.com/3735943886/rustuya-local/blob/master/docs/DAEMON.md) (`pip install rustuya-local`)
- [rustuya-manager plugin](https://github.com/3735943886/rustuya-local/blob/master/docs/MANAGER_PLUGIN.md)

Run only one of them per bridge.

For contributors:

- [Architecture](https://github.com/3735943886/rustuya-local/blob/master/docs/ARCHITECTURE.md)
- [Development and tests](https://github.com/3735943886/rustuya-local/blob/master/docs/DEVELOPMENT.md)
- [Changelog](https://github.com/3735943886/rustuya-local/blob/master/CHANGELOG.md)

License: [MIT](https://github.com/3735943886/rustuya-local/blob/master/LICENSE)
