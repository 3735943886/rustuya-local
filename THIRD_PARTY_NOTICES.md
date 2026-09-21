# Third-party notices

## rustuya-homeassistant (`tuya2ha.v2`)

Entity generation runs on [rustuya-homeassistant](https://github.com/3735943886/rustuya-homeassistant)'s
`rustuya_ha.tuya2ha.v2` package -- a real dependency (`manifest.json`), not vendored code. It is a sans-I/O,
data-driven reproduction of [home-assistant/core](https://github.com/home-assistant/core)'s official `tuya`
integration (Apache-2.0) and of `tuya-device-handlers` (Apache-2.0): its category tables, quirk data and
value-conversion rules are *generated* from those sources (see that repository's `scripts/gen_*.py` and
`tests/golden/`), and it is verified against core's own test fixtures and snapshots. `entity_engine.py` here is
the Home Assistant rendering half. `translations/en.json`'s `entity` section and `icons.json` are copied from
core's `tuya/strings.json` / `tuya/icons.json` (Apache-2.0).

Same author as this project (MIT); the generated data derived from Apache-2.0 sources keeps that notice.

## tuya_sharing (`tuya-device-sharing-sdk`)

`bridge_device.py` and `entity_engine.py` build entities against
`tuya_sharing.CustomerDevice`/`DeviceFunction`/`DeviceStatusRange`'s shape --
plain `SimpleNamespace` subclasses, not a live Tuya Cloud SDK session (see
`bridge_device.build_shadow_device`). Used for its data shape only; no
network code from this package runs as part of this integration.

## rustuya / rustuya-bridge / rustuya-manager

This integration is a client of [rustuya-bridge](https://github.com/3735943886/rustuya-bridge)
(itself built on [rustuya](https://github.com/3735943886/rustuya)),
communicated with over MQTT, and depends on
[rustuya-manager](https://github.com/3735943886/rustuya-manager)'s `Manager`
facade for device registration/removal and the Tuya Cloud QR-login wizard.
No code from any of the three is vendored; `bridge_supervisor.py` optionally
depends at runtime on the `pyrustuyabridge` PyO3 bindings package when the
user opts into embedded mode.

## tuya-device-sharing-sdk value conversion

`tuya2ha.v2.adapter` re-implements (does not vendor) the `default`, `enum` and `dj_v2_*_alg` `value_convert`
strategies of [tuya-device-sharing-sdk](https://github.com/tuya/tuya-device-sharing-sdk) (`strategy_repo/`,
Apache-2.0), plus the inverse write direction the SDK lacks; it is tested against the SDK's own strategies.

## History

Earlier versions vendored HA core's `tuya` platform files (`vendor/ha_core/`), then depended on `tuya2ha` v1's own
heuristic classifier. Both were replaced by `tuya2ha.v2`, which reproduces core's behaviour from data instead of
carrying a copy of its code, and is checked against core's test snapshots (1205 entities, 58 service-call cases).
See git history for the earlier designs.
