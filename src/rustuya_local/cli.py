"""`rustuya-local run --config config.json`: the bridge-to-IL runner as a daemon, no Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from tuya2ildevice import Hub, IlTopics
from tuya2ildevice.host import DeviceWatcher, MqttTransport, Runner

from .bridge_client import BridgeClient
from .config import Config

log = logging.getLogger(__name__)


async def run(config: Config) -> None:
    # registered first: bridge_client.start() below can block for its whole bootstrap timeout waiting for a bridge
    # that never answers, and a SIGTERM during that wait must still shut down gracefully, not fall through to
    # Python's default (ungraceful) handling because nothing was listening for it yet
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    bridge = MqttTransport(config.bridge.host, config.bridge.port, client_id="rustuya-local-bridge",
                           username=config.bridge.username, password=config.bridge.password)
    await bridge.connect()
    bridge_client = BridgeClient(bridge, config.root)

    # no devices yet: bridge_client hands the Hub the ones the bridge holds (device file and bridge registration)
    hub = Hub([], il=IlTopics(config.prefix, config.source), **config.hub_options)
    will = hub.presence(False)                                           # M-12
    il = MqttTransport(config.il.host, config.il.port, client_id="rustuya-local-il", username=config.il.username,
                       password=config.il.password, will=(will.topic, will.payload, will.qos, will.retain))
    await il.connect()
    runner = Runner(hub, il, on_bridge_command=bridge_client.send_command)
    bridge_client.runner = runner
    await runner.start()
    await bridge_client.start()
    bridge_client.sync_devices(config.devices)
    watcher = None
    if config.devices_path and config.watch_interval > 0:
        watcher = DeviceWatcher(config.devices_path, bridge_client, config.watch_interval)
        watcher.start()
    log.info("%d device(s) in the device file; IL follows the ones registered on %s", len(config.devices), config.root)
    await stop.wait()
    if watcher:
        await watcher.stop()
    await runner.stop()
    for t in (il, bridge):
        await t.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="rustuya-local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the bridge-to-IL runner")
    r.add_argument("--config", required=True)
    r.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    asyncio.run(run(Config.from_file(args.config)))


if __name__ == "__main__":
    main()
