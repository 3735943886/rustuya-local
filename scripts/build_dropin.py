#!/usr/bin/env python3
"""Build the drop-in zip rustuya-manager's plugin catalog installs.

The manager downloads the zip, checks its SHA-256 against its catalog, unpacks it into its plugin directory and calls
the top-level package's `register(ctx)`. A dropped-in plugin cannot pip-install, so the zip has one top-level entry,
`rustuya_local/`, with tuya2ildevice (pure Python) vendored under `rustuya_local/_vendor/`. paho-mqtt and
pyrustuyabridge are not vendored: the manager depends on both.

The bytes depend only on the file contents (sorted entries, fixed timestamps and modes, no compression), so the same
sources give the same SHA-256.

    python scripts/build_dropin.py [--outdir dist]

tuya2ildevice is taken from the environment the script runs in (install rustuya-local first).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "src" / "rustuya_local"
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
VENDORED = ("tuya2ildevice",)


def _files(root: Path) -> list[Path]:
    return sorted((p for p in root.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}),
                  key=lambda p: p.relative_to(root).as_posix())


def _members() -> list[tuple[str, Path]]:
    out = [(f"rustuya_local/{p.relative_to(PKG_DIR).as_posix()}", p) for p in _files(PKG_DIR)
           if p.relative_to(PKG_DIR).parts[0] != "_vendor"]
    for name in VENDORED:
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.submodule_search_locations:
            sys.exit(f"error: {name} is not installed; install rustuya-local first")
        root = Path(next(iter(spec.submodule_search_locations)))
        out += [(f"rustuya_local/_vendor/{name}/{p.relative_to(root).as_posix()}", p) for p in _files(root)]
    return sorted(out)


def build(outdir: Path) -> Path:
    version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
    outdir.mkdir(parents=True, exist_ok=True)
    artifact = outdir / f"rustuya_local-{version}-dropin.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_STORED) as zf:
        for arcname, path in _members():
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return artifact


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default="dist", type=Path)
    args = ap.parse_args()
    artifact = build(args.outdir if args.outdir.is_absolute() else REPO_ROOT / args.outdir)
    print(f"artifact: {artifact}")
    print(f"vendored: {', '.join(f'{n} {importlib.metadata.version(n)}' for n in VENDORED)}")
    print(f"sha256:   {hashlib.sha256(artifact.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
