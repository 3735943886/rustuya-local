# rustuya-manager plugin

Next to [rustuya-manager](https://github.com/3735943886/rustuya-manager), rustuya-local appears as a **Tuya (IL)** tab
and runs the same service under the manager: the manager's broker and bridge root, and the manager's device list
(followed). For Home Assistant, use the integration instead (see the [README](../README.md)); run only one IL
producer per bridge.

## Install

Either:

- `pip install rustuya-local` in the manager's environment, or
- the drop-in zip each release carries (`rustuya_local-<version>-dropin.zip`): unpack it into the manager's plugin
  directory, or install it from the manager's plugin catalog.

Installed both ways, the plugin runs once.

The drop-in zip vendors the tuya-interpreter binding, its native library and builtin rules, and is built by
`scripts/build_dropin.py` on the release runner (Linux x86_64). Use a matching OS and architecture, or install with
pip to get the right wheel. It freezes the tuya-interpreter it was built with: a tuya-interpreter fix reaches manager
users with the next rustuya-local release.

## Settings

Its data dir (`rustuya-local/` next to the manager's plugins) holds `settings.json` (`il`, `options`) and
`custom_converters/`, and the tab edits both:

- Saved settings restart the service in place.
- Saved converter files are picked up while it runs.
- A settings file it cannot use is shown on the tab until it is fixed.
- Automatic builtin rule updates can be turned off in its settings.

Rule files and cover settings work as described in the README's
[Non-standard devices](../README.md#non-standard-devices) and [Cover settings](../README.md#cover-settings),
with the data dir's `custom_converters/` in place of `<config>/rustuya_converters`.

If another producer already runs with the same IL prefix and source, the tab shows it and retries.
