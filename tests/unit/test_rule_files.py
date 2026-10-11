"""The host's rule directory is the engine's rule format: the filename order and Rust validation over decoded layers."""
import copy
import json

from tuya2ildevice import TuyaDriver

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
