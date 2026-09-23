"""Keys and defaults shared by config_flow.py and __init__.py."""

from __future__ import annotations

DOMAIN = "rustuya"

# --- entry data (set at setup, changed only through a reconfigure flow) ----------------
CONF_BRIDGE_MODE = "bridge_mode"
BRIDGE_EXTERNAL = "external"          # an already-running rustuya-bridge
BRIDGE_EMBEDDED = "embedded"          # this integration spawns pyrustuyabridge itself

CONF_BROKER_HOST = "broker_host"
CONF_BROKER_PORT = "broker_port"
CONF_BROKER_USERNAME = "broker_username"
CONF_BROKER_PASSWORD = "broker_password"
CONF_BRIDGE_ROOT = "bridge_root"

CONF_BRIDGE_STATE_FILE = "bridge_state_file"    # embedded only
CONF_BRIDGE_LOG_LEVEL = "bridge_log_level"      # embedded only

CONF_IL_PREFIX = "il_prefix"
CONF_IL_SOURCE = "il_source"

CONF_DEVICES_PATH = "devices_path"              # tuyadevices.json; the cloud wizard (config/options flow) writes
                                                 # it through rustuya-manager's Manager, the always-on Runner/Hub
                                                 # side only ever reads it

# --- entry options (changed any time through the options flow) -------------------------
CONF_WATCH_INTERVAL = "watch_interval"
CONF_ALLOW_HAZARDOUS = "allow_hazardous"
CONF_EXPOSE_UNUSED = "expose_unused"

DEFAULT_BROKER_PORT = 1883
DEFAULT_BRIDGE_ROOT = "rustuya"
DEFAULT_IL_PREFIX = "il"
DEFAULT_IL_SOURCE = "tuya"
DEFAULT_DEVICES_FILE = ".storage/rustuya_tuyadevices.json"     # not user-facing config -- HA's own storage area
DEFAULT_BRIDGE_STATE_FILE = ".storage/rustuya_bridge_state.json"
DEFAULT_WATCH_INTERVAL = 5
