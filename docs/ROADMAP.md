# Outstanding work

Updated: 2026-10-13. Baseline: `rustuya-local 0.1.0.dev8`.
See [ARCHITECTURE.md](ARCHITECTURE.md) for components and [REVIEW.md](REVIEW.md) for review evidence.
Completed milestones and earlier deployment records are preserved in the [previous roadmap](history/ROADMAP-2026-10-12.md).

## Review fixes completed for dev8

- [x] Always attempt embedded-bridge cleanup on HA service-stop failure/cancellation; remove the stopped runtime afterward. F1.
- [x] Stop MQTT dispatch, clean up asynchronous handlers, retrieve errors and stop the thread off the event loop. F2.
- [x] Lock HA API reads, patch merging, validation and restart together to preserve concurrent partial saves. F3.
- [x] Validate pack ledger filenames/hashes and directory boundaries; preserve protected files and symlinks. F4.
- [x] Add regression tests for failure, cancellation, concurrent saves and unsafe ownership entries.

## Maintenance

- [ ] Reduce dependence on driver state internals in `Hub._same` and private manager registry APIs through public contracts.
- [ ] Verify Python 3.13 with minimum supported dependency versions. The full CI suite uses 3.14; wheel import/CLI smoke tests pass on 3.13.
- [ ] Define the supported drop-in OS/architectures and provide platform assets as needed. Each archive includes its build host's native library.
- [ ] Verify ownership/conflict policy when manual file editing, IL settings saves and automatic pack updates overlap.

## Real-environment validation

Earlier records include state/availability checks on hk1 with an embedded bridge and one device.
Those checks do not constitute revalidation of this development version.

- [ ] Actual HA + ildevice-homeassistant: reads, commands, rejections, multiple devices/categories, HA/bridge/broker restarts.
- [ ] Actual rustuya-manager: catalog drop-in installation, TLS broker, device additions/removals, plugin tab, offline on shutdown.
- [ ] Upgrade an older HA integration, convert rule layers, verify converter hot reload and preserved cover settings.
- [ ] Validate the four builtin override products: three curtains and window opener `5rta89nj`, including direction, motion and commands.

## Work in other repositories

These are outstanding records carried forward from the previous roadmap; their current repository state has not been checked.

- [ ] rustuya-homeassistant: decide on a final PyPI retirement release and GitHub archival.
- [ ] tuya-interpreter: obtain real payloads for `db_v1_data` and `db_v1_tariff` and verify the parsers.
- [ ] ildevice-homeassistant: decide whether to port legacy names/translations.
