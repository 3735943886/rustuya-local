"""Descriptor routing and reserved transport topics."""
import pytest
from tuya_interpreter import Descriptor

from rustuya_local.host.il import IlTopics
from rustuya_local.service import _device_topics, _owned_ids


@pytest.mark.parametrize("prop", ["descriptor", "reject"])
def test_descriptor_refuses_properties_colliding_with_transport_topics(prop):
    with pytest.raises(ValueError, match="reserved IL property"):
        IlTopics().actions("d", [Descriptor({"id": "d", "props": {prop: {"type": "string"}}})])


def test_cleanup_only_recognizes_descriptor_topics_under_a_nested_prefix():
    retained = {
        "home/il/a": '{"source":"tuya"}',
        "home/il/a/descriptor": '{"source":"tuya"}',
        "home/il/a/power": 'true',
        "home/il/old": '{"source":"tuya"}',
        "home/il/b/descriptor": '{"source":"other"}',
        "else/il/a/descriptor": '{"source":"tuya"}',
    }
    assert _owned_ids(retained, "home/il", "tuya") == {"a"}
    assert _device_topics(retained, "home/il", {"a"}) == [
        "home/il/a/power", "home/il/a/descriptor",
    ]
