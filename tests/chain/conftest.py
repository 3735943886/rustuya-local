"""Chain harness: HA-core tuya fixtures -> tuya2ildevice descriptor -> il-ha core plan, compared with core's golden.

The golden data and fixture loader live in the sibling tuya2ildevice checkout (tests/golden).
il-ha's HA-free `il_ha.core` is a normal dependency (path-installed, see pyproject.toml).
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
GOLDEN = pathlib.Path(os.environ.get("TUYA2IL_GOLDEN", ROOT / "tuya2ildevice/tests/golden"))

if str(GOLDEN) not in sys.path:
    sys.path.insert(0, str(GOLDEN))
