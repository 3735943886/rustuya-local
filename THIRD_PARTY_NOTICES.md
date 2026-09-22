# Third-party notices

This repository is config and a daemon/HA integration only: it contains no vendored or reproduced third-party
code. The Apache-2.0-derived reproduction of Home Assistant core's `tuya` integration, tuya-device-handlers and
tuya-device-sharing-sdk that this project used to carry directly (`entity_engine.py`, `bridge_device.py`,
`rustuya_ha.tuya2ha.v2`) moved out during the redesign to `tuya2ildevice` — see
[that repository's THIRD_PARTY_NOTICES.md](https://github.com/3735943886/tuya2ildevice/blob/main/THIRD_PARTY_NOTICES.md)
for those notices; nothing of it remains here.

## rustuya / rustuya-bridge / rustuya-manager / tuya2ildevice

This project is a client, over MQTT, of [rustuya-bridge](https://github.com/3735943886/rustuya-bridge) (itself
built on [rustuya](https://github.com/3735943886/rustuya)), converts what it publishes with
[tuya2ildevice](https://github.com/3735943886/tuya2ildevice), and depends on
[rustuya-manager](https://github.com/3735943886/rustuya-manager)'s `Manager` facade for device
registration/removal and the Tuya Cloud QR-login wizard, used only by `custom_components/rustuya_local`'s config
and options flows. No code from any of these is vendored. `custom_components/rustuya_local/bridge_supervisor.py`
optionally depends at runtime on the `pyrustuyabridge` PyO3 bindings package, only when the user opts into
embedded-bridge mode; in external-bridge mode this integration never imports it (the topic layer it talks over is
`tuya2ildevice`'s own, not `pyrustuyabridge`).

## History

Earlier versions of this repository generated Home Assistant entities directly (`tuya2ha.v2`, later moved here as
`rustuya_ha.tuya2ha.v2`); see git history for that design. The current architecture produces IL
(`ildevice`/`il-ha`'s Instance Layer) instead: this repository has no Home Assistant entity code and creates no
entities itself. All Apache-2.0-derived attribution now lives with the code it actually describes, in
`tuya2ildevice`.
