import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # custom_components/ is importable as a package


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations):
    return


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def broker():
    """A real mosquitto on a free port (skipped when the binary is not installed) — for test_init.py, which sets
    entries up for real and needs an actual MQTT connection; test_config_flow.py never reaches this."""
    # session-scoped, so its first (and only) use can land in a test before pytest sets up the
    # function-scoped `socket_enabled` a test requests -- higher-scoped fixtures are set up before
    # lower-scoped ones within the same test, regardless of request-argument order. Enable
    # directly rather than depending on that ordering.
    try:
        import pytest_socket
    except ImportError:
        pass
    else:
        pytest_socket.enable_socket()
    exe = shutil.which("mosquitto")
    if exe is None:
        pytest.skip("mosquitto is not installed")
    port = _free_port()
    proc = subprocess.Popen([exe, "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("mosquitto did not start")
    yield port
    proc.terminate()
    proc.wait(5)
