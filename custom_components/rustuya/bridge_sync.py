"""Selective reconciliation of the cloud device list against what rustuya-bridge actually holds — the missing /
mismatch / orphan handling rustuya-manager's own UI offers, as one config/options-flow form. `Manager.sync()` only
computes the diff (auto-applying it is a policy decision it leaves to the caller), so nothing here changes the bridge
unless the user ticks it: every field defaults to empty.

  add     cloud has it, the bridge does not        -> `add` command with the cloud device's credentials
  update  both have it but a field differs         -> the same `add` again (the bridge updates an existing entry)
  remove  anything the bridge holds — in sync, mismatched or orphaned (not in the cloud list) -> `remove` command

Every device in either list appears somewhere on the form with its state, so a fully synced setup is still
manageable (a synced device can be removed from the bridge).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

ADD, UPDATE, REMOVE = "add", "update", "remove"
AUTO = "Auto"           # the cloud's wildcard for a field it does not pin


def add_extra(dev: Any) -> dict[str, Any]:
    """The `add` command's payload fields for `dev`, as rustuya-manager's UI builds them: a WiFi device carries its
    key/ip/version (each only when the cloud actually pins one — "Auto" is a wildcard, not a value to push), a
    sub-device its cid and parent."""
    if dev.type == "WiFi":
        return {k: v for k in ("key", "ip", "version") if (v := getattr(dev, k)) and v != AUTO}
    return {k: v for k in ("cid", "parent_id") if (v := getattr(dev, k))}


def _label(dev: Any, state: str, reasons: list[str] | None = None) -> str:
    text = f"[{state}] {dev.name} ({dev.id})"
    return f"{text}: {', '.join(reasons)}" if reasons else text


def has_changes(diff: Any) -> bool:
    return bool(diff.missing or diff.mismatched or diff.orphaned)


def has_devices(diff: Any) -> bool:
    return has_changes(diff) or bool(diff.synced)


def placeholders(diff: Any) -> dict[str, str]:
    return {"synced": str(len(diff.synced)), "missing": str(len(diff.missing)),
            "mismatched": str(len(diff.mismatched)), "orphaned": str(len(diff.orphaned))}


def schema(diff: Any) -> vol.Schema:
    """One optional multi-select per action that has candidates; nothing pre-selected. `remove` lists everything
    the bridge holds (in sync, mismatched and orphaned), each tagged with its state."""
    fields: dict[Any, Any] = {}
    if diff.missing:
        fields[vol.Optional(ADD, default=[])] = cv.multi_select({d.id: _label(d, "missing") for d in diff.missing})
    if diff.mismatched:
        fields[vol.Optional(UPDATE, default=[])] = cv.multi_select(
            {d.id: _label(d, "mismatch", reasons) for d, reasons in diff.mismatched})
    on_bridge = {d.id: _label(d, "orphan") for d in diff.orphaned}
    on_bridge.update({d.id: _label(d, "mismatch", reasons) for d, reasons in diff.mismatched})
    on_bridge.update({d.id: _label(d, "synced") for d in diff.synced})
    if on_bridge:
        fields[vol.Optional(REMOVE, default=[])] = cv.multi_select(on_bridge)
    return vol.Schema(fields)


def _on_bridge(diff: Any) -> dict[str, Any]:
    """Every device the bridge holds, by id: orphaned, mismatched and in sync."""
    return {d.id: d for d in (*diff.orphaned, *(d for d, _ in diff.mismatched), *diff.synced)}


async def apply(manager: Any, diff: Any, user_input: dict[str, Any]) -> int:
    """Send the commands the user selected; returns how many. Only ids that are actually in `diff` for that kind are
    acted on. A `RuntimeError` (broker down, templates unresolved) propagates for the flow to show."""
    sent = 0
    for action, candidates in ((ADD, {d.id: d for d in diff.missing}),
                               (UPDATE, {d.id: d for d, _ in diff.mismatched})):
        for device_id in parents_first(list(user_input.get(action, [])), diff):     # a gateway before its sub-devices
            if device_id in candidates:
                await _register(manager, candidates[device_id])
                sent += 1
    on_bridge = _on_bridge(diff)
    for device_id in user_input.get(REMOVE, []):
        if device_id in on_bridge:
            await manager.remove_device(device_id)
            sent += 1
    return sent


async def _register(manager: Any, dev: Any) -> None:
    await manager.publish_command("add", target_id=dev.id, target_name=dev.name, extra=add_extra(dev) or None)


# ---- the panel's device list (panel.py's BridgeView) ---------------------------------------------------------------

CATEGORIES = ("missing", "orphan", "mismatch", "synced")      # rustuya-manager's order: presence wrong, then fields


def _fields(dev: Any) -> dict[str, Any]:
    return {"name": dev.name, "type": dev.type, "cid": dev.cid, "parent_id": dev.parent_id, "key": dev.key,
            "ip": dev.ip, "version": dev.version}


def listing(diff: Any, bridge: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every device of the diff as the panel draws it: `category`, the mismatch `reasons`, and the `cloud` and
    `bridge` side of it (None where that side has none). `bridge` is the manager's bridge-side devices by id, for the
    bridge's own values of a synced or mismatched device; without it those show the cloud's."""
    bridge = bridge or {}
    out: list[dict[str, Any]] = []

    def row(dev: Any, category: str, cloud: Any, on_bridge: Any, reasons: list[str] | None = None) -> None:
        out.append({"id": dev.id, "category": category, "reasons": reasons or [],
                    "cloud": _fields(cloud) if cloud is not None else None,
                    "bridge": _fields(on_bridge) if on_bridge is not None else None})

    for dev in diff.missing:
        row(dev, "missing", dev, None)
    for dev in diff.orphaned:
        row(dev, "orphan", None, dev)
    for dev, reasons in diff.mismatched:
        row(dev, "mismatch", dev, bridge.get(dev.id, dev), reasons)
    for dev in diff.synced:
        row(dev, "synced", dev, bridge.get(dev.id, dev))
    return out


def parents_first(ids: list[str], diff: Any) -> list[str]:
    """`ids` with gateways and WiFi devices ahead of sub-devices, so a gateway is on the bridge before its children."""
    subs = {d.id for d in (*diff.missing, *(d for d, _ in diff.mismatched)) if d.type == "SubDevice"}
    return sorted(ids, key=lambda i: i in subs)
