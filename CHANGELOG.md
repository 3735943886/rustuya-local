# Changelog

Release notes are read by the release workflow before publishing.

## [Unreleased]

- Update the embedded IL device hub to ildevice-homeassistant 0.2.1.
- Create the Home Assistant entities in the integration itself, with the ildevice hub embedded on an in-process
  transport. The IL device integration is no longer needed, and the integration publishes no IL on the broker; the
  bridge still needs one. **Existing entities are not taken over**: see *Upgrading from a version before 0.2* in the
  README.
- Remove the IL prefix and source settings from setup and the panel.
- Keep the last values across Home Assistant restarts in the integration's own storage.
- An options change restarts the service only, keeping the entities.
- Allow deleting a Home Assistant device nothing drives any more; one the bridge still holds is refused with a reason.

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
