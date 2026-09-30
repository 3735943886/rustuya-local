#!/usr/bin/env python3
"""Print a version's CHANGELOG.md section: the body of that version's GitHub Release.

    python scripts/release_notes.py VERSION [--prerelease] [--changelog CHANGELOG.md]

A section is everything under `## [VERSION] ...` up to the next `## ` heading. A stable version without a non-empty
section is an error (exit 1), so the release workflow stops before publishing to PyPI. A pre-release (TestPyPI) falls
back to `## [Unreleased]`, where its changes are written until the stable version gets its section.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def section(text: str, version: str) -> str | None:
    """The body under `## [version]`, stripped; None if there is no such heading."""
    heading = re.compile(rf"^## \[{re.escape(version)}\]", re.MULTILINE)
    match = heading.search(text)
    if match is None:
        return None
    eol = text.find("\n", match.end())
    start = len(text) if eol < 0 else eol + 1
    following = re.compile(r"^## ", re.MULTILINE).search(text, start)
    return text[start:following.start() if following else len(text)].strip()


def notes(text: str, version: str, prerelease: bool = False) -> str:
    body = section(text, version)
    if not body and prerelease:
        body = section(text, "Unreleased")
    if not body:
        raise LookupError(f"CHANGELOG.md has no entry for {version}: add a `## [{version}] — <date>` section")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--changelog", type=Path, default=REPO_ROOT / "CHANGELOG.md")
    args = parser.parse_args(argv)
    try:
        print(notes(args.changelog.read_text(encoding="utf-8"), args.version, args.prerelease))
    except LookupError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
