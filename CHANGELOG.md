# Changelog

Release notes are read by the release workflow before publishing.

## [Unreleased]

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
