# Current status

Updated: 2026-10-18. Baseline: `0.1.0.dev9`.
The state of deployed servers has not been checked.

## Implementation

The chain is `rustuya-bridge → rustuya-local → IL MQTT → ildevice-homeassistant / other IL consumers`.
The daemon, Home Assistant integration and rustuya-manager plugin use the same `Service`.
`tuya-interpreter` provides the Sans-I/O Rust driver. `rustuya_local.host` owns the registry, transports, timers,
rule files and settings persistence. It does not create HA entities or publish MQTT discovery.

| Component | Declared requirement |
|---|---|
| rustuya-local | `0.1.0.dev9` |
| Python | Base package `>=3.13`; full tests use 3.14 |
| tuya-interpreter | `>=0.1.0a2` |
| pyrustuyabridge | `>=0.4` |
| paho-mqtt | `>=2.0` |
| rustuya-manager | `>=0.2.3` in the manager test extra and HA manifest |

These are minimum requirements; installed versions may differ.

## Verification scope

The 2026-10-13 review, fixes and automated verification are recorded in [REVIEW.md](REVIEW.md).
Broker and Tuya-emulator tests are distinct from testing a deployed HA server and real devices.
Real-device commands, multiple categories, TLS and restart checks remain in [ROADMAP.md](ROADMAP.md).

## Documentation index

| Document | Purpose |
|---|---|
| [README](../README.md) | Installation, operation and test setup |
| [ARCHITECTURE](ARCHITECTURE.md) | Current components and file precedence |
| [MIGRATING](MIGRATING.md) | Legacy converters and rustuya-homeassistant migration |
| [REVIEW](REVIEW.md) | Assessment, findings, fixes and verification |
| [ROADMAP](ROADMAP.md) | Outstanding work |
| [CHANGELOG](../CHANGELOG.md) | Changes by release |
| [REDESIGN](REDESIGN.md) | Original redesign history |
| [Previous roadmap](history/ROADMAP-2026-10-12.md) | Archived milestone history |
