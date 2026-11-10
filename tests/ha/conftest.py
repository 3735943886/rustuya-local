import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # custom_components/ is importable as a package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
from brokers import running_broker


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations):
    return


@pytest.fixture(scope="session")
def broker():
    """A real broker on a free port (see tests/e2e/brokers.py; skipped when not installed) — for test_init.py, which sets
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
    with running_broker() as port:
        yield port
