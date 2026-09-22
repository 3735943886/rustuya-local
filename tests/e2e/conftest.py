import shutil
import socket
import subprocess
import time

import pytest


try:
    import pytest_socket  # noqa: F401
except ImportError:
    pass
else:
    @pytest.fixture(autouse=True)
    def _real_sockets(socket_enabled):
        """`pytest-homeassistant-custom-component` (installed for tests/ha/) blocks real sockets by default for the
        whole session once it's on the venv, which every test here needs (a real mosquitto, real paho connections);
        opt back in for this whole directory instead of every test asking for `socket_enabled` itself. `pytest_socket`
        decides this per test from the test's *static* fixture list, so `socket_enabled` has to be a normal parameter
        here, not requested dynamically — and the fixture (and this whole block) only exists when that plugin does."""


try:
    import pytest_homeassistant_custom_component  # noqa: F401
except ImportError:
    pass
else:
    @pytest.fixture(autouse=True)
    def verify_cleanup():
        """Shadow `pytest-homeassistant-custom-component`'s autouse `verify_cleanup` fixture for this directory only
        (tests/ha/ keeps the real one). That fixture asserts every thread started during a test function is gone by
        the time the function returns, which does not hold here on purpose: `stack` (in test_full_stack.py) is
        module-scoped and its second `tuyamock` device, started mid-test when a device is made to drop off the
        network, is meant to keep running for the rest of the module. Without this override that intentional
        long-lived thread is misreported as a leak."""
        yield


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
