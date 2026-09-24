"""IL descriptor -> Home Assistant MQTT discovery. Pure: no transport, no clock.

Which entities a device becomes is il-ha's own plan (`ildevice.core.plan_entities`), so a device looks the same through
this path and through the il-ha integration; this module only writes each planned entity as a discovery config.

Every config reads the IL state topics and writes the IL set topics directly, with templates for the wire forms
(`true`/`false`, `#rrggbb`, an empty payload for *absent*). Where Home Assistant's MQTT platform wants one topic for
what IL keeps apart, the config points at a *relay* topic instead and `Rendered.routes` says what a message there
means in IL writes (a cover's open/close/stop, a lock, an alarm panel, a vacuum, a climate's mode with its power, a
fan's percentage). The one platform that only reads a JSON document (the vacuum) gets a *mirror*: `Rendered.mirrors`
says which IL values make that document. The publisher (`publisher.py`) carries out both.
"""

from __future__ import annotations

import colorsys
import json
from dataclasses import dataclass, field
from typing import Any

from ildevice.core import Descriptor, EntitySpec, plan_entities
from ildevice.core.plan import availability_prop
from ildevice.core.topics import Topics, resolve_topics, set_topic, state_topic
from ildevice.core.values import number_text

# An empty IL payload is *absent*. Home Assistant's MQTT platforms differ per field: the state-like ones read "None" as
# unknown (VALUE, BOOL), the others (a light's brightness, a cover's position, a select, ...) cannot parse it and simply
# ignore an empty payload, so those get the IL payload as it is (no template): the last value stays shown.
VALUE = "{{ value if value != '' else 'None' }}"
BOOL = "{{ 'None' if value == '' else ('true' if value | lower in ('true', 'on', '1') else 'false') }}"
# a number written the IL way: an integral value has no fraction (il-mqtt.md, section 3)
NUMBER = "{{ value | int if value == value | int else value }}"


@dataclass(frozen=True)
class Write:
    topic: str
    payload: str
    unless: tuple[str, str] | None = None
    """(state topic, payload): skip this write while that IL value is already this (il-ha turns a climate on before a
    mode only when it is not on)."""


@dataclass(frozen=True)
class Route:
    """What a message on a relay topic means. `map`: payload -> writes. `percent`: a percentage; `zero` are the writes
    for 0, `topic` takes any other value (`levels` given: the named level it falls in, else the number itself), and
    `before` are written first (turn the fan on)."""
    map: dict[str, tuple[Write, ...]] = field(default_factory=dict)
    percent: bool = False
    zero: tuple[Write, ...] = ()
    topic: str | None = None
    levels: tuple[str, ...] = ()
    before: tuple[Write, ...] = ()
    light: "LightTopics | None" = None

    def writes(self, payload: str, values: dict[str, str] | None = None) -> tuple[Write, ...]:
        """The IL writes for `payload`; `values` are the latest raw IL payloads by state topic, for `Write.unless`."""
        values = values or {}
        if self.light is not None:
            return self.light.writes(payload)
        if not self.percent:
            return tuple(w for w in self.map.get(payload.strip(), ())
                         if w.unless is None or values.get(w.unless[0], "").strip().lower() != w.unless[1])
        try:
            value = float(payload)
        except ValueError:
            return ()
        if value <= 0:
            return self.zero
        if self.levels:
            n = len(self.levels)                     # Home Assistant's percentage_to_ordered_list_item
            level = self.levels[min(n, max(1, -(-int(value * n) // 100))) - 1]
            return (*self.before, Write(self.topic, level))
        return (*self.before, Write(self.topic, number_text(min(100.0, value))))


def hs_to_hex(h: float, s: float) -> str:
    """Home Assistant's color_hs_to_RGB, as `#rrggbb` (il-ha writes a colour this way)."""
    r, g, b = (round(x * 255) for x in colorsys.hsv_to_rgb(h / 360, s / 100, 1))
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_hs(text: str) -> tuple[float, float] | None:
    """Home Assistant's color_RGB_to_hs of an IL `#rrggbb` (il-ha reads a colour this way)."""
    if not isinstance(text, str) or len(text) != 7 or not text.startswith("#"):
        return None
    try:
        r, g, b = (int(text[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return None
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return round(h * 360, 3), round(s * 100, 3)


@dataclass(frozen=True)
class LightTopics:
    """A light on Home Assistant's JSON schema: its IL set topics, for the commands (`writes`), and its IL state topics,
    for the state document (`state`)."""
    on: tuple[str, str]                                  # (state topic, set topic) of each slot the light has
    brightness: tuple[str, str] | None = None
    color_temperature: tuple[str, str] | None = None
    color: tuple[str, str] | None = None
    color_mode: str | None = None                        # state topic only (read only)
    lowest: int = 1

    def writes(self, payload: str) -> tuple[Write, ...]:
        try:
            cmd = json.loads(payload)
        except ValueError:
            return ()
        if not isinstance(cmd, dict):
            return ()
        if cmd.get("state") == "OFF":
            return (Write(self.on[1], "false"),)
        out = [Write(self.on[1], "true")]                # the order il-ha writes in
        color = cmd.get("color")
        if self.color and isinstance(color, dict) and "h" in color and "s" in color:
            out.append(Write(self.color[1], hs_to_hex(float(color["h"]), float(color["s"]))))
        if self.color_temperature and cmd.get("color_temp") is not None:
            out.append(Write(self.color_temperature[1], number_text(float(cmd["color_temp"]))))
        if self.brightness and cmd.get("brightness") is not None:
            out.append(Write(self.brightness[1], number_text(max(self.lowest, min(100, round(float(cmd["brightness"])))))))
        return tuple(out)

    def modes(self) -> list[str]:
        modes = (["hs"] if self.color else []) + (["color_temp"] if self.color_temperature else [])
        return modes or (["brightness"] if self.brightness else ["onoff"])

    def state(self, values: dict[str, str]) -> str:
        """The JSON state document from the latest raw IL payloads (by state topic), as il-ha would show the light."""
        doc: dict[str, Any] = {}
        on = values.get(self.on[0], "")
        doc["state"] = None if on == "" else ("ON" if on.strip().lower() in ("true", "on", "1") else "OFF")
        if self.brightness and (raw := values.get(self.brightness[0], "")) != "":
            try:
                doc["brightness"] = json.loads(raw)
            except ValueError:
                pass
        modes = self.modes()
        if len(modes) == 1:
            mode = modes[0]
        else:
            mode = "hs" if values.get(self.color_mode, "") == "color" else "color_temp"
        doc["color_mode"] = mode
        if mode == "hs" and self.color and (hs := hex_to_hs(values.get(self.color[0], ""))) is not None:
            doc["color"] = {"h": hs[0], "s": hs[1]}
        elif mode == "color_temp" and self.color_temperature and (raw := values.get(self.color_temperature[0], "")):
            try:
                doc["color_temp"] = round(json.loads(raw))
            except (ValueError, TypeError):
                pass
        if mode in ("hs", "color_temp") and not ("color" in doc or "color_temp" in doc):
            del doc["color_mode"]                        # nothing to show for the mode yet
        return json.dumps(doc, sort_keys=True)


@dataclass(frozen=True)
class Mirror:
    """A JSON document published (retained) on `topic`: key -> the IL state topic its value comes from, with how to
    read it (`bool`, `number` or `text`)."""
    topic: str
    fields: dict[str, tuple[str, str]] = field(default_factory=dict)
    light: LightTopics | None = None

    def sources(self) -> set[str]:
        if self.light is not None:
            lt = self.light
            return {t for t in (lt.on[0], lt.brightness and lt.brightness[0], lt.color and lt.color[0],
                                lt.color_temperature and lt.color_temperature[0], lt.color_mode) if t}
        return {topic for topic, _ in self.fields.values()}

    def payload(self, values: dict[str, str]) -> str:
        return self.light.state(values) if self.light is not None else mirror_payload(self.fields, values)


@dataclass
class Rendered:
    configs: dict[str, dict] = field(default_factory=dict)     # discovery config topic -> config
    routes: dict[str, Route] = field(default_factory=dict)     # relay topic -> route
    mirrors: list[Mirror] = field(default_factory=list)


@dataclass(frozen=True)
class Options:
    il_prefix: str = "il"
    discovery_prefix: str = "homeassistant"
    node_id: str = "ildevice"
    """The discovery topic's node id: every config this publisher owns is under `<discovery_prefix>/+/<node_id>/`."""
    relay_prefix: str = "ildevice-ha"
    """Relay and mirror topics: `<relay_prefix>/<device id>/<entity key>/...`."""
    source: str | None = None
    """The producer's presence topic suffix (`<il_prefix>/_producer/<source>`); the descriptor's `source` if None."""


def _object_id(key: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in key)


class _Device:
    def __init__(self, desc: Descriptor, opt: Options) -> None:
        self.desc, self.opt = desc, opt
        self.topics: Topics = resolve_topics(desc, opt.il_prefix)
        self.out = Rendered()

    def st(self, prop: str) -> str:
        return state_topic(self.desc, self.topics, prop)

    def set(self, prop: str) -> str:
        return set_topic(self.desc, self.topics, prop)

    def relay(self, spec: EntitySpec, verb: str) -> str:
        return f"{self.opt.relay_prefix}/{self.desc.id}/{_object_id(spec.key)}/{verb}"

    def availability(self, spec: EntitySpec) -> list[dict]:
        desc, source = self.desc, self.opt.source or self.desc.source
        out: list[dict] = []
        if source:
            out.append({"topic": f"{self.opt.il_prefix}/_producer/{source}",
                        "payload_available": "online", "payload_not_available": "offline"})
        if (avail := availability_prop(desc)) is not None:
            out.append({"topic": self.st(avail), "value_template": BOOL,
                        "payload_available": "true", "payload_not_available": "false"})
        req = spec.requires
        if req is not None and req.prop in desc.props:
            test = (f"value | lower in ('true', 'on', '1')" if req.in_ is None else f"value in {list(req.in_)!r}")
            out.append({"topic": self.st(req.prop), "payload_available": "yes", "payload_not_available": "no",
                        "value_template": "{{ 'yes' if " + test + " else 'no' }}"})
        return out

    def base(self, spec: EntitySpec) -> dict:
        desc = self.desc
        cfg: dict[str, Any] = {
            "name": spec.name,
            "unique_id": f"{self.opt.node_id}_{spec.unique_id}",
            "device": {"identifiers": [f"{self.opt.node_id}_{desc.id}"], "name": desc.label or desc.model or desc.id,
                       **({"manufacturer": desc.vendor} if desc.vendor else {}),
                       **({"model": desc.model} if desc.model else {})},
            "availability": self.availability(spec),
            "availability_mode": "all",
            "qos": 1,
        }
        if spec.entity_category:
            cfg["entity_category"] = spec.entity_category
        if spec.icon:
            cfg["icon"] = spec.icon
        return cfg

    def add(self, platform: str, spec: EntitySpec, cfg: dict) -> None:
        # an explicit null is meaningful for `name` (a composite takes the device's name) and a `payload_*` (the
        # feature is off: left out, Home Assistant would use its default payload and offer it); others are left out
        cfg = {k: v for k, v in cfg.items() if v is not None or k == "name" or k.startswith("payload_")}
        topic = f"{self.opt.discovery_prefix}/{platform}/{self.opt.node_id}/{_object_id(self.desc.id + '_' + spec.key)}/config"
        self.out.configs[topic] = cfg

    def prop(self, spec: EntitySpec, slot: str):
        name = spec.slots.get(slot) or spec.shared.get(slot)
        return self.desc.props[name] if name else None

    # ---- plain entities -----------------------------------------------------------------------------

    def plain(self, spec: EntitySpec) -> None:
        p = self.desc.props[spec.slots["value"]]
        cfg = self.base(spec)
        st, cmd = self.st(p.name), self.set(p.name)
        platform = spec.platform
        if platform == "sensor":
            cfg.update(state_topic=st, value_template=VALUE, device_class=spec.device_class,
                       state_class=spec.state_class, unit_of_measurement=spec.unit)
            if spec.device_class == "enum":
                cfg["options"] = list(spec.options)
        elif platform == "binary_sensor":
            cfg.update(state_topic=st, value_template=BOOL, payload_on="true", payload_off="false",
                       device_class=spec.device_class)
        elif platform == "switch":
            cfg.update(state_topic=st, value_template=BOOL, command_topic=cmd, payload_on="true", payload_off="false",
                       state_on="true", state_off="false", device_class=spec.device_class)
        elif platform == "number":
            cfg.update(state_topic=st, value_template=VALUE, command_topic=cmd, min=spec.min, max=spec.max,
                       step=spec.step, mode="box", unit_of_measurement=spec.unit, device_class=spec.device_class)
        elif platform == "select":
            cfg.update(state_topic=st, command_topic=cmd, options=list(spec.options))
        elif platform == "text":
            cfg.update(state_topic=st, command_topic=cmd)
        elif platform == "button":
            cfg.update(command_topic=cmd, payload_press="", device_class=spec.device_class)    # a trigger: any payload
        elif platform == "event":
            cfg.update(state_topic=st, event_types=list(spec.options), device_class=spec.device_class,
                       value_template='{{ {"event_type": value} | tojson if value else "None" }}')
        else:
            return
        self.add(platform, spec, cfg)

    # ---- composites -----------------------------------------------------------------------------------

    def light(self, spec: EntitySpec) -> None:
        # the JSON schema: one state document carries the colour mode (il-ha takes it from the `color_mode` role), so
        # a mirror writes it and a relay turns the JSON commands into IL writes
        cfg = self.base(spec)
        on, b = self.prop(spec, "on"), self.prop(spec, "brightness")
        ct, c, cm = self.prop(spec, "color_temperature"), self.prop(spec, "color"), self.prop(spec, "color_mode")
        pair = lambda p: (self.st(p.name), self.set(p.name)) if p is not None else None   # noqa: E731
        lt = LightTopics(on=pair(on), brightness=pair(b), color_temperature=pair(ct), color=pair(c),
                         color_mode=self.st(cm.name) if cm is not None else None,
                         lowest=max(1, round(b.min)) if b is not None and b.min else 1)
        relay, mirror = self.relay(spec, "set"), self.relay(spec, "state")
        self.out.routes[relay] = Route(light=lt)
        self.out.mirrors.append(Mirror(mirror, light=lt))
        cfg.update(schema="json", state_topic=mirror, command_topic=relay, supported_color_modes=lt.modes(),
                   brightness=b is not None, brightness_scale=100, flash=False, transition=False)
        if ct is not None:
            cfg.update(color_temp_kelvin=True, min_kelvin=int(ct.min) if ct.min else None,
                       max_kelvin=int(ct.max) if ct.max else None)
        self.add("light", spec, cfg)

    def cover(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        pos = self.prop(spec, "position")
        writable_pos = pos is not None and pos.writable
        route: dict[str, tuple[Write, ...]] = {}
        for verb, fallback in (("open", "100"), ("close", "0")):
            if (t := self.prop(spec, verb)) is not None:
                route[verb.upper()] = (Write(self.set(t.name), ""),)
            elif writable_pos:
                route[verb.upper()] = (Write(self.set(pos.name), fallback),)
        if (t := self.prop(spec, "stop")) is not None:
            route["STOP"] = (Write(self.set(t.name), ""),)
        if route:
            relay = self.relay(spec, "command")
            self.out.routes[relay] = Route(map=route)
            cfg.update(command_topic=relay, payload_open="OPEN" if "OPEN" in route else None,
                       payload_close="CLOSE" if "CLOSE" in route else None,
                       payload_stop="STOP" if "STOP" in route else None)
        else:
            cfg.update(payload_open=None, payload_close=None, payload_stop=None)
        if pos is not None:
            cfg.update(position_topic=self.st(pos.name), position_open=100, position_closed=0)
            if writable_pos:
                cfg["set_position_topic"] = self.set(pos.name)
        if (s := self.prop(spec, "cover_state")) is not None:
            cfg.update(state_topic=self.st(s.name), value_template=VALUE, state_open="open", state_closed="closed",
                       state_opening="opening", state_closing="closing", state_stopped="stopped")
        if (t := self.prop(spec, "tilt")) is not None:
            cfg.update(tilt_status_topic=self.st(t.name), tilt_min=0, tilt_max=100)
            if t.writable:
                cfg["tilt_command_topic"] = self.set(t.name)
        cfg["device_class"] = spec.device_class
        cfg["optimistic"] = False
        self.add("cover", spec, cfg)

    def climate(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        on, mode = self.prop(spec, "on"), self.prop(spec, "mode")
        hvac = {"auto", "heat", "cool", "heat_cool", "dry", "fan_only"}
        modes = [m for m in (mode.options if mode else ()) if m in hvac]
        route: dict[str, tuple[Write, ...]] = {}
        if on is not None:
            route["off"] = (Write(self.set(on.name), "false"),)
        for m in modes:
            power = (Write(self.set(on.name), "true", unless=(self.st(on.name), "true")),) if on is not None else ()
            route[m] = power + (Write(self.set(mode.name), m),)
        relay = self.relay(spec, "mode")
        self.out.routes[relay] = Route(map=route)
        cfg["modes"] = (["off"] if on is not None else []) + modes
        cfg["mode_command_topic"] = relay
        # the mode state needs both the power and the mode, so a mirror publishes them together; a climate with
        # neither still gets one (an empty document), or Home Assistant would assume `off` for it
        mirror_topic = self.relay(spec, "mode_state")
        fields = {}
        if on is not None:
            fields["on"] = (self.st(on.name), "bool")
        if mode is not None:
            fields["mode"] = (self.st(mode.name), "text")
        self.out.mirrors.append(Mirror(mirror_topic, fields))
        cfg["mode_state_topic"] = mirror_topic
        cfg["mode_state_template"] = (
            "{% set d = value_json %}"
            "{{ 'off' if d.on is defined and d.on == false else "
            "(d.mode if d.mode is defined and d.mode in " + repr(modes) + " else 'None') }}")
        if on is not None:
            cfg.update(power_command_topic=self.set(on.name), payload_on="true", payload_off="false")
        if (t := self.prop(spec, "target_temperature")) is not None:
            cfg.update(temperature_state_topic=self.st(t.name), temperature_state_template=VALUE,
                       temperature_command_topic=self.set(t.name), temperature_command_template=NUMBER, min_temp=spec.min, max_temp=spec.max,
                       temp_step=spec.step, precision=(spec.step if spec.step and spec.step < 1 else 1.0),
                       temperature_unit="C")
        if (c := self.prop(spec, "current_temperature")) is not None:
            cfg.update(current_temperature_topic=self.st(c.name), current_temperature_template=VALUE)
        if (h := self.prop(spec, "current_humidity")) is not None:
            cfg.update(current_humidity_topic=self.st(h.name), current_humidity_template=VALUE)
        if (a := self.prop(spec, "action")) is not None:
            actions = {"off", "heating", "cooling", "drying", "idle", "fan", "preheating", "defrosting"}
            cfg.update(action_topic=self.st(a.name),
                       action_template="{{ value if value in " + repr(sorted(actions)) + " else 'None' }}")
        if (f := self.prop(spec, "fan_speed")) is not None:
            cfg.update(fan_mode_state_topic=self.st(f.name), fan_mode_state_template=VALUE,
                       fan_mode_command_topic=self.set(f.name), fan_modes=list(f.options))
        for slot, key in (("swing_vertical", "swing_mode"), ("swing_horizontal", "swing_horizontal_mode")):
            if (s := self.prop(spec, slot)) is not None:
                cfg.update({f"{key}_state_topic": self.st(s.name),
                            f"{key}_state_template": "{{ 'None' if value == '' else ('on' if value == 'true' else 'off') }}",
                            f"{key}_command_topic": self.set(s.name),
                            f"{key}_command_template": "{{ 'true' if value == 'on' else 'false' }}",
                            f"{key}s": ["on", "off"]})
        self.add("climate", spec, cfg)

    def humidifier(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        on, target = self.prop(spec, "on"), self.prop(spec, "target_humidity")
        cfg.update(state_topic=self.st(on.name), state_value_template=BOOL, command_topic=self.set(on.name),
                   payload_on="true", payload_off="false", target_humidity_state_topic=self.st(target.name),
                   target_humidity_command_topic=self.set(target.name),
                   min_humidity=spec.min, max_humidity=spec.max, device_class=spec.device_class or "humidifier")
        if (m := self.prop(spec, "mode")) is not None:
            cfg.update(mode_state_topic=self.st(m.name), mode_command_topic=self.set(m.name), modes=list(m.options))
        if (h := self.prop(spec, "current_humidity")) is not None:
            cfg.update(current_humidity_topic=self.st(h.name))
        self.add("humidifier", spec, cfg)

    def fan(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        on = self.prop(spec, "on")
        cfg.update(state_topic=self.st(on.name), state_value_template=BOOL, command_topic=self.set(on.name),
                   payload_on="true", payload_off="false")
        speed, levels = self.prop(spec, "speed"), self.prop(spec, "fan_speed")
        if speed is not None or levels is not None:
            relay = self.relay(spec, "percentage")
            off = (Write(self.set(on.name), "false"),)
            if speed is not None:           # a percentage is preferred to named levels (il.md, `speed`)
                self.out.routes[relay] = Route(percent=True, zero=off, topic=self.set(speed.name))
                cfg.update(percentage_state_topic=self.st(speed.name))
            else:
                opts = list(levels.options)
                self.out.routes[relay] = Route(percent=True, zero=off, topic=self.set(levels.name), levels=tuple(opts))
                n = len(opts)
                cfg.update(percentage_state_topic=self.st(levels.name),
                           percentage_value_template=(
                               "{% set o = " + repr(opts) + " %}"
                               "{{ ((o.index(value) + 1) * 100 / " + str(n) + ") | round(0) | int "
                               "if value in o else '' }}"))
            cfg.update(percentage_command_topic=relay, speed_range_min=1, speed_range_max=100)
        if (o := self.prop(spec, "oscillate")) is not None:
            cfg.update(oscillation_state_topic=self.st(o.name),
                       oscillation_command_topic=self.set(o.name), payload_oscillation_on="true",
                       payload_oscillation_off="false")
        if (d := self.prop(spec, "direction")) is not None:
            cfg.update(direction_state_topic=self.st(d.name),
                       direction_command_topic=self.set(d.name))
        if (m := self.prop(spec, "mode")) is not None:
            cfg.update(preset_mode_state_topic=self.st(m.name), preset_mode_value_template=VALUE,
                       preset_mode_command_topic=self.set(m.name), preset_modes=list(m.options))
        self.add("fan", spec, cfg)

    def lock(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        locked = self.prop(spec, "locked")
        route = {"LOCK": (Write(self.set(locked.name), "true"),), "UNLOCK": (Write(self.set(locked.name), "false"),)}
        if (u := self.prop(spec, "unlatch")) is not None:
            route["OPEN"] = (Write(self.set(u.name), ""),)
        relay = self.relay(spec, "command")
        self.out.routes[relay] = Route(map=route)
        cfg.update(command_topic=relay, payload_lock="LOCK", payload_unlock="UNLOCK",
                   payload_open="OPEN" if "OPEN" in route else None)
        if (s := self.prop(spec, "lock_state")) is not None:
            cfg.update(state_topic=self.st(s.name), value_template=VALUE, state_locked="locked",
                       state_unlocked="unlocked", state_locking="locking", state_unlocking="unlocking",
                       state_jammed="jammed", state_open="open")
        else:
            cfg.update(state_topic=self.st(locked.name), value_template=BOOL, state_locked="true",
                       state_unlocked="false")
        cfg["optimistic"] = False
        self.add("lock", spec, cfg)

    def siren(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        on = self.prop(spec, "on")
        cfg.update(state_topic=self.st(on.name), state_value_template=BOOL, command_topic=self.set(on.name),
                   payload_on="true", payload_off="false", state_on="true", state_off="false",
                   command_template="{{ value }}", command_off_template="{{ value }}",
                   support_duration=False, support_volume_set=False)
        self.add("siren", spec, cfg)

    def valve(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        opened = self.prop(spec, "opened")
        cfg.update(state_topic=self.st(opened.name), value_template=BOOL, command_topic=self.set(opened.name),
                   payload_open="true", payload_close="false", state_open="true", state_closed="false",
                   reports_position=False, device_class=spec.device_class, optimistic=False)
        self.add("valve", spec, cfg)

    def alarm(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        state = self.prop(spec, "alarm_state")
        route = {}
        features = []
        for role, payload, feature in (("disarm", "DISARM", None), ("arm_home", "ARM_HOME", "arm_home"),
                                       ("arm_away", "ARM_AWAY", "arm_away"), ("arm_night", "ARM_NIGHT", "arm_night")):
            if (t := self.prop(spec, role)) is not None:
                route[payload] = (Write(self.set(t.name), ""),)
                if feature:
                    features.append(feature)
        relay = self.relay(spec, "command")
        self.out.routes[relay] = Route(map=route)
        states = ["disarmed", "armed_home", "armed_away", "armed_night", "armed_vacation", "armed_custom_bypass",
                  "pending", "triggered", "arming", "disarming"]
        cfg.update(state_topic=self.st(state.name), command_topic=relay, supported_features=features,
                   code_arm_required=False, code_disarm_required=False, code_trigger_required=False,
                   value_template="{{ value if value in " + repr(states) + " else 'None' }}")
        self.add("alarm_control_panel", spec, cfg)

    def vacuum(self, spec: EntitySpec) -> None:
        cfg = self.base(spec)
        route, features = {}, []
        for role, payload, feature in (("start", "start", "start"), ("pause", "pause", "pause"),
                                       ("return_home", "return_to_base", "return_home"),
                                       ("locate", "locate", "locate")):
            if (t := self.prop(spec, role)) is not None:
                route[payload] = (Write(self.set(t.name), ""),)
                features.append(feature)
        relay = self.relay(spec, "command")
        self.out.routes[relay] = Route(map=route)
        fields = {"state": (self.st(self.prop(spec, "vacuum_state").name), "text")}
        if (f := self.prop(spec, "fan_speed")) is not None:
            fields["fan_speed"] = (self.st(f.name), "text")
            features.append("fan_speed")
            cfg.update(set_fan_speed_topic=self.set(f.name), fan_speed_list=list(f.options))
        mirror = self.relay(spec, "state")
        self.out.mirrors.append(Mirror(mirror, fields))
        cfg.update(state_topic=mirror, command_topic=relay, supported_features=features,
                   payload_start="start", payload_pause="pause", payload_return_to_base="return_to_base",
                   payload_locate="locate")
        self.add("vacuum", spec, cfg)


_COMPOSITES = {"light": _Device.light, "cover": _Device.cover, "climate": _Device.climate,
               "humidifier": _Device.humidifier, "fan": _Device.fan, "lock": _Device.lock, "siren": _Device.siren,
               "valve": _Device.valve, "alarm_control_panel": _Device.alarm, "vacuum": _Device.vacuum}


def render(desc: Descriptor, options: Options | None = None) -> Rendered:
    """The discovery configs, relay routes and mirrors of one device."""
    dev = _Device(desc, options or Options())
    for spec in plan_entities(desc):
        builder = _COMPOSITES.get(spec.platform)
        if builder is not None:
            builder(dev, spec)
        else:
            dev.plain(spec)
    return dev.out


def mirror_payload(fields: dict[str, tuple[str, str]], values: dict[str, str]) -> str:
    """The JSON a mirror publishes, from the latest raw IL payload of each of its state topics (absent ones omitted)."""
    doc: dict[str, Any] = {}
    for key, (topic, kind) in fields.items():
        raw = values.get(topic, "")
        if raw == "":
            continue
        if kind == "bool":
            doc[key] = raw.strip().lower() in ("true", "on", "1")
        elif kind == "number":
            try:
                doc[key] = json.loads(raw)
            except ValueError:
                continue
        else:
            doc[key] = raw
    return json.dumps(doc, sort_keys=True)
