"""Selective reconciliation of the cloud device list against what rustuya-bridge actually holds — the missing /
mismatch / orphan handling rustuya-manager's own UI offers, as one config/options-flow form. `Manager.sync()` only
computes the diff (auto-applying it is a policy decision it leaves to the caller), so nothing here changes the bridge
unless the user ticks it: every field defaults to empty.

  add     cloud has it, the bridge does not        -> `add` command with the cloud device's credentials
  update  both have it but a field differs         -> the same `add` again (the bridge updates an existing entry)
  remove  the bridge has it, the cloud list does not -> `remove` command
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

ADD, UPDATE, REMOVE = "add", "update", "remove"


def add_extra(dev: Any) -> dict[str, Any]:
    """The `add` command's payload fields for `dev`, as rustuya-manager's UI builds them: a WiFi device carries its
    key/ip/version (each only when the cloud actually pins one — "Auto" is a wildcard, not a value to push), a
    sub-device its cid and parent."""
    extra: dict[str, Any] = {}
    if dev.type == "WiFi":
        if dev.key and dev.key != "Auto":
            extra["key"] = dev.key
        if dev.ip and dev.ip != "Auto":
            extra["ip"] = dev.ip
        if dev.version and dev.version != "Auto":
            extra["version"] = dev.version
    else:
        if dev.cid:
            extra["cid"] = dev.cid
        if dev.parent_id:
            extra["parent_id"] = dev.parent_id
    return extra


def _label(dev: Any, reasons: list[str] | None = None) -> str:
    text = f"{dev.name} ({dev.id})"
    return f"{text}: {', '.join(reasons)}" if reasons else text


def has_changes(diff: Any) -> bool:
    return bool(diff.missing or diff.mismatched or diff.orphaned)


def placeholders(diff: Any) -> dict[str, str]:
    return {"missing": str(len(diff.missing)), "mismatched": str(len(diff.mismatched)),
            "orphaned": str(len(diff.orphaned))}


def schema(diff: Any) -> vol.Schema:
    """One optional multi-select per kind of difference that exists; nothing pre-selected."""
    fields: dict[Any, Any] = {}
    if diff.missing:
        fields[vol.Optional(ADD, default=[])] = cv.multi_select({d.id: _label(d) for d in diff.missing})
    if diff.mismatched:
        fields[vol.Optional(UPDATE, default=[])] = cv.multi_select(
            {d.id: _label(d, reasons) for d, reasons in diff.mismatched})
    if diff.orphaned:
        fields[vol.Optional(REMOVE, default=[])] = cv.multi_select({d.id: _label(d) for d in diff.orphaned})
    return vol.Schema(fields)


async def apply(manager: Any, diff: Any, user_input: dict[str, Any]) -> int:
    """Send the commands the user selected; returns how many. Only ids that are actually in `diff` for that kind are
    acted on. A `RuntimeError` (broker down, templates unresolved) propagates for the flow to show."""
    missing = {d.id: d for d in diff.missing}
    mismatched = {d.id: d for d, _ in diff.mismatched}
    orphaned = {d.id for d in diff.orphaned}
    sent = 0
    for device_id in user_input.get(ADD, []):
        if device_id in missing:
            await _register(manager, missing[device_id])
            sent += 1
    for device_id in user_input.get(UPDATE, []):
        if device_id in mismatched:
            await _register(manager, mismatched[device_id])
            sent += 1
    for device_id in user_input.get(REMOVE, []):
        if device_id in orphaned:
            await manager.remove_device(device_id)
            sent += 1
    return sent


async def _register(manager: Any, dev: Any) -> None:
    await manager.publish_command("add", target_id=dev.id, target_name=dev.name, extra=add_extra(dev) or None)
