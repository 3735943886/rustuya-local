"""A real MQTT broker on a free port for the tests that need one.

`RUSTUYA_TEST_BROKER` picks it: `mosquitto` (the default), or `pymqttd`, the broker the integration falls back to when a
user has none. Each is skipped when its executable is not on PATH; `RUSTUYA_TEST_PYMQTTD` can name the `pymqttd`
executable (another venv's, say).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _command(kind: str, port: int) -> list[str]:
    if kind == "mosquitto":
        exe = shutil.which("mosquitto")
        if exe is None:
            pytest.skip("mosquitto is not installed")
        # `-p` runs the default local-only configuration: this machine's clients, anonymous, nothing persisted
        return [exe, "-p", str(port)]
    if kind == "pymqttd":
        exe = os.environ.get("RUSTUYA_TEST_PYMQTTD") or shutil.which("pymqttd")
        if exe is None:
            pytest.skip("pymqttd is not installed")
        return [exe, "--port", str(port)]
    raise pytest.UsageError(f"RUSTUYA_TEST_BROKER={kind!r}: expected mosquitto or pymqttd")


@contextmanager
def running_broker() -> Iterator[int]:
    """The port of a broker started for the duration of the block."""
    kind = os.environ.get("RUSTUYA_TEST_BROKER", "mosquitto")
    port = free_port()
    proc = subprocess.Popen(_command(kind, port), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if proc.poll() is not None:
                pytest.fail(f"{kind} exited at startup ({proc.returncode})")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail(f"{kind} did not start")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
