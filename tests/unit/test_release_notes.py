"""A release's GitHub Release notes are its CHANGELOG.md section; a stable version without one is not published."""
import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
sys.modules["release_notes"] = release_notes
spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## [Unreleased]

### Added
- next thing

## [0.0.10] — 2026-10-01

### Fixed
- ten

## [0.0.1] — 2026-09-23

- one
"""


def test_the_section_up_to_the_next_version():
    assert release_notes.notes(CHANGELOG, "0.0.10") == "### Fixed\n- ten"
    assert release_notes.notes(CHANGELOG, "0.0.1") == "- one"                 # the last section runs to the end


def test_a_version_is_not_a_prefix_match():
    with pytest.raises(LookupError):
        release_notes.notes(CHANGELOG, "0.0.")
    assert release_notes.section(CHANGELOG, "0.0.1") == "- one"               # not 0.0.10's


def test_a_stable_version_without_a_section_is_refused():
    with pytest.raises(LookupError):
        release_notes.notes(CHANGELOG, "0.0.11")
    with pytest.raises(LookupError):
        release_notes.notes("## [0.0.11] — 2026-10-02\n\n## [0.0.10]\n- ten\n", "0.0.11")   # an empty section too


def test_a_prerelease_falls_back_to_unreleased():
    assert release_notes.notes(CHANGELOG, "0.0.11rc1", prerelease=True) == "### Added\n- next thing"


def test_the_current_version_has_notes():
    """Bumping the version without a CHANGELOG entry fails here, before a tag is pushed."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert release_notes.notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
