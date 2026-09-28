"""`rustuya-local run --config config.json`: the bridge-to-IL runner as a daemon, no Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from .config import Config
from .service import AnotherProducer, Service, purge_il

log = logging.getLogger(__name__)


def service_for(config: Config) -> Service:
    return Service(config.settings(),
                   connect_bridge=lambda: config.bridge.connect("rustuya-local-bridge"),
                   connect_il=lambda will: config.il.connect("rustuya-local-il", will))


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


async def purge(config: Config) -> None:
    """Take this daemon's devices out of IL for good (run it after stopping the daemon, when retiring it): the retained
    descriptors, values and presence of its IL prefix and source are cleared. The bridge is not touched."""
    t = await config.il.connect("rustuya-local-purge")
    try:
        ids = await purge_il(t, config.prefix, config.source)
    finally:
        await t.close()
    print(f"took {len(ids)} device(s) of {config.prefix}/_producer/{config.source} out of IL" +
          (f": {', '.join(ids)}" if ids else ""))


COMMANDS = {
    "run": (run, "run the bridge-to-IL runner"),
    "purge": (purge, "clear this producer's retained IL devices (after stopping it for good)"),
}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="rustuya-local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, (_, help_text) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", required=True)
        p.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    try:
        asyncio.run(COMMANDS[args.cmd][0](Config.from_file(args.config)))
    except AnotherProducer as e:
        log.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
