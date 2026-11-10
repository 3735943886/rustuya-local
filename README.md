# Rustuya

[![GitHub release](https://img.shields.io/github/v/release/3735943886/rustuya-local)](https://github.com/3735943886/rustuya-local/releases)

Local control of Tuya devices in Home Assistant, with no cloud round trip for state or commands.

Rustuya talks to your devices over the LAN through [rustuya-bridge](https://github.com/3735943886/rustuya-bridge),
interprets them, and creates their Home Assistant devices and entities itself, with the [IL device](https://github.com/3735943886/ildevice-homeassistant)
hub built in.

```
Tuya devices ──LAN──► rustuya-bridge ──MQTT──► Rustuya ──► Home Assistant entities
```

Tuya Cloud is only used once, to fetch your device list and local keys (QR code login from the Smart Life / Tuya Smart app).

## Requirements

- Home Assistant **2025.3** or newer.
- An MQTT broker (for example the **Mosquitto broker** add-on), which rustuya-bridge needs. Rustuya connects to it
  itself; Home Assistant's **MQTT** integration is not required. Without one, Rustuya can run a small broker of its own
  (see *Setup*).
- rustuya-bridge, either one you already run, or the one Rustuya can run inside Home Assistant (amd64 and aarch64).

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=3735943886&repository=rustuya-local&category=integration)

1. In Marketplace, search for **Rustuya** and download it.
2. Restart Home Assistant.
3. *Add integration → Rustuya* (see below).

## Setup

Setup first asks how Rustuya should run. MQTT is the small message hub that devices and programs use to talk to each
other (the broker). If you don't know what it is, pick the first option.

| choice | when |
|---|---|
| **Beginner: everything inside Home Assistant** (recommended) | You don't know MQTT and don't want to set up a broker. Rustuya runs rustuya-bridge and a small broker of its own ([pymqttd](https://github.com/3735943886/pymqttd)) between them, reachable only from this machine. |
| **Expert: my own MQTT broker; Home Assistant runs the bridge** | You already run a broker (for example the Mosquitto add-on). Rustuya starts and owns rustuya-bridge itself. |
| **Expert: my own MQTT broker and my own rustuya-bridge** | You already run both. Setup checks that your running bridge answers on the broker; if it is not on the default topic root `rustuya`, it asks for that root. |

For the two expert choices, enter the broker (host, port, optional username and password).

The built-in broker is for a setup without one. If you have a broker, or plan to share one with other devices, use one
of the expert choices. It runs as a separate process, listens only on `127.0.0.1` with credentials made up at each start,
and is stopped with Home Assistant, so nothing else on your network can use it. What it retains lives in memory: after a
Home Assistant restart the devices show their last values, unavailable, until the bridge has reconnected to them.


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
  - device file
  - embedded bridge's state file and log level

  Saving restarts the integration.

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

## Removing a device

For a device you no longer have:

1. Remove it from the bridge: in the panel's *Devices*, or under *Configure → Sync devices with the bridge* by
   selecting it in *Remove from the bridge*. Rustuya then deletes it from Home Assistant,
   and the bridge clears its own retained topics. Removing a gateway removes its sub-devices too.
2. Once it is gone from the Smart Life / Tuya Smart app (delete it there if it is still listed), fetch the device list
   again (*Configure → Fetch devices from Tuya Cloud*), so it no longer shows as *missing*.

Home Assistant refuses to delete a device the bridge still holds, since it would come right back. Fetching the list again after it is
gone from the app takes it out of Home Assistant, but the bridge keeps it, as *bridge only* in the panel, and keeps
trying to reach it until you remove it from the bridge.

## Upgrading from a version before 0.2

Rustuya used to publish IL over MQTT for the IL device integration. It now creates the entities itself and publishes
nothing on the broker, and it does not take over the old entities. To move, keeping the same entity IDs (areas, custom
names and other registry changes are lost):

1. Before updating, disable the Rustuya entry.
2. Clear what it left on the broker, so IL device deletes those devices. This clears every retained topic under the
   prefix: if another IL producer shares it, restart that producer afterwards, or clear only the topics of your devices.

   ```sh
   mosquitto_sub -h <broker> -t 'il/#' --retained-only --remove-retained -W 3
   ```

3. Update Rustuya, restart Home Assistant and enable the entry. Remove IL device if nothing else uses it.

Done in another order, the old entities keep their IDs and the new ones get a `_2` suffix.

## Removing

Deleting the integration removes its devices from Home Assistant and deletes the device list and Tuya login it kept under `.storage/rustuya/`. With the embedded bridge it also clears that bridge's
retained topics and state file. A device file you pointed elsewhere and everything on an external bridge are left alone.
Then remove Rustuya in HACS and restart.

## Troubleshooting

- **No devices appear**: check the device is registered on the bridge (the panel's *Devices*) and in the device list.
- **Commands or values look doubled**: run only one Rustuya per bridge. The integration cannot see a standalone daemon or
  rustuya-manager plugin on the same bridge, since it no longer shares IL with them over MQTT.
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
