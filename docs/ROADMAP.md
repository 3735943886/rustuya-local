# Release readiness

Current release: `0.1.1`. Home Assistant installation is in the [README](../README.md); the [daemon](DAEMON.md) and
[manager plugin](MANAGER_PLUGIN.md) have their own pages.
The [architecture](ARCHITECTURE.md) describes the runtime boundaries.

## Automated verification

Verified for the release candidate on 2026-10-20 (Linux aarch64):

| Check | Result |
|---|---|
| Python 3.14 full suite, tuya-interpreter 0.1.0, bridge 0.4.4, manager 0.2.3 | 363 passed, 2 skipped |
| Python 3.13 core unit tests, interpreter 0.1.0, bridge 0.4.0, paho-mqtt 2.0.0 | 159 passed, 3 skipped |
| Ruff 0.16.8 and installed dependency compatibility | Passed |
| Clean Python 3.13 wheel installation with PyPI dependencies, imports, native preload and CLI | Passed |
| Wheel and sdist build, strict metadata validation | Passed |
| Manager drop-in build; isolated native/rule loading and reproducibility tests | Passed |
| Package/integration version consistency, release notes and local Markdown links | Passed |

Two unit cases are intentionally skipped because `x-mqtt` belongs to a host rather than this producer.
The minimum-dependency environment also skips the FastAPI router test; the full suite exercises it.
CI runs the full suite, minimum runtime dependencies on Python 3.13 and a clean wheel installation.
GitHub-hosted Hassfest and HACS validation still need to pass for the release commit.

## Environment checks before publishing

Automated tests use brokers and emulators; actual deployment checks must be recorded separately.

- [ ] HA with ildevice-homeassistant: reads, commands, rejection handling and multiple device categories.
- [ ] HA, bridge and broker restarts: reconnection, availability and retained device reconciliation.
- [ ] Manager catalog installation: matching native platform, plugin tab, device additions/removals and shutdown.
- [ ] Authenticated/TLS broker through the manager's transport; daemon broker configuration supports username/password.
- [ ] Real covers: direction, motion, commands and saved settings, including builtin override products.
- [ ] Concurrent manual rule edits, IL settings saves and automatic builtin updates: confirm ownership expectations.

## Distribution constraints

The base package supports Python 3.13 and newer. The complete test suite requires Python 3.14 because of
Home Assistant and the IL consumer. The consumer is installed from its source checkout for tests.

The manager drop-in contains the build machine's native tuya-interpreter library. The release workflow builds
one archive on Ubuntu; it is not a universal archive. Use pip for other supported wheel platforms, or build
an archive on the target platform. Test a catalog installation on the intended deployment platform before publishing.

## HACS default repository submission

Requirements: [HACS inclusion guide](https://hacs.xyz/docs/publish/include/).

- Repository is public and active, has a description/topics, enables issues and uses an identified MIT license.
- One integration lives under `custom_components/rustuya/`, with its manifest, translations and local `brand/icon.png`.
- `rustuya` does not conflict with a Home Assistant core integration domain in the checked development tree.
- `hacs.json` declares Home Assistant 2026.10 or newer, matching the tested release series.
- HACS Action has no ignored checks; HACS and Hassfest workflows are configured.

Before submitting:

1. Push the release commit and confirm HACS Action and Hassfest pass for that commit.
2. Publish the matching `rustuya-local==X.Y.Z` package to PyPI and the full `vX.Y.Z` GitHub release
   (done for `0.1.1`). The integration installs that exact PyPI version; a tag alone is insufficient.
3. As the owner or a major contributor, open an editable PR from a new branch in a personal fork of
   `hacs/default`. Add `3735943886/rustuya-local` to `integration` in sorted order and complete its PR template.

The companion ildevice-homeassistant is already in the default list. After this repository is merged,
switch the README badge from *HACS-Custom* to *HACS-Default*. Submission and upstream review are separate from local validation.
