import pytest
from brokers import running_broker

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


@pytest.fixture(scope="session")
def broker():
    """A real broker on a free port (see brokers.py; skipped when not installed)."""
    # session-scoped, so its first (and only) use can land in a test where pytest hasn't set up
    # the function-scoped `_real_sockets`/`socket_enabled` fixture yet -- higher-scoped fixtures
    # are set up before lower-scoped ones within the same test, autouse or not. Enable directly
    # rather than depending on that ordering.
    try:
        import pytest_socket
    except ImportError:
        pass
    else:
        pytest_socket.enable_socket()
    with running_broker() as port:
        yield port
