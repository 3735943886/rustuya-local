# Migration guide

For the current component boundaries and file precedence, see [Architecture](ARCHITECTURE.md).
Use [README](../README.md) for installation and [Changelog](../CHANGELOG.md) for version-specific changes.

## Moving to rustuya-local 0.1 / tuya-interpreter

The device file, MQTT configuration and IL topics stay the same. Custom files in the converters directory now use
the rule-layer format. Wrap an existing product/device override mapping in `{"overrides": ...}`. For example:

```json
{"overrides": {"product_id": {"cover": {"invert_position": true}}}}
```

Back up the converters directory before editing. Apply the same conversion to `zz_settings.json` to preserve cover settings saved by an older version. Leave
`options.overrides` in the daemon configuration as a product/device mapping; the host still accepts that format.
Rewrite Python converters as JSON programs under `converters.declarative`. Invalid JSON rule layers are reported
and skipped, so check the logs after upgrading. The managed override pack updates automatically when enabled;
files you edited remain yours to convert. JSON files use filename order, including `zz_settings.json`; check for later
files or inline overrides that replace saved settings. See the cover compatibility details below for `infer_motion`
and `converters.cover_motion`.

## Moving from rustuya-homeassistant

rustuya-homeassistant (`rustuya-ha`) published Home Assistant MQTT discovery for your Tuya devices, as a CLI or a
rustuya-manager plugin. It is retired. Its replacement is two pieces:

- **rustuya-local** turns the devices on rustuya-bridge into IL devices (MQTT topics under a prefix such as `il/tuya`).
  Run it one way: the `rustuya` Home Assistant integration (HACS), the rustuya-manager plugin (catalog: *Tuya (IL)*),
  or the `rustuya-local run` daemon.
- **[ildevice-homeassistant](https://github.com/3735943886/ildevice-homeassistant)** (HACS) turns IL devices
  into Home Assistant entities. MQTT discovery is no longer used.

### What changes for you

- **Entity ids change.** The old entities belonged to Home Assistant's `mqtt` integration; the new ones belong to
  `ildevice`, and Home Assistant keeps the two apart. Automations, scripts and dashboards that name the old entities
  need the new ids. If you clear the old discovery first (step 1), the old ids are free and you can rename the new
  entities to them in Home Assistant.
- **Entities follow Home Assistant core's `tuya` integration** (tuya-interpreter reproduces it), so a device can come out
  with more, fewer or differently split entities than v1 made.
- **Availability** comes from rustuya-local's presence: when it stops, its devices show unavailable.

### Steps

1. **Remove the old discovery** while `rustuya-ha` still runs, so Home Assistant drops those entities:

   ```bash
   rustuya-ha clear '*' --dry-run     # what would be cleared
   rustuya-ha clear '*' -y            # clear it (a backup is written first; `rustuya-ha restore --last` undoes it)
   ```

   Then uninstall the old manager plugin (*Home Assistant Discovery*), or stop running `rustuya-ha`.

2. **Install rustuya-local** one way (see the [README](../README.md#home-assistant)). Keep the same rustuya-bridge and
   device file (`tuyadevices.json`).

3. **Install ildevice-homeassistant** from HACS and add the **IL device** integration with the prefix rustuya-local publishes under
   (`il` unless you set another one in rustuya-local; both sides must match). Add the devices it offers, or turn on
   *Add discovered devices automatically*.

4. **Bring your custom converters** into the new converters directory:

   | rustuya-local run as | directory |
   |---|---|
   | Home Assistant integration | `<config>/rustuya_converters/` |
   | rustuya-manager plugin | `rustuya-local/custom_converters/` in the manager's plugin data directory |
   | daemon | `custom_converters` in its `config.json` |

   - `*.json` files are rule layers, with product/device fixes under `overrides`
     ([User overrides](https://github.com/3735943886/tuya-interpreter/blob/master/docs/device-customization.md#user-overrides)); the old ones are not read, so rewrite them.
     A property the tables do not build is defined from its dp in `props`.
   - A curtain's `invert_position` / `invert_set_position`, reversed command words and derived state are not files any
     more: each cover has configuration switches for them (README, *Cover settings*).
   - Rewrite Python converters as JSON programs under `converters.declarative`; Python plugins are not loaded by rustuya-local 0.1.
   - `converters.cover_motion` and a cover's `infer_motion` are read as the cover's `state_source` (`inferred`, or
     `none` for `infer_motion: false`), and a `cover_motion` `settle` as the cover's `settle`; its other keys are
     dropped with a warning. `zz_settings.json` is rewritten in the new form the next time a setting is saved.
   - Leave the old pack (`00_default.json`, `00_curtain.py`, `.rustuya_pack.json`) out. Official rules ship with tuya-interpreter; online updates arrive under
     `rules/builtin/` automatically. User files are applied after all official rules.

   Changes to the directory are picked up while running, no restart needed.

5. **Update automations and dashboards** to the new entity ids.
