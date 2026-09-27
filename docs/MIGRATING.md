# Moving from rustuya-homeassistant

rustuya-homeassistant (`rustuya-ha`) published Home Assistant MQTT discovery for your Tuya devices, as a CLI or a
rustuya-manager plugin. It is retired. Its replacement is two pieces:

- **rustuya-local** turns the devices on rustuya-bridge into IL devices (MQTT topics under a prefix such as `il/tuya`).
  Run it one way: the `rustuya` Home Assistant integration (HACS), the rustuya-manager plugin (catalog: *Tuya (IL)*),
  or the `rustuya-local run` daemon.
- **il-ha** ([ildevice-homeassistant](https://github.com/3735943886/ildevice-homeassistant), HACS) turns IL devices
  into Home Assistant entities. MQTT discovery is no longer used.

## What changes for you

- **Entity ids change.** The old entities belonged to Home Assistant's `mqtt` integration; the new ones belong to
  `ildevice`, and Home Assistant keeps the two apart. Automations, scripts and dashboards that name the old entities
  need the new ids. If you clear the old discovery first (step 1), the old ids are free and you can rename the new
  entities to them in Home Assistant.
- **Entities follow Home Assistant core's `tuya` integration** (tuya2ildevice reproduces it), so a device can come out
  with more, fewer or differently split entities than v1 made.
- **Availability** comes from rustuya-local's presence: when it stops, its devices show unavailable.

## Steps

1. **Remove the old discovery** while `rustuya-ha` still runs, so Home Assistant drops those entities:

   ```bash
   rustuya-ha clear '*' --dry-run     # what would be cleared
   rustuya-ha clear '*' -y            # clear it (a backup is written first; `rustuya-ha restore --last` undoes it)
   ```

   Then uninstall the rustuya-homeassistant manager plugin, or stop running `rustuya-ha`. Leaving both running shows
   every device twice.

2. **Install rustuya-local** one way (see the [README](../README.md#home-assistant)). Keep the same rustuya-bridge and
   device file (`tuyadevices.json`).

3. **Install il-ha** from HACS and add the **IL device** integration with the prefix rustuya-local publishes under
   (`il` unless you set another one in rustuya-local; both sides must match). Add the devices it offers, or turn on
   *Add discovered devices automatically*.

4. **Bring your custom converters** into the new converters directory:

   | rustuya-local run as | directory |
   |---|---|
   | Home Assistant integration | `<config>/rustuya_converters/` |
   | rustuya-manager plugin | `rustuya-local/custom_converters/` in the manager's plugin data directory |
   | daemon | `custom_converters` in its `config.json` |

   - `*.json` files work as they are: `dp_meta`, `model` and `discovery_overrides.cover` are converted on load; other
     `discovery_overrides` fields are reported and dropped.
   - The curated pack (`00_default.json`, `00_curtain.py`) is built into tuya2ildevice: leave it out.
   - A `*.py` file (`setup(api)`) is reported, not run. Port it to a `Converter`:
     [porting v1 converters](https://github.com/3735943886/tuya2ildevice/blob/master/docs/porting-v1-converters.md).

   Changes to the directory are picked up while running, no restart needed.

5. **Update automations and dashboards** to the new entity ids.
