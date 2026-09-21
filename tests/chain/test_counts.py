"""Baseline for the chain (count level). Phase 1 raises it; it must never fall.

`hit[p]` = golden entities of platform p the chain reproduced (by count per fixture); `produced` above golden = extras.
"""
import json
import pathlib

import pytest

from measure import run

BASELINE = json.loads((pathlib.Path(__file__).parent / "baseline.json").read_text())


@pytest.fixture(scope="module")
def result():
    return run()


def test_fixture_set_is_complete(result):
    assert result["fixtures"] == 324  # a moved fixture dir must not turn this into a vacuous pass


def test_no_crashes(result):
    assert result["errors"] == []


def test_golden_total(result):
    assert result["total"] == BASELINE["total"]


@pytest.mark.parametrize("platform", sorted(BASELINE["hit"]))
def test_reproduced_not_below_baseline(result, platform):
    assert result["hit"].get(platform, 0) >= BASELINE["hit"][platform]


@pytest.mark.parametrize("platform", sorted(BASELINE["produced"]))
def test_extras_not_above_baseline(result, platform):
    extra = result["produced"].get(platform, 0) - result["hit"].get(platform, 0)
    base_extra = BASELINE["produced"][platform] - BASELINE["hit"].get(platform, 0)
    assert extra <= base_extra, "chain produces more entities than core (expose_unused?)"
