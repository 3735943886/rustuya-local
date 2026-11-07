"""IL image entities, created by the ildevice hub."""

from .platform import platform_setup

async_setup_entry = platform_setup(__name__)
