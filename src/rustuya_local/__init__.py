"""rustuya-local: HA-independent core that connects rustuya-bridge to IL consumers (il-ha)."""

import sys
from pathlib import Path

# The rustuya-manager drop-in zip (scripts/build_dropin.py) carries tuya2ildevice here, since a dropped-in plugin cannot
# pip-install. Appended, not prepended: an installed tuya2ildevice wins.
_VENDOR = Path(__file__).parent / "_vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.append(str(_VENDOR))


def register(ctx):
    """rustuya-manager's drop-in plugin entry (a dropped-in package is loaded by its top-level `register`)."""
    from .manager_plugin import register as _register

    _register(ctx)
