"""`rustuya-local run --config config.json`: the bridge-to-IL runner as a daemon, no Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from tuya2ildevice.host import MqttTransport

from .config import Config
from .service import Service

log = logging.getLogger(__name__)


def service_for(config: Config) -> Service:
    async def connect_bridge():
        t = MqttTransport(config.bridge.host, config.bridge.port, client_id="rustuya-local-bridge",
                          username=config.bridge.username, password=config.bridge.password)
        await t.connect()
        return t

    async def connect_il(will):
        t = MqttTransport(config.il.host, config.il.port, client_id="rustuya-local-il", username=config.il.username,
                          password=config.il.password, will=will)
        await t.connect()
        return t

    return Service(config.settings(), connect_bridge=connect_bridge, connect_il=connect_il)


async def run(config: Config) -> None:
    # registered first: the service's start can block for the bridge client's whole bootstrap timeout waiting for a
    # bridge that never answers, and a SIGTERM during that wait must still shut down gracefully, not fall through to
    # Python's default (ungraceful) handling because nothing was listening for it yet
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    service = service_for(config)
    await service.start()
    try:
        await stop.wait()
    finally:
        await service.stop()


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
