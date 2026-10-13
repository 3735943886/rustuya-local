> This is the **design and implementation history** beginning on 2026-09-21. Versions, APIs, test counts and open
> items describe their recorded point in time, not current implementation or deployment state. The former
> `tuya2ildevice` project is now `tuya-interpreter`. For current information see [ARCHITECTURE.md](ARCHITECTURE.md),
> [STATUS.md](STATUS.md), [ROADMAP.md](ROADMAP.md) and release-specific [CHANGELOG.md](../CHANGELOG.md).

# rustuya-local redesign history — HA-independent core and modular hosts

Written: 2026-09-21. Scope: the initial redesign and implementation/deployment record around the 0.0.40 period.
This replaced the v1 plan based on an HA custom component, including the earlier approach in `docs/STATUS.md` and
`rustuya-homeassistant/docs/tuya2ha-v2/STATUS.md` where rustuya-local owned both the engine and entity creation.
The verification assets from those plans remained useful.

## 1. Decisions confirmed by the user

| Item | Decision |
|---|---|
| Chain | `rustuya-bridge → tuya2ildevice → (ildevice IL) → ildevice-homeassistant` |
| Form | HA-independent core first; a thin custom-component wrapper later; modular design |
| IL | Existing `../ildevice` specification, amend it where necessary |
| Converter | `../tuya2ildevice`, initially a local path dependency, later pip |
| ildevice-homeassistant | Support both separate installation and embedding by modularizing the consumer |
| Other repositories | Changes to tuya2ildevice, ildevice-homeassistant and ildevice authorized |
| Accepted defaults | `expose_unused=False`, matching core; camera outside IL; implement alarm/vacuum in tuya2ildevice; consumer owns names/icons |
| Safety assets | 1,205 golden entities, 58 write replays, oracle, generator and adapter; rebuild the implementation |
| Commits | Do not commit before the user requests it |

## 2. Target architecture

```text
rustuya-bridge (LAN DPs, MQTT)
  ↕ abstract Transport
rustuya-local (HA-independent app core)
  configuration / device and bridge management / cloud schemas / asyncio Runner
  rustuya_local.host.Hub → TuyaDriver (Rust interpretation)
  ↕ abstract Transport carrying IL messages
  A: MQTT broker → separately installed ildevice-homeassistant
  B: direct in-process connection → embedded ildevice-homeassistant engine

Thin HA wrapper: custom_components/rustuya_local (Phase 4)
```

The central decision was to abstract transport. Both producer and consumer know only topic subscriptions and
publishes, without requiring MQTT rather than in-process memory. In mode A the consumer subscribes through HA MQTT
and rustuya-local can run independently in Docker. In mode B the wrapper embeds the consumer engine without a broker
between them. IL messages and the consumer code are the same in both modes.

## 3. Work by repository

### 3.1 ildevice specification — minimal changes

- No implementation code. Add specification changes only when needed: recheck alarm/vacuum roles, document camera
  as outside scope in `il-rationale.md`, and explain that `il-mqtt.md` messages can travel over an abstract transport.
- Add conformance vectors for new alarm/vacuum cases and write replays.

### 3.2 tuya2ildevice — parity and runtime APIs

1. Extend golden comparisons from counts to complete properties: class, unit, options, features, default enabled
   state and values. Compare the 58 writes from IL commands to DPs.
2. Investigate missing climate −3, cover −1 and humidifier −3; implement two alarms and two vacuums. Explicitly exclude camera.
3. Default `expose_unused` to false, retain an option, and reconcile the 112 extra entities against core.
4. Add runtime device addition, removal and schema replacement to Hub, previously fixed at construction except reload.
5. Remaining engine issues: 18 unported SDK strategies, duplicate delta accumulation on periodic retransmission, enum wire validation.
6. Keep `examples/host.py` only as an example; the rustuya-local Runner replaces it without another implementation.

### 3.3 ildevice-homeassistant — separate and embedded operation

The initial code had HA-independent `core/` (descriptor, plan, topics, values), `hub.py` (301 lines, directly using
HA MQTT), `entity.py` (754 lines), platform shims (13 lines each), integration setup and config flow.

```text
il_ha/
  core/        existing pure core, Transport protocol, device state model
  ha/          entity classes, registries and platform setup
  transports/  HaMqttTransport and test InProcessTransport
custom_components/il_ha/
               config flow + attach(hass, entry, HaMqttTransport)
```

- Public `attach(hass, config_entry, transport, options) -> IlHub` lets another integration embed the engine under
  its own entry. Reuse platform setup through `il_ha.ha.platforms.setup(platform, hub, add_entities)`.
- The consumer owns names, icons and translations. Port core translations separately if needed; the initial default was `humanize(prop)`.
- Embedding changes the entity platform/domain to `rustuya_local`. Even with identical unique IDs, registry entries
  differ from standalone `il_ha`: switching can duplicate entities and split history. Choose a mode at installation
  and document mode switching as unsupported.

### 3.4 rustuya-local — rebuild the app

Use an HA-independent `rustuya_local/` package and later a `custom_components/rustuya_local/` wrapper.

- Configuration: bridge host/port/root, IL broker/prefix, overrides/converters and cached device list; files/environment/CLI without HA.
- Device registration, deletion, scanning and cloud schemas use the rustuya-manager `Manager` facade; do not duplicate its protocol.
- External rustuya-bridge is the default; embedded pyrustuyabridge is opt-in. Read `BridgeTopics` templates from bridge configuration.
- asyncio Runner drives Hub and bridge/IL transports, executing Subscribe/Publish/Schedule/Unschedule, LWT (M-12),
  resubscription and `start()` after reconnect.
- paho `MqttTransport` and `InProcessTransport` with wildcard/retained semantics for tests and embedding.
- CLI daemon `rustuya-local run --config …`, also usable in Docker.
- Phase 4 HA config flow/options/services/diagnostics only call the core. Mode A uses a separate consumer; mode B embeds it.
- Retire `entity_engine.py`, `bridge_device.py`, 17 platforms, entity translations and `icons.json`; rewrite the
  1,012-line config flow, bridge supervisor/client, services and diagnostics.
- Since the original code was uncommitted, move it to unpackaged `legacy/` before deletion, then remove it after validation.

## 4. Initial measured chain (2026-09-21, entity counts)

Reproduced 1,185 of 1,205 golden entities, zero errors, 1,317 produced. Matching counts: light 65/65, fan 13/13,
valve 14/14, siren 5/5, event 14/14, button 12/12. Shortfalls: climate 17/20, cover 15/16, humidifier 4/7,
alarm 0/2, vacuum 0/2, camera 0/9. Extras from `expose_unused`: switch +9, select +22, number +26, sensor +73,
binary_sensor +2, total +112. Property, state and write comparisons were the first Phase 1 task.

## 5. Phases

| Phase | Work | Completion criteria |
|---|---|---|
| 0. Safety net | Package layout, `legacy/`, path dependencies, port `tests/chain/` golden harness | pytest chain comparison with a fixed baseline |
| 1. tuya2ildevice | Section 3.2 items 1–4 | Full property/state parity with documented exceptions, 58 writes, runtime Hub addition/removal |
| 2. Consumer modularization | Core Transport/model, HA layer, attach | Existing tests plus identical HaMqtt/InProcess results |
| 3. rustuya-local core | Configuration/devices/Runner/transports/CLI | HA-free in-process bridge → Hub → consumer-plan E2E; reconnect/timers/LWT |
| 4. HA wrapper | Modes A/B, config flow, services, diagnostics | Both modes through pytest-homeassistant-custom-component |
| 5. Real devices | Real bridge, devices and HA | Enum wire, dj hex, duplicate deltas, `support_local=false` and other unverified cases |

Phases 1 and 2 were independent. Phase 3 depended on settled Hub addition/removal and Transport interfaces.

## 6. Benefits, costs and risks

- HA-free execution and testing, quicker iteration, the same consumer structure as other producers such as rusthinq,
  and entity logic in one place: ildevice-homeassistant.
- Three repositories change together. Define Hub, Transport and attach interfaces before each phase to avoid incompatible changes.
- The consumer does not know core names/translations/icons. Property golden parity does not guarantee identical names.
- Entity registry compatibility differs between embedded and standalone modes (section 3.3).
- Wire formats and duplicate delta behavior cannot be established without real devices.

## 7. Open decisions at the time

1. paho (default, matching tuya2ildevice examples) or aiomqtt for Runner MQTT.
2. Local pyproject path dependencies initially, pip later.
3. Whether to port names/translations after consumer modularization and real use.

## 8. Implementation record

- **2026-09-21 Phase 0 completed**: move `custom_components/`, `hacs.json` and `test_render_golden.py` to ignored
  `legacy/`; create src-layout `rustuya_local`, a `../tuya2ildevice` path dependency and uv `.venv`. Add
  `tests/chain/measure.py`, `test_counts.py`, `baseline.json`: 1,185/1,205, zero errors, 1,317 produced. 34 tests passed.
  Golden data came from `../tuya2ildevice/tests/golden`; consumer core was imported by path pending Phase 2 packaging.
- At that point `../tuya2ildevice` had no Git history. Save `tuya2ildevice.backup.tgz` and
  `ildevice-homeassistant.backup.tgz` in session scratch storage before modifications; decide Git initialization before Phase 1.
- **2026-09-21**: initialize tuya2ildevice Git on master; baseline commit `6ea06c9`.
- **2026-09-21 Phase 1 first pass completed, uncommitted**:
  - `tests/chain/props.py`/`test_props.py` match golden entities to descriptors by DP code (`src`/bitmap labels),
    comparing platform/class/category/unit. Initial 151 missing matches were mostly comparator issues: bitmap label
    case and empty composite keys. Actual differences: 18 classes, 12 categories, 3 platforms, 1 unit and 20 missing entities.
  - Implement alarm/vacuum assembly, alarm disarm only with `allow_hazardous` under S-1, descriptor group classes and
    composite categories, and false `expose_unused`. Consumer fixes: valve device class and composite entity category.
  - Result: 1,190/1,205 with matching platforms; remaining 15 were nine cameras outside IL, three climates without
    target temperature and three humidifiers without target humidity. Zero class/category differences; one windspeed
    unit difference from HA host conversion. Exceptions in `test_props.py` make new differences fail.
  - Add Hub `set_device(device)` and `remove_device(id)`, clearing removed props before replacement (M-11) and values
    before descriptors on removal. Tests: tuya2ildevice 47, consumer 105, rustuya-local 41 passed.
- **Phase 1 remaining findings**:
  - IL fan only had select `fan_speed`/`mode`, so core percentage/oscillate/direction became separate number/select/switch
    entities (six extras). Adding roles and consumer fan support awaited a decision.
  - Climate/humidifier fallback to switch/select/sensor preserved functions but changed presentation; decide whether to relax IL requirements.
  - The 58 writes and IL state comparison should pass through consumer entities (service → IL command) in Phase 4;
    engine-level golden tests already passed. SDK strategies, duplicate deltas and enum wire validation remained for Phase 5.
- **2026-09-21 fan-role decision approved**: add `speed` (number %), `oscillate` (binary) and `direction` (select) to
  ildevice specification/schema/consumer rationale; producer and consumer support them. Extras decreased 39→28.
  Keep climate/humidifier fallback as decided.
- **2026-09-21 Phase 2 completed, uncommitted**:
  - Add `il_ha/core/transport.py`, `core/memory.py` and `core/model.py` (`IlModel` + `Sink`) for descriptors, values,
    availability grace, presence, rejects and discovery decisions without HA. Preserve MQTT filters/retained/tombstones.
  - HA `hub.py` implements `IlHub` as Sink for entities/registries/discovery/dispatchers; add `mqtt_transport.py` and
    `attach.py` with `build_hub(hass, options, transport, platform)`. Importing `il_ha.core` does not import HA, tested explicitly.
  - hatch installs `custom_components/il_ha` as wheel package `il_ha`, allowing ordinary core imports without path hacks.
    116 consumer tests passed (106 existing + 10 model); existing HA integration tests passed unchanged.
- **2026-09-21 Phase 3 first pass**:
  - Runner connects Hub to two transports with ordered publishing, timers, reconnect `hub.start()` and runtime
    set/remove/reload. Add paho transport with resubscriptions/hooks/LWT, configuration and CLI.
  - 51 tests passed: 41 chain, five in-process flow/commands/rejects/runtime/shutdown cases, two real-mosquitto
    flow/LWT/reconnect cases, daemon SIGTERM offline and two configuration cases.
  - Fix an infinite `InProcessTransport.settle` loop by yielding to done callbacks with `sleep(0)`; make Runner stop idempotent to avoid drain deadlock.
- **2026-09-21 consumer icons**: `core/icons.py` chooses by name, then unit, then kind; add `EntitySpec.icon` and
  `_attr_icon`, leaving HA icons for device-class entities. Cover all 445 classless entities across 324 real-device
  schemas. 133 consumer tests passed.
- **2026-09-21 Phase 3 second pass: device list and bridge configuration**:
  - rustuya-manager already provides CLI/web/QR login/bridge sync and atomically writes cloud schemas to
    `tuyadevices.json`. rustuya-local follows that file without writing it or duplicating management. Keep manager
    dependencies out of the core; the Phase 4 HA wizard can use them.
  - `parse_devices` skips/reports missing categories, DeviceWatcher polls mtime/size and syncs additions/replacements/
    removals while preserving state on read failure. Runner isolates failed records. Hub exposes records.
    Read retained bridge config and topic templates; fall back to defaults with warnings.
  - 55 tests passed: four new parsing/templates/sync/watcher cases and a daemon E2E over actual mosquitto using
    bridge templates and device-file additions/removals.
  - Unverified/unsupported at that point: nondefault `mqtt_payload_template`, real bridge single-DP mode and opt-in embedded-bridge lifecycle.
- **2026-09-21 Phase 3 third pass: standalone full stack**:
  - Validate the daemon before the custom component. Chain: tuyamock Tuya LAN 3.4 emulator ↔ real in-process
    `pyrustuyabridge.PyBridgeServer` ↔ mosquitto ↔ daemon subprocess ↔ IL mosquitto ↔ MQTT observer. Only devices were mocked.
  - Seven `test_full_stack.py` cases: descriptors/state, device push, IL brightness 50 → raw 507 plus switch on,
    switch off, out-of-range rejection without device writes, device disappearance/recovery, daemon restart with
    all IL retained data removed and recovery from bridge snapshots, no daemon stderr exceptions/errors.
  - Two `test_full_stack_devices.py` cases emulate 38 core fixture shapes (up to four per platform), assigning DPs
    and local strategies, and compare all 273 resulting IL values with direct TuyaDriver output. A mutation test
    confirms the comparison detects differences. Boolean/Integer/Enum/String/Raw/Json/Bitmap bridge wire formats match engine assumptions.
  - No implementation bugs found; correct test assumptions that raw brightness 500/1,000 yields 49 and stopped MockDevices must be recreated.
  - Still unverified: real firmware/3.3/3.5 devices, custom payload templates, 58 IL write replays, soak tests and hundreds of devices.
- **2026-09-21 code relocation**: move Runner, transports, Transport protocol, watcher/parser and bridge config reader
  to `tuya2ildevice.host` (paho `host` extra), add `BridgeTopics.from_config`. rustuya-local shrinks to 144 config/CLI
  lines and drops consumer runtime dependency. Consumer remains unchanged; structural protocols and specifications
  connect them without mutual imports. Move chain tests into tuya2ildevice, conditional on consumer availability.
  Passed: tuya2ildevice 137, consumer 174, rustuya-local 20.
- **2026-09-21 specification vectors**: `ildevice/vectors/wire-values.json` (four value/payload directions),
  `topics.json` (layout, x-mqtt, presence, IDs), `composites.json` (18 kind/role cases, schema validated).
  Remove copied `tuya2ildevice/tests/spec`; both read `../ildevice` or `$ILDEVICE`, skipping if unavailable.
  - Fix `encode_value(50.0)` producing `50.0` instead of the required integral payload `50`.
  - Relax K-2 cover requirements to position or open or close; consumers already support open-only garage remotes.
  - Resolve K-6: lock/valve composites require writable `locked`/`opened`; readonly values remain generic binary props,
    with optional native consumer presentation. Add four vectors, 45 consumer tests passed, document rationale.
  - Remove `opened` from S-1's hazardous list: ordinary water valves are normal controls; the general rule still
    covers hazardous supplies such as gas. Producer already emits writable valves. S-3's existing locked rule is referenced by K-6.
- **Remaining at that point**: Phase 3 embedded lifecycle and diagnostics; Phase 4 HA modes/config flow/attach and
  58 write/state IL comparisons; Phase 5 real devices.
- **2026-09-22 bridge wire interpretation moved to rustuya-local, uncommitted**:
  - Handwritten regex `BridgeTopics` did not interpret actual bridge templates, especially custom payloads.
    rustuya-manager's binding-based client supplied the reference pattern: pyrustuyabridge `match_topic`,
    `render_template`, `tpl_to_wildcard` and `parse_seed_dps`, matching the Rust bridge parser.
  - Remove tuya2ildevice `BridgeTopics` and `host/bridge.py` config reader. Hub receives decoded Connected/
    Disconnected/Message inputs through `on_bridge_message(now, device_id, input, retained=False)` and emits
    abstract `BridgeCommand(device_id, action, dps)`. Remove `_dps_of` wrapper unwrapping because the parser produces
    flat DP maps. Runner handles one IL transport and injects bridge-command callbacks for every input/reload path.
  - New rustuya-local `bridge_client.py` owns config bootstrap, templates, event/error matching and command rendering.
    CLI and HA wrapper use the same wiring. The wrapper imports the package absolutely; HA distribution would need
    a manifest requirement once rustuya-local reached PyPI, as it then worked only in the development venv.
  - Public API changes required tuya2ildevice 0.2.0 after released 0.1.0; temporarily restore local uv sources.
  - Register CLI signal handlers before bridge bootstrap so SIGTERM during the wait still uses graceful shutdown.
    Adjust full-stack/driver tests from wrapped payloads to the flat contract. Passed: tuya2ildevice 137 + four skips,
    rustuya-local 34.

## 9. Absorbing rustuya-homeassistant (2026-09-24, recommended approach approved)

rustuya-homeassistant (`rustuya-ha`) provided a CLI and manager “HA Discovery” plugin that interpreted Tuya and
published discovery. With interpretation owned by tuya2ildevice, rustuya-local would replace it and reuse its
operational tools. The proposed responsibilities at that point were:

```text
tuya2ildevice          Tuya interpretation and host parts: Runner, transports, watchers
ildevice-homeassistant IL → entity planning through plan_entities, importable without HA
rustuya-local         HA-free BridgeClient, shared Service and IL → HA discovery operations
                      CLI / custom_components/rustuya / manager plugin shells
```

Service would assemble bridge input, IL output and optional HA output (none, discovery or an embedded consumer);
shells would select components and own their lifetime. Decisions: keep the rustuya-local name; retire and eventually
archive rustuya-homeassistant; initially use a dedicated BridgeClient connection like the daemon rather than the
manager DP bus, which lacks online/offline events; leave GitHub `pack.py` synchronization out of the first pass.

### Destination of existing code

| rustuya_ha component | Destination |
|---|---|
| v1/uncommitted v2 `tuya2ha`, `dp_mapping`, `render_mqtt`, `scheme`, bridge/converter core, plugin engine | Delete; replaced by tuya2ildevice and pyrustuyabridge |
| `custom_converters/00_default.json`, `00_curtain.py` | Builtin tuya2ildevice `overrides.json` and `cover_motion` |
| Converter directory loading/hot reload | Host `load_overrides` and `OverrideWatcher` |
| Planning/restore/backup/verification/detail and CLI status/publish/clear/restore | rustuya-local discovery module with IL inputs |
| Manager plugin tab | rustuya-local manager shell |

### Planned steps

1. Extend overrides: DP renaming, alias/invert remapping, block-level `expose_unused`, named converter types,
   builtin overrides, v1 discovery-cover conversion and directory loader.
2. Extract shared Service for CLI/HA and override directory configuration.
3. Plan discovery with consumer `plan_entities`, use IL topics directly and producer + device availability, and port operations.
4. Manager `register(ctx)` shell with `ctx.add_service` and status tab.
5. HA output selection between a separate consumer and discovery.
6. Retirement release and archival of rustuya-homeassistant.

The old v1 discovery pointed at bridge topics and did not require its process after publication. New discovery
would point at IL and require the producer continuously. Discovery also represents less than the native consumer,
including rejection feedback and translated names. Warn or prevent simultaneous producers on a bridge root or
using both discovery and the native consumer for the same devices.

### 9.1 Implementation record

- **2026-09-24 step 1 completed in tuya2ildevice, uncommitted**:
  - `overrides.py` allows `dp.code` without `type` to rename an existing DP while retaining type/range/strategy.
    `remap.<code>.alias` maps device words to standard words in reads/writes and Enum ranges; invert negates booleans
    or reverses integers within range. Adapter Remap runs after read strategy and before write strategy; converters
    see translated values. Add per-block `expose_unused`, host-provided named `converter_types`, builtin
    `overrides.json` controlled by `use_quirks`, and user precedence per key.
  - Builtins reproduce four products from v1 `00_default.json`. Three curtains use core + `cover_motion`;
    `f6jujmx0` removes `percent_state` to reproduce v1 position DP 2. Window opener `5rta89nj`, category `mc`, needs
    schema-missing DP 104. With no core category table/control DP, expose a writable position number and battery
    through DP definitions/unused props. v1 assembled a cover by DP shape: presentation differs, functionality remains.
  - `from_v1` converts discovery cover command/set/current position DPs assuming standard 1/2/3 to renaming/removal;
    payload words to control aliases; position inversion to remap inversion; derived state or motion words to
    `cover_motion`. Warn for remaining HA fields; `dp_meta` enables unused props to match v1. Tests compare converted
    v1 defaults with builtins, except labels.
  - Directory `load_overrides` merges JSON by filename, automatically converts v1, reads Python `CONVERTERS`, reports
    v1 `setup(api)` files and skips broken files with warnings. OverrideWatcher reloads Runner and retains active
    settings on Hub rejection. 154 tests passed, including chains. Product fixtures omit device IDs/keys.
- **2026-09-24 step 2 completed: shared core**: `Service` assembles BridgeClient, Hub, Runner, watchers and discovery
  from transport factories; unwinds failed startup and stops idempotently. CLI/HA share it. Live `custom_converters`
  directory reads/imports run through `to_thread` to avoid HA loop warnings. Warn if presence already says online.
- **2026-09-24 step 3 completed: il2discovery**:
  - Pure `discovery/render.py` maps entity plans to configuration, relays and mirrors; publisher follows IL
    descriptors, updates/clears retained config, relays writes, mirrors JSON and sweeps. Add operations and CLI
    `discovery status|clear|restore` with backups. Relays combine cover/lock/alarm/vacuum commands, climate mode/power,
    fan percentage and light writes; mirrors serve JSON-only vacuum/light/climate state. A running service is required.
  - `tests/ha/test_discovery_parity.py` loads both consumers for all 324 core fixtures into one test HA. Entities
    match 1:1, state/attributes match and 1,422 service calls yield identical IL writes. Allowed differences are HA
    MQTT behavior: climate on/off features, select `none`, negative number `-30.0`, and empty fan preset modes.
  - Tests caught color-role mismatch in basic light schema, requiring JSON mirror/relay with HA color conversion;
    field-specific absent handling, null payload defaults enabling features, siren JSON wrapping, `17.0` numeric
    formatting, button payloads and avoiding redundant climate power writes.
  - Fix consumer `IlValve` ignoring device class; strengthen its tests (186 passed).
  - Record an unfixed producer case: `bzyd_45idzfufidgee7ir` emits brightness 393 above max 100 and color
    `#-2ec-2e6ff`; explicitly exempt it in parity tests pending the later fix.
- **2026-09-24 step 4 completed: manager plugin**: entry point `rustuya_manager.plugins` registers the same Service
  using manager broker, TLS, root, `ctx.devices()` and `watch_devices`; add transport TLS, default discovery on,
  settings/converters data directories and status tab. Test through the real manager plugin host.
- **Step 5 decision changed**: omit HA discovery output. HACS installs the consumer as `custom_components.ildevice`,
  not importable as a PyPI `ildevice` package; inside HA use that integration. Rustuya is only an IL producer with
  `<config>/rustuya_converters`.
- **2026-09-24 step 6: retirement cleanup, uncommitted**: remove uncommitted v2 code/docs/scripts/golden tools from
  rustuya-homeassistant after scratch backups, since current copies exist in tuya2ildevice. Revert tracked v2 edits;
  add retirement/migration notices to README/CHANGELOG. 255 v1 tests passed in isolation. PyPI release/archival remain user decisions.
- **2026-09-24 discovery removed by user decision**: retain only IL production. Remove discovery modules, CLI,
  daemon/plugin configuration keys, extra and tests. IL→HA conversion is unrelated to Tuya and created a consumer
  dependency that blocked PyPI/drop-in distribution. Use ildevice-homeassistant. Code remains at `c646922`;
  outstanding work is in [ROADMAP.md](ROADMAP.md).
- **2026-09-24–27 releases**: 0.0.7 drop-in/catalog; 0.0.8 deduplication; 0.0.9 tuya2ildevice 0.3.1 value bounds,
  power names and source; 0.0.10 0.3.2 handover presence, nonblocking HA and warning placeholders; 0.0.11 0.3.3 and
  HACS brand assets; 0.0.12 PyPI README. Detailed milestone history is in the archived roadmap.
- **2026-09-27 0.0.13–0.0.15**: settings/converter editor and migration guide; 0.0.15 with tuya2ildevice 0.3.5 adds
  producer probe/refusal and restores online override packs. 0.0.14 was tagged but never published: the temporary
  repository rename blocked PyPI publication. Keep `rustuya-local`, since it is not HA-only.
- **2026-09-27 0.0.16**: HA install failed because required rustuya-manager 0.2 was not on PyPI (development versions
  were only on TestPyPI). Release manager 0.2.0 and remove upper dependency caps `<0.4`, `<0.3`, `<0.1`.
- **2026-09-27 0.0.17**: opening device sync immediately after closing login let two Manager sessions use
  `rustuya-config`, repeatedly disconnecting each other. Serialize sessions, waiting at most 15 seconds then
  aborting `manager_busy`; bound close to 15 seconds so the lock cannot remain held forever.
- **2026-09-27 0.0.18**: r5c's 61 bridge devices became 11 IL devices because broadcast status pages from other
  requests were accepted and a repeated final page committed as the full list. Track one request cycle, schedule
  one rerun for overlapping requests and abandon a cycle after ten seconds without replies.
- **2026-09-27 0.0.19**: use pyrustuyabridge 0.4.0.dev2 `render_command`, verified by the bridge parser. A
  `{root}/command/{action}/{id}/{dp}` single-DP write now sends the bare value rather than an unresolved `{dp}` and
  JSON object. Manager 0.2.1 uses it too; embedded bridge modes require the 0.4 development version at that point.
- **2026-09-27 0.0.20**: require tuya2ildevice 0.3.7, read only its converter format and remove legacy conversion.
  DP-based props allow position-only window openers as covers; support `auto: false`, writable code converters and
  curtain `cover_state`. Product-specific corrections belong in user/pack files rather than package code.
- **2026-09-27 0.0.21**: tuya2ildevice 0.3.8 retries refused devices after converter files are fixed without restart.
- **2026-09-27 0.0.22**: 0.3.9 preserves bridge position rather than inverting it; use remap for models needing
  inversion. Changed live passive values count as pushes, enabling motion for passive-only curtains.
- **2026-09-27 0.0.23**: 0.3.10 prevents merged bridge state snapshots from immediately settling curtain motion.
- **2026-09-27 0.0.24**: 0.3.11 emits button/events only from active reports, matching rustuya-homeassistant.
- **2026-09-28 0.0.25**: administrator sidebar panel with missing/orphan/mismatch/synced filters, gateway/subdevice
  grouping, live links, add/update/remove, converter editing/origins and immediate pack sync; single integration
  instance. Move defaults under `.storage/rustuya/`, entry migration 1.2 including credentials. Manager tab also labels pack origins.
- **2026-09-28 0.0.26**: external setup checks broker/config and displays cannot-connect/bridge-not-found errors.
  Deleting the entry clears all retained IL owned by its source, including previous-run devices, while preserving
  other sources. Embedded mode also clears bridge topics/state and owned device/login files. Add daemon purge and
  roundtrip flush before closing so final tombstones/offline are delivered.
- **2026-09-28 0.0.27**: setup asks bridge mode, broker and IL prefix; login is optional and closing it completes
  via import. Keep other settings at defaults and edit them in panel Settings, clearing old namespaces when moved.
  Ask external root only if the default fails. Remove `config_panel_domain`, which redirected Configure away from
  options; add Hide panel and drag/drop converter import in HA and manager.
- **2026-09-28 0.0.28**: Configure retains panel visibility; move hazardous control, unused DPs and pack settings to
  panel Options with restart on changes.
- **2026-09-28 0.0.29**: permit startup without a device file, recover failed entries after login, and run an
  embedded bridge during setup using the same state file so registration persists. Accept QR/sync/error submissions
  only after the corresponding form is shown, avoiding forwarded progress input. Create storage before login,
  register gateways first and always hand the fetched device file to the entry.
- **2026-09-28 0.0.30**: label the action “Fetch from Tuya Cloud”, reuse valid login, show QR only when needed;
  add panel fetch with idle/ten-minute session expiry and report done after closing and handing over the device file.
  Configure enables/links the panel; save only changed values, warn on hiding with unsaved edits.
- **2026-09-28 0.0.31**: respond after restart and refresh afterward; preserve the sidebar through reloads, remove
  it only on disable/delete/option-off, apply panel-only options without restart and confirm Hide only with unsaved edits.
- **2026-09-28 0.0.32**: Korean integration translations, key/placeholder parity tests; remove Configure's panel-removal option.
- **2026-09-28 0.0.33**: panel follows HA Korean/English, falls back to English and rebuilds on language changes;
  localize fetch states/dates and test key/placeholder coverage.
- **2026-09-28 0.0.34**: `rustuya.send_command` sends one raw DP, accepting Tuya IDs or unambiguous HA device
  identifiers. Reject lock/alarm/garage commands by category or descriptor unless hazardous control is enabled.
- **2026-09-28 0.0.35**, tuya2ildevice 0.3.12: prevent unavailable flapping on settings restarts, which made event
  entities appear pressed and triggered lights/plugs/IR automations. Merge retained per-DP state and seed new drivers
  with link and state; options restart uses `stop(offline=False)` / `start(resume=True)` to preserve presence.
- **2026-09-28 0.0.36**, 0.3.13: avoid querying every device on restart when bridge snapshots already provide state.
  Request get only for live reconnects or a connected device without state.
- **2026-09-28 0.0.37**: behavior-preserving refactor; no-change options show “Nothing changed” instead of refreshing.
  Consolidate bridge template following in `_BridgeFollower` and HA broker/session/default helpers in their modules.
