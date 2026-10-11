"""The host's rule directory is the engine's rule format: the filename order and Rust validation over decoded layers."""
import copy
import json

from tuya2ildevice import Connected, TuyaDriver, merge_rules

from rustuya_local.host import Hub, OverrideWatcher, load_overrides
from rustuya_local.host.rules import load_rules, rules

DEVICE = {'id': 'd', 'product_id': 'p', 'category': 'kg',
          'function': {'switch_1': {'type': 'Boolean', 'values': {}}}}


def write(path, value):
    path.write_text(json.dumps(value))


def test_host_files_are_rule_layers_and_product_fixes_live_under_overrides(tmp_path):
    write(tmp_path / '10_bare.json', {'p': {'device': {'label': 'bare'}}})
    write(tmp_path / '20_rules.json', {'overrides': {'p': {'device': {'label': 'unified'}}}})
    loaded = load_overrides(tmp_path)
    assert [w.split(':')[0] for w in loaded.warnings] == ['10_bare.json']     # not a rule object: left out
    assert loaded.overrides == loaded.rules['overrides']
    assert TuyaDriver(DEVICE, rules=loaded.rules).descriptor['label'] == 'unified'




def test_watcher_reloads_global_rules_and_restores_defaults_after_removal(tmp_path):
    class Runner:
        def __init__(self):
            self.hub = Hub([DEVICE])

        def reload(self, overrides, *, rules):
            self.hub.reload(overrides, rules=rules)

    runner = Runner()
    watcher = OverrideWatcher(tmp_path, runner)
    path = tmp_path / '10_rules.json'
    layout = copy.deepcopy(rules()['entities']['switch']['layout'])
    layout[0]['definition']['label'] = 'User switch'
    write(path, {'entities': {'switch': {'layout': layout}}})
    assert watcher.check()
    assert runner.hub.drivers['d'].descriptor['props']['switch_1']['label'] == 'User switch'
    path.unlink()
    assert watcher.check()
    assert runner.hub.drivers['d'].descriptor['props']['switch_1'].get('label') != 'User switch'




def test_host_uses_global_filename_order_for_rule_layers(tmp_path):
    write(tmp_path / '000_before_base.json', {'maps': {'switch_on': {'probe': True}}})
    write(tmp_path / '10_after_base.json', {'overrides': {'p': {'device': {'label': 'user'}}}})
    loaded = load_overrides(tmp_path)
    assert not loaded.warnings
    assert loaded.rules == load_rules([tmp_path])


def test_keys_an_older_version_saved_are_upgraded_not_the_file_dropped(tmp_path):
    write(tmp_path / 'zz_settings.json', {'overrides': {
        'a': {'cover': {'infer_motion': True, 'invert_position': True}},
        'b': {'cover': {'infer_motion': False, 'control_2': {'infer_motion': True}}},
        'c': {'cover': {'infer_motion': True, 'state_source': 'control'}},
        'd': {'converters': {'cover_motion': {'settle': 5, 'invert': True}}},
    }})
    loaded = load_overrides(tmp_path)
    assert not loaded.warnings
    assert loaded.overrides == {
        'a': {'cover': {'state_source': 'inferred', 'invert_position': True}},
        'b': {'cover': {'state_source': 'none', 'control_2': {'state_source': 'inferred'}}},
        'c': {'cover': {'state_source': 'control'}},
        'd': {'cover': {'state_source': 'inferred', 'settle': 5}},
    }


def test_inline_overrides_with_older_keys_are_upgraded():
    hub = Hub([DEVICE], overrides={'p': {'converters': {'cover_motion': {}}}})
    assert hub._kw['overrides'] == {'p': {'cover': {'state_source': 'inferred'}}}


def test_a_rule_pack_the_hub_moves_off_is_released_once_no_hub_uses_it():
    from tuya2ildevice.native import unregister_rules

    own, other = labelled('only this test'), labelled('Other')
    first, second = Hub([dict(DEVICE)], rules=own), Hub([dict(DEVICE, id='e')], rules=own)
    old = first._rules_id
    first.reload(None, rules=other)
    assert first._rules_id != old and first.drivers['d'].rules_id == first._rules_id
    second.drivers['e'].handle(0, Connected())                              # still registered: `second` uses it
    second.reload(None, rules=other)
    assert not unregister_rules(old)                                        # released with its last user


def labelled(label):
    return merge_rules([{'overrides': {'p': {'device': {'label': label}}}}], base=rules())['rules']
