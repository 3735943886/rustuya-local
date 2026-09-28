"""`hazardous()`: what `Service.send_dps` refuses while `allow_hazardous` is off."""

from rustuya_local.service import hazardous


def test_by_tuya_category():
    for category in ("ms", "jtmspro", "videolock", "mal", "ckmkzq"):
        assert hazardous({"category": category}, {"props": {}})
    assert not hazardous({"category": "dj"}, {"kind": "light", "props": {}})


def test_by_what_the_hub_made_of_it():
    assert hazardous({"category": "x"}, {"kind": "alarm", "props": {}})
    assert hazardous({"category": "x"}, {"kind": "cover", "class": "garage", "props": {}})
    assert hazardous({"category": "x"}, {"kind": "switch", "groups": {"door": {"kind": "cover", "class": "gate"}}})
    assert not hazardous({"category": "cl"}, {"kind": "cover", "class": "curtain", "props": {}})
    assert not hazardous({}, {"kind": "cover", "props": {}})
