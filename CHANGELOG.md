# Changelog

All notable changes to this project are documented in this file. A release's GitHub Release notes are its section
here (the release workflow reads it, and refuses to publish a version without one).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are `0.0.x` until the first
stable release.

## [Unreleased]

## [0.0.47] — 2026-09-30

### Changed
- The panel's Fetch from Tuya Cloud opens in a dialog (with its QR code), instead of a box in the bridge card.
- Its form starts with the saved Tuya login's user code, as the config flow's does.

### Fixed
- The fetch form hides while a fetch runs (its row's `display: flex` kept it on screen).

## [0.0.46] — 2026-09-30

### Changed
- Require pyrustuyabridge 0.4 (the stable release, was `0.4.0.dev2`) in the package and the Home Assistant manifest.
  pyrustuyabridge 0.4.0 has the same Python API as `0.4.0.dev2`.

## [0.0.45] — 2026-09-30

### Changed
- Device keys are shown as plain text in the panel's device forms.

## [0.0.44] — 2026-09-30

### Changed
- Simpler panel controls and device forms.

## [0.0.43] — 2026-09-30

### Added
- Registered devices can be edited in the panel.

### Changed
- Panel actions are collapsed.

## [0.0.42] — 2026-09-29

### Fixed
- Cover settings test; the test extra uses httpx2.

## [0.0.41] — 2026-09-29

### Changed
- tuya2ildevice 0.3.16: a cover's state source is a selectable setting (replaces `cover_infer_motion`).

## [0.0.40] — 2026-09-29

### Added
- Cover settings as the device's own switches (tuya2ildevice 0.3.15).

## [0.0.39] — 2026-09-29

### Added
- Manual bridge device registration.

## [0.0.38] — 2026-09-29

### Fixed
- The panel is not registered twice.

## [0.0.37] — 2026-09-28

### Changed
- Refactor, no behaviour change (the Options card says "Nothing changed" rather than reloading).

## [0.0.36] — 2026-09-28

### Changed
- A restart no longer asks every device for its state (tuya2ildevice 0.3.13).

## [0.0.35] — 2026-09-28

### Fixed
- A restart from the panel or an options change no longer makes every device flap (tuya2ildevice 0.3.12).
- The panel's module URL carries the file's hash, so a changed panel is never taken from a browser's cache.

## [0.0.34] — 2026-09-28

### Added
- `rustuya.send_command`: raw DPs by Tuya or Home Assistant device id. Locks, alarms and garage doors are refused
  unless allowed.

## [0.0.33] — 2026-09-28

### Added
- The panel in Korean too, following Home Assistant's language.

## [0.0.32] — 2026-09-28

### Added
- Korean translation.

### Fixed
- Configure no longer removes the panel.

## [0.0.31] — 2026-09-28

### Fixed
- Panel saves answer after the restart, the panel survives restarts, quieter Hide.

## [0.0.30] — 2026-09-28

### Added
- Cloud fetch in the panel, the panel one click from Configure, save only when changed.

## [0.0.29] — 2026-09-28

### Fixed
- Setup and login paths (no device file yet, embedded bridge during setup, saved Tuya login).

## [0.0.28] — 2026-09-28

### Added
- The options other than the panel in the panel's Options card.

## [0.0.27] — 2026-09-28

### Added
- A short Home Assistant setup, panel Settings and Hide panel, drop-in converter files.

## [0.0.26] — 2026-09-28

### Added
- External bridge check in setup; deleting cleans up IL, the embedded bridge and owned files.

## [0.0.25] — 2026-09-28

### Added
- The Home Assistant sidebar panel (bridge devices, converters, pack).

## [0.0.24] — 2026-09-27

### Changed
- tuya2ildevice 0.3.11: events fire only from `active`, as in rustuya-homeassistant.

## [0.0.23] — 2026-09-27

### Fixed
- tuya2ildevice 0.3.10: the bridge's `state` copy no longer settles a curtain's move.

## [0.0.22] — 2026-09-27

### Changed
- tuya2ildevice 0.3.9: cover position as the bridge shows it; live passive changes are pushes.

## [0.0.21] — 2026-09-27

### Fixed
- tuya2ildevice 0.3.8: a device refused at start is driven once the converters file is fixed.

## [0.0.20] — 2026-09-27

### Changed
- tuya2ildevice 0.3.7: properties defined from dps, writable converters, curtain `cover_state`.

## [0.0.19] — 2026-09-27

### Changed
- Bridge commands rendered by `pyrustuyabridge.render_command`.

## [0.0.18] — 2026-09-27

### Fixed
- Take only the bridge's answers to this client's own status request.

## [0.0.17] — 2026-09-27

### Fixed
- One rustuya-manager session at a time in the config and options flows.

## [0.0.16] — 2026-09-27

### Changed
- Installable in Home Assistant (rustuya-manager 0.2.0 is on PyPI); no upper version caps.

## [0.0.15] — 2026-09-27

### Changed
- Keep the rustuya-local name (repository rename reverted).

## [0.0.14] — 2026-09-27

### Added
- Refuse a second IL producer; override pack.

## [0.0.13] — 2026-09-27

### Added
- The plugin tab edits settings and converters; migration guide; package smoke CI.

## [0.0.12] — 2026-09-27

### Added
- README on PyPI.

## [0.0.11] — 2026-09-24

### Changed
- tuya2ildevice 0.3.3.

## [0.0.10] — 2026-09-24

### Fixed
- Presence survives a producer handover; no blocking I/O in Home Assistant's loop.

## [0.0.9] — 2026-09-24

### Changed
- tuya2ildevice 0.3.1; ruff 0.16 clean and checked in CI.

## [0.0.8] — 2026-09-24

### Fixed
- Register the manager plugin once when installed both ways.

## [0.0.7] — 2026-09-24

### Added
- rustuya-manager drop-in zip.

## [0.0.1] … [0.0.6] — 2026-09-23 … 2026-09-24

Initial releases; see `git log`.
