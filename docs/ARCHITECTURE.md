# Current architecture

Baseline: 2026-10-13, `rustuya-local 0.1.0.dev8`.
See [README](../README.md) for installation and [REDESIGN](REDESIGN.md) for earlier designs.

```text
Tuya LAN devices
  ↕
rustuya-bridge / embedded pyrustuyabridge
  ↕ MQTT (topic and payload templates announced by the bridge)
BridgeClient ↔ Runner ↔ Hub ↔ tuya-interpreter TuyaDriver
                ↕
          IL MQTT transport
                ↕
ildevice-homeassistant / other IL consumers
```

## Components

| Location | Responsibility |
|---|---|
| `src/rustuya_local/service.py` | Shared assembly, startup/shutdown, pack updates, retained cleanup, raw DP commands |
| `bridge_client.py` | Bridge templates, paginated registry snapshots, intersection of registered and configured devices |
| `host/hub.py` | Driver registry, replacement/reload, native rule-pack reference management |
| `host/runner.py` | IL subscriptions and publish queue, timers, presence/probes, settings callbacks |
| `host/il.py` | Mapping driver outputs to IL MQTT topics and values |
| `host/rules.py`, `overrides.py` | Rule discovery/merging, legacy cover keys, file watching and settings persistence |
| `host/pack.py` | Official rule downloads, hash verification and ownership ledger |
| `host/mqtt.py`, `memory.py` | paho MQTT and in-process test transports |
| `cli.py`, `config.py` | Standalone daemon and configuration validation |
| `manager_plugin/` | Supervision by the manager, settings/file APIs and web tab |
| `custom_components/rustuya/` | HA config entry, embedded bridge, login/registration and administrator panel |
| External `tuya-interpreter` | Single-device Tuya DP interpretation, command validation and rule composition |

HA entity creation belongs to the IL consumer. The base package does not import Home Assistant.

## Startup and shutdown

`Service.start()` prepares the native library and rules in worker threads, then connects the bridge and IL transports
and creates the Hub. It refuses startup if a producer with the same IL prefix/source answers a probe. This is a
preflight check, not an atomic distributed lock. After a complete bridge registry snapshot, it advertises devices
present in both the device file and registry and clears stale IL topics left by the previous run. Pages are checked
for order, counts and duplicates, but the bridge has no request ID for complete correlation with other requests.

Normal shutdown marks presence offline and leaves descriptors/state retained. Restarting within the same namespace
preserves presence. Moving namespaces or running `purge` clears the previous retained topics. `AsyncExitStack` manages
service resources. HA unload always attempts embedded-bridge shutdown, even if service shutdown fails or is cancelled,
and then removes the unusable runtime. The resume marker is set only after successful cleanup.

MQTT close blocks new dispatch, cancels and awaits asynchronous handlers, and stops the paho thread outside the event
loop. Cancelling close still waits for its cleanup before propagating cancellation. Asynchronous handler errors are
retrieved and logged. Closed transports cannot be connected, subscribed or published through again.

## Rule and settings precedence

1. Bundled `rules/builtin/*.json` from `tuya-interpreter`.
2. Downloaded `rules/builtin/*.json` below the configured rule directories.
3. JSON files from `TUYA_ENGINE_RULES` and user directories, merged by filename byte order.
4. Inline service `hub_options.overrides`; the daemon calls this `options.overrides`.

Later values override earlier ones. `manifest.json` is excluded from rule layers, and directory files starting with
`.` or `_` are skipped. `zz_settings.json` is a normal user layer: later filenames or inline overrides can replace
its settings. Invalid user layers are skipped with warnings. A rejected Hub reload preserves the existing drivers.

Automatic pack updates run at startup and daily; manual updates also work when automatic updates are disabled.
Only files with matching ownership hashes are replaced or removed; user edits are preserved. Manifest and ledger
entries must have safe filenames and SHA-256 hashes. Host settings, manifests and symlink targets cannot be owned
by the pack. Each managed file is checked against the directory boundary.

The watcher must run to apply downloaded files to an active Hub. The daemon's `watch_interval: 0` disables polling.
IL settings saves use a separate lock and atomic replacement, then reload immediately. HA settings/options API
requests share a lock covering the current-entry read, patch merge, validation, probing and restart.

## Verification and coupling

Unit tests, in-process services, real MQTT brokers, Tuya emulators and HA integration tests verify different
boundaries. They do not replace real LAN-device tests. Driver state internals remain a coupling point that needs
checking when dependencies change. Manager sessions use public registry readiness and wizard state-change APIs;
HA no longer patches manager internals or polls wizard state. See [REVIEW](REVIEW.md) for fixes and limits.
