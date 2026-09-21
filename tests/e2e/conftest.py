import shutil
import socket
import subprocess
import time

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def broker():
    """A real mosquitto on a free port (skipped when the binary is not installed)."""
    exe = shutil.which("mosquitto")
    if exe is None:
        pytest.skip("mosquitto is not installed")
    port = _free_port()
    # `-p` runs the default local-only configuration: this machine's clients, anonymous, nothing persisted
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
