"""Configuration rules shared by the daemon, manager and Home Assistant."""

import math
import secrets


def client_id(kind: str) -> str:
    return f"rustuya-{kind}-{secrets.token_hex(8)}"


def topic_ok(value: object, *, source: bool = False) -> bool:
    return (isinstance(value, str) and bool(value)
            and not any(c in value for c in "+#\0") and not any(c.isspace() for c in value)
            and "" not in value.split("/")
            and (not source or ("/" not in value and not value.startswith("_"))))


def validate_topics(root: str, prefix: str, source: str) -> None:
    for name, value in (("root", root), ("prefix", prefix), ("source", source)):
        if not topic_ok(value, source=name == "source"):
            raise ValueError(f"invalid MQTT {name}: {value!r}")


def boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")  # noqa: TRY004 -- configuration errors use ValueError
    return value


def interval(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)
