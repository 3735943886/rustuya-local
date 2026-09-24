"""`rustuya-local run --config config.json`: the bridge-to-IL runner as a daemon, no Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

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

    connect_ha = None
    if config.discovery_broker is not None:
        async def connect_ha():
            b = config.discovery_broker
            t = MqttTransport(b.host, b.port, client_id="rustuya-local-ha", username=b.username, password=b.password)
            await t.connect()
            return t

    return Service(config.settings(), connect_bridge=connect_bridge, connect_il=connect_il, connect_ha=connect_ha)


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


async def discovery_command(config: Config, args: argparse.Namespace) -> int:
    """`rustuya-local discovery status|clear|restore`: the retained discovery configs, by hand."""
    from .discovery import ops
    from .discovery.render import Options

    d = config.discovery
    if d is None:
        from .service import Discovery
        d = Discovery()
    options = Options(il_prefix=config.prefix, discovery_prefix=d.prefix, node_id=d.node_id,
                      relay_prefix=d.relay_prefix, source=config.source)
    il = MqttTransport(config.il.host, config.il.port, client_id="rustuya-local-ops-il",
                       username=config.il.username, password=config.il.password)
    await il.connect()
    ha = il
    if config.discovery_broker is not None:
        b = config.discovery_broker
        ha = MqttTransport(b.host, b.port, client_id="rustuya-local-ops-ha", username=b.username, password=b.password)
        await ha.connect()
    try:
        snap = await ops.collect(il, ha, options, args.wait)
        backups = Path(args.backup_dir)
        if args.action == "status":
            st = ops.status(snap, options)
            print(f"{len(snap.descriptors)} IL device(s); configs: {st.summary()}")
            if args.detail:
                for kind in ("missing", "differs", "stale"):
                    for topic in getattr(st, kind):
                        print(f"  {kind:8} {topic}")
            return 0 if not (st.missing or st.differs or st.stale) else 1
        if args.action == "clear":
            plan = ops.clear_plan(snap, options, stale_only=args.stale_only)
            print(f"{len(plan)} config(s) to clear under {options.discovery_prefix}/+/{options.node_id}/")
            if not plan or not args.yes:
                if plan:
                    print("dry run; pass --yes to clear")
                return 0
            if not args.no_backup:
                print(f"backup: {ops.save_backup(backups, snap.configs)}")
            for topic in plan:
                await ha.publish(topic, "", 1, True)
            return 0
        if args.action == "restore":
            path = Path(args.file) if args.file else ops.latest_backup(backups)
            if path is None:
                print(f"no backup in {backups}")
                return 1
            plan = ops.restore_plan(snap, ops.load_backup(path))
            print(f"restore {path}: {sum(1 for _, p in plan if p)} to publish, {sum(1 for _, p in plan if not p)} to clear")
            if not args.yes:
                print("dry run; pass --yes to restore")
                return 0
            if not args.no_backup:
                print(f"backup: {ops.save_backup(backups, snap.configs)}")
            for topic, payload in plan:
                await ha.publish(topic, payload, 1, True)
            return 0
        return 2
    finally:
        await asyncio.sleep(0.5)                       # let paho send what was published
        for t in {id(il): il, id(ha): ha}.values():
            await t.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="rustuya-local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the bridge-to-IL runner")
    r.add_argument("--config", required=True)
    r.add_argument("-v", "--verbose", action="store_true")
    d = sub.add_parser("discovery", help="the retained Home Assistant discovery configs, by hand")
    d.add_argument("action", choices=["status", "clear", "restore"])
    d.add_argument("--config", required=True)
    d.add_argument("--detail", action="store_true", help="status: list each topic that is not ok")
    d.add_argument("--stale-only", action="store_true", help="clear: only configs no device produces")
    d.add_argument("--yes", action="store_true", help="clear/restore: do it (the default is a dry run)")
    d.add_argument("--file", help="restore: this backup (default: the latest)")
    d.add_argument("--backup-dir", default=".rustuya-local-backups")
    d.add_argument("--no-backup", action="store_true")
    d.add_argument("--wait", type=float, default=2.0, help="seconds to collect retained messages")
    d.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    if args.cmd == "run":
        asyncio.run(run(Config.from_file(args.config)))
    else:
        raise SystemExit(asyncio.run(discovery_command(Config.from_file(args.config), args)))


if __name__ == "__main__":
    main()
