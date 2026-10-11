"""Host-owned device registry and IL MQTT adapter over transport-independent TuyaDriver instances."""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Sequence
from typing import Any

from tuya2ildevice import Command, Connected, Disconnected, Message, Timer, TuyaDriver
from tuya2ildevice.native import register_rules, unregister_rules

from .il import (
    IL,
    BridgeCommand,
    IlTopics,
    Publish,
    Subscribe,
    Unschedule,
    decode_write,
    usable_id,
)
from .rules import rules as default_rules
from .rules import upgrade_overrides

# Hubs using each registered rule pack. The engine keeps a pack until it is released, per process, and packs are
# keyed by content, so Hubs can share one: the last Hub to stop using a pack releases it.
_pack_users: Counter[str] = Counter()


def _release(rules_id: str) -> None:
    _pack_users[rules_id] -= 1
    if _pack_users[rules_id] <= 0:
        del _pack_users[rules_id]
        unregister_rules(rules_id)


class Hub:
    """Own the fleet and map driver data to IL MQTT host actions. No Tuya conversion knowledge lives here."""

    def __init__(self, devices: list[dict], *, il: IlTopics | None = None, rules: dict | None = None,
                 **driver_kwargs: Any):
        self.il = il or IlTopics()
        self._kw = {**driver_kwargs, 'rules': rules if rules is not None else default_rules()}
        self._kw['overrides'] = upgrade_overrides(self._kw.get('overrides'))
        self._rules_id = register_rules(self._kw['rules'])
        _pack_users[self._rules_id] += 1
        for device in devices:
            self._check_id(device['id'])
        self._devices = {d['id']: d for d in devices}
        self.drivers = {i: self._create(d, self._kw) for i, d in self._devices.items()}

    @staticmethod
    def _create(device, options):
        return TuyaDriver(device, **options)

    @property
    def records(self) -> dict[str, dict]:
        return dict(self._devices)

    @staticmethod
    def _check_id(device_id: str) -> None:
        if not usable_id(device_id):
            raise ValueError(f'device id {device_id!r} is not usable as an il-mqtt topic level (M-2)')

    @staticmethod
    def _same(old: TuyaDriver, new: TuyaDriver) -> bool:
        # Version-specific driver configuration metadata, compared without interpreting Tuya DPs.
        return (old.rules_id == new.rules_id
                and old.state['block'] == new.state['block']
                and old.state['delta'] == new.state['delta']
                and old.descriptor == new.descriptor
                and [c['settings'] for c in old.state['covers']] == [c['settings'] for c in new.state['covers']])

    def _adopt(self, old: TuyaDriver | None, new: TuyaDriver, seed: Sequence, now: float,
               *, rules: dict) -> list:
        device_id = new.descriptor['id']
        actions = []
        if old is not None:
            new.carry(old)
            actions.extend(Unschedule(device_id, name) for name in sorted(old.timers))
            motion_role = rules['motion_property']['role']
            gone = sorted(p for p, spec in old.descriptor['props'].items()
                          if p not in new.descriptor['props'] or spec.get('role') == motion_role)
            actions.extend(self.il.clear(device_id, gone))
        actions.extend(self.il.actions(device_id, new.describe(seed, now=now)))
        return actions

    def set_device(self, device: dict, seed: Sequence[Connected | Disconnected | Message] = (),
                   now: float | None = None) -> list:
        self._check_id(device['id'])
        new = self._create(device, self._kw)
        old = self.drivers.get(device['id'])
        if not seed and old is not None and old.linked:
            seed = [Connected()]
        ask = any(isinstance(i, Connected) for i in seed) and not any(
            isinstance(i, Message) and i.channel == 'state' for i in seed)
        actions = self._adopt(old, new, seed, time.time() if now is None else now, rules=self._kw['rules'])
        if ask:
            actions.append(BridgeCommand(device['id'], 'get'))
        self.drivers[device['id']] = new
        self._devices[device['id']] = device
        return actions

    def remove_device(self, device_id: str) -> list:
        old = self.drivers.pop(device_id, None)
        self._devices.pop(device_id, None)
        if old is None:
            return []
        return [*(Unschedule(device_id, name) for name in sorted(old.timers)),
                *self.il.clear(device_id, old.descriptor['props'], descriptor=True)]

    def reload(self, overrides: dict | None, *, rules: dict | None = None, now: float | None = None) -> list:
        options = {**self._kw, 'overrides': upgrade_overrides(overrides)}
        rules_id = self._rules_id
        if rules is not None:
            options['rules'] = rules
            rules_id = register_rules(rules)
        try:
            fresh = {i: self._create(d, options) for i, d in self._devices.items()}
        except Exception:
            if not _pack_users[rules_id]:           # refused: a pack nobody took is not kept
                unregister_rules(rules_id)
            raise
        actions = []
        now = time.time() if now is None else now
        replacements = {}
        for device_id, new in fresh.items():
            old = self.drivers[device_id]
            if self._same(old, new):
                continue
            seed = [Connected()] if old.linked else []
            if old.synced:
                seed.append(Message('state', old.dps))
            actions.extend(self._adopt(old, new, seed, now, rules=options['rules']))
            if old.linked and not old.synced:
                actions.append(BridgeCommand(device_id, 'get'))
            replacements[device_id] = new
        self.drivers.update(replacements)
        self._kw = options
        if rules_id != self._rules_id:              # every driver was replaced: none uses the old pack now
            _pack_users[rules_id] += 1
            _release(self._rules_id)
            self._rules_id = rules_id
        return actions

    def subscriptions(self) -> list[Subscribe]:
        return [Subscribe(IL, self.il.set_subscription())]

    def presence(self, online: bool) -> Publish:
        return Publish(IL, self.il.presence, 'online' if online else 'offline', True)

    def start(self, now: float | None = None) -> list:
        now = time.time() if now is None else now
        actions = [self.presence(True)]
        for device_id, driver in self.drivers.items():
            actions.extend(self.il.actions(device_id, driver.describe(now=now)))
        return actions

    def stop(self) -> list[Publish]:
        return [self.presence(False)]

    def on_bridge_message(self, now: float, device_id: str, inp: Connected | Disconnected | Message,
                          retained: bool = False) -> list:
        driver = self.drivers.get(device_id)
        if driver is None or (retained and isinstance(inp, Message) and inp.channel != 'state'):
            return []
        actions = self.il.actions(device_id, driver.handle(now, inp))
        if isinstance(inp, Connected) and not (retained and driver.synced):
            actions.append(BridgeCommand(device_id, 'get'))
        return actions

    def on_timer(self, now: float, device_id: str, name: str) -> list:
        driver = self.drivers.get(device_id)
        return self.il.actions(device_id, driver.handle(now, Timer(name))) if driver is not None else []

    def on_il(self, now: float, topic: str, payload: bytes | str, retained: bool = False) -> list:
        loc = self.il.parse_set(topic)
        if loc is None or retained:
            return []
        device_id, prop = loc
        driver = self.drivers.get(device_id)
        if driver is None:
            return []
        if isinstance(payload, bytes):
            payload = payload.decode('utf-8', 'replace')
        ptype = driver.descriptor['props'].get(prop, {}).get('type', '')
        return self.il.actions(device_id, driver.handle(now, Command(prop, decode_write(ptype, payload))))
