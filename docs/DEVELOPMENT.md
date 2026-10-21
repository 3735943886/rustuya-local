# Development

See [Architecture](ARCHITECTURE.md) for components, lifecycle, MQTT topics and rule precedence.

## Layout

- **[tuya-interpreter](https://github.com/3735943886/tuya-interpreter)** is the only place that interprets Tuya:
  Home Assistant core's `tuya` behaviour and user overrides for non-standard devices, through its Sans-I/O Rust
  engine and Python wrapper.
- **rustuya-local** is one `Service` (`src/rustuya_local/service.py`) and three thin shells that run it: the
  `rustuya-local run` daemon, the `custom_components/rustuya` Home Assistant integration, and a rustuya-manager
  plugin. Its `host` package owns the runner, transports and file watchers. It only produces IL and knows nothing
  of Home Assistant entities.
- **[ildevice-homeassistant](https://github.com/3735943886/ildevice-homeassistant)** turns IL descriptors into
  Home Assistant entities.

## tuya-interpreter host boundary

The shared Rust core interprets one device and has no MQTT or rule-file loading API.
`rustuya_local.host.Hub` owns the device registry and maps `TuyaDriver` outputs to IL MQTT wire data;
`host.rules` discovers and loads the bundled builtin rules and user layers, passing decoded rules to Rust for composition.
The service preloads native code and rules in a worker thread before starting devices.
Timers, retained-message policy, bridge `get` requests, presence and settings persistence remain in this host.
MQTT and in-process transport tests exercise the same adapter and the bidirectional Rust driver.

## Tests

```bash
uv venv --python 3.14
uv pip install -e ../ildevice-homeassistant -e ".[test,test-ha,fullstack,manager]"
.venv/bin/python -m pytest -q
uvx ruff==0.16.8 check src tests custom_components scripts
```

The complete suite uses Python 3.14 because the consumer and Home Assistant test dependencies require it.
Install `mosquitto` for broker tests and Node.js for panel JavaScript tests. The command above installs the consumer
from a sibling checkout and other dependencies from the package index; CI follows the same approach.
Missing base or test dependencies can fail collection; optional broker, emulator and fixture tests skip when their
prerequisites are unavailable. The full-stack device comparison uses `../tuya-interpreter/tests/golden`; IL wire and
topic vectors use `../ildevice/vectors` (or the `ILDEVICE` environment variable). Keep those checkouts for these checks.

- `tests/unit/`: configuration validation, host lifecycle, IL vectors, rule loading, settings persistence,
  pack ownership, converter APIs, panel JavaScript and release packaging.
- `tests/e2e/test_service.py`: the service on in-memory brokers (overrides directory, a failed start).
- `tests/e2e/test_chain_*.py`, `test_daemon.py`: the runner with ildevice-homeassistant's consumer model on the other side, in one process
  and over a real broker; the daemon as a subprocess.
- `tests/e2e/test_manager_plugin.py`: the plugin under rustuya-manager's real plugin host (skipped without it).
- `tests/ha/`: the integration (config flow, setup).
- `tests/e2e/test_full_stack*.py`: the real rustuya-bridge (`pyrustuyabridge`, in process) and Tuya device emulators
  (`tuyamock`) around the daemon: state, commands, rejections, a device dropping off and returning, a daemon restart, and a
  differential check that 38 real device shapes come out of the whole chain exactly as `tuya-interpreter` computes them.

## Releasing

Bump the version in `pyproject.toml` and `custom_components/rustuya/manifest.json` (both its `version` and the
`rustuya-local==` requirement), add a `## [X.Y.Z]` section to `CHANGELOG.md`, and push a `vX.Y.Z` tag. The release
workflow runs the tests, publishes to PyPI, and creates the GitHub release with the wheel, sdist and manager drop-in zip.
HACS installs from the GitHub release, and the integration then installs the exact `rustuya-local` version from PyPI.

`hacs.json` declares Home Assistant 2025.3 or newer: the first release with both Python 3.13 (required by
`rustuya-local`) and paho-mqtt 2.x (2025.2 pins 1.6.1). `tests/ha` passes on 2025.3.0 as well as the current release.
