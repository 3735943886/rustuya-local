"""Property-level parity of the chain with Home Assistant core's tuya entities (golden.json), per entity:
found by dp code in the IL descriptor, then platform / class / category / unit compared.

Every remaining difference is a documented IL-level decision, listed here so a new one fails the test:
  * camera            — a stream is outside the IL (il-rationale).
  * climate           — IL `climate` requires `target_temperature`; core makes a climate without one.
  * humidifier        — IL `humidifier` requires `target_humidity`; core makes one from a bare switch. The dps stay
                        available as switch/select/sensor.
  * windspeed unit    — Home Assistant converts to the display unit on the host side; not a producer concern.
"""
import pytest

from props import compare


@pytest.fixture(scope="module")
def result():
    return compare()


CLIMATE_WITHOUT_TARGET = {"qn_5ls2jw49hpczwqng", "rs_d7woucobqi8ncacf", "wk_ccpwojhalfxryigz"}
HUMIDIFIER_WITHOUT_TARGET = {"cs_biflejkeshx1sqig", "cs_eguoms25tkxtf5u8", "jsq_r492ifwk6f2ssptb"}


def test_entities_found(result):
    assert result["tally"]["ok_platform"] == 1190


def test_missing_only_camera_and_targetless_climate(result):
    missing = result["rows"]["missing"]
    assert {(c, p) for c, p, _ in missing if p != "camera"} == {(c, "climate") for c in CLIMATE_WITHOUT_TARGET}
    assert sum(1 for _, p, _ in missing if p == "camera") == 9


def test_platform_differences_are_targetless_humidifiers(result):
    assert {r[0] for r in result["rows"]["platform"]} == HUMIDIFIER_WITHOUT_TARGET
    assert all(r[2] == "humidifier" for r in result["rows"]["platform"])


def test_class_and_category_match(result):
    assert result["rows"]["class"] == [] and result["rows"]["category"] == []


def test_only_unit_difference_is_host_conversion(result):
    assert [r[:2] for r in result["rows"]["unit"]] == [("qxj_fsea1lat3vuktbt6", "windspeed_avg")]
