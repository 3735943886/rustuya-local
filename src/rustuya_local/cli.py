"""`rustuya-local run --config config.json`: the bridge-to-IL runner as a daemon, no Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from tuya2ildevice import BridgeTopics, Hub, IlTopics
from tuya2ildevice.host import DeviceWatcher, MqttTransport, Runner, read_bridge_config

from .config import Config

log = logging.getLogger(__name__)


async def run(config: Config) -> None:
    bridge = MqttTransport(config.bridge.host, config.bridge.port, client_id="rustuya-local-bridge",
                           username=config.bridge.username, password=config.bridge.password)
    await bridge.connect()
    found = await read_bridge_config(bridge, config.root)
    if found is None:
        log.warning("no configuration from the bridge on %s/bridge/config; using the default topic layout", config.root)
    topics = BridgeTopics.from_config(found or {}, config.root)
    root = topics.root
    hub = Hub(config.devices, bridge=topics, il=IlTopics(config.prefix, config.source), **config.hub_options)
    will = hub.presence(False)                                           # M-12
    il = MqttTransport(config.il.host, config.il.port, client_id="rustuya-local-il", username=config.il.username,
                       password=config.il.password, will=(will.topic, will.payload, will.qos, will.retain))
    await il.connect()
    runner = Runner(hub, bridge, il)
    await runner.start()
    watcher = None
    if config.devices_path and config.watch_interval > 0:
        watcher = DeviceWatcher(config.devices_path, runner, config.watch_interval)
        watcher.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("driving %d device(s) via %s", len(config.devices), root)
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
