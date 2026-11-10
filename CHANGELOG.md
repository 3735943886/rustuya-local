# Changelog

Release notes are read by the release workflow before publishing.

## [Unreleased]

- Add a setup choice for users without an MQTT broker: *Beginner: everything inside Home Assistant* (recommended) runs the
  embedded bridge on a broker of the integration's own ([pymqttd](https://github.com/3735943886/pymqttd), rumqttd in a
  child process), on `127.0.0.1` with per-start credentials. It is first in the setup menu, with the two expert choices
  (your own MQTT broker) after it.
- Fix a scene, or any single action that sets a light's colour and brightness together, changing only the brightness
  and leaving the light on the previous colour or white (#3): require tuya-interpreter 0.1.1, which computes each
  write on the values it has just sent as well as those the device reported.

## [0.2.0]

### Breaking Changes

- **Device and entity management moved from `ildevice` to `rustuya`**:
  Home Assistant entities and devices are now created directly by the Rustuya integration using an embedded IL device hub (via an in-process transport) instead of publishing IL over MQTT to the external IL device integration.
  - The standalone IL device (`ildevice`) integration is no longer needed.
  - **Migration Note**: Existing entities are not automatically taken over. When upgrading from a version before 0.2, delete the existing entity entries from Home Assistant first before updating/enabling the integration. Doing so ensures new entities are created cleanly with their original entity IDs without unintended `_2` suffixes or collisions. (See *Upgrading from a version before 0.2* in the README for details.)
  - Removed IL topic prefix and source settings from the setup flow and management panel.

### Features & Improvements

- Update the embedded IL device hub to `ildevice-homeassistant` 0.2.1 and add support for the image platform.
- Retain the last known values across Home Assistant restarts in the integration's own storage.
- Options flow changes now restart the service only, preserving entities without reloading platforms.
- Allow deleting Home Assistant devices that are no longer driven; devices still held by the bridge are refused with a reason.

## [0.1.4]

- Fix the embedded bridge connecting without a username to a broker that requires one: it passed `mqtt_username`
  where the bridge takes `mqtt_user`, and an unknown option is silently ignored.
- Load `rustuya_local.validation` and the panel's `rustuya_local.manager_plugin.api` in Home Assistant's import
  executor as well, and before the setup flow first checks a topic, instead of on the event loop at first use.

## [0.1.3]

- Load rustuya-manager and pyrustuyabridge in Home Assistant's import executor, once, instead of importing them on
  the event loop from the config flow, the panel and the embedded bridge's start.

## [0.1.2]

- Lower the minimum Home Assistant version to 2025.3.
- Rewrite the README for Home Assistant (HACS) users, with guidance on choosing the IL topic prefix; the daemon,
  manager plugin and development notes move to their own pages under `docs/`.

## [0.1.1]

- Use the Rustuya gear-and-t brand icon in the Home Assistant sidebar, following theme and selection colors.
- Load the sidebar icon on every Home Assistant page, with content-based cache invalidation.

## [0.1.0]

Initial release.

- Local Tuya control through rustuya-bridge and tuya-interpreter, published as IL over MQTT.
- Standalone daemon, Home Assistant integration and rustuya-manager plugin sharing one service.
- Device registration, cloud device import and administrator panels in English and Korean.
- JSON rule layers, builtin rule updates, live device/configuration reload and persistent cover settings.
- Retained device discovery, availability, command validation and source-scoped cleanup.
