"""Host-side rule artifact discovery and file loading. The Rust core only composes decoded data."""
from __future__ import annotations

import copy
import functools
import json
import logging
import os
from pathlib import Path

import tuya_interpreter
from tuya_interpreter import merge_rules

_LOGGER = logging.getLogger(__name__)


def _upgrade_cover(cover, nested=True):
    # `infer_motion` (saved by the Infer motion switch before 0.3.16) became `state_source`; an explicit
    # `state_source` wins, as it did. False meant no motion: `none`, which still gives the resting state.
    if 'infer_motion' in cover:
        legacy = cover.pop('infer_motion')
        cover.setdefault('state_source', 'inferred' if legacy is True else 'none')
    if nested:
        for value in cover.values():
            if isinstance(value, dict):
                _upgrade_cover(value, False)


def upgrade_overrides(mapping, where=''):
    """Rewrite override keys tuya2ildevice 0.4.0.dev3 no longer accepts (`cover.infer_motion`,
    `converters.cover_motion`) as their `cover` settings, so a file saved by an older version still loads (the engine
    refuses a file with either, and the settings in it with it). Returns a new mapping; `mapping` is left as it is."""
    if not isinstance(mapping, dict):
        return mapping
    out = copy.deepcopy(mapping)
    for key, block in out.items():
        if not isinstance(block, dict):
            continue
        cover = block.get('cover')
        if isinstance(cover, dict):
            _upgrade_cover(cover)
        converters = block.get('converters')
        if isinstance(converters, dict) and 'cover_motion' in converters:
            motion = converters.pop('cover_motion')
            cover = block.setdefault('cover', {})
            if isinstance(cover, dict):
                cover.setdefault('state_source', 'inferred')
                if isinstance(motion, dict):
                    if 'settle' in motion:
                        cover.setdefault('settle', motion['settle'])
                    if dropped := sorted(set(motion) - {'settle'}):
                        _LOGGER.warning('%soverride %s: converters.cover_motion %s dropped: use its cover settings',
                                        where, key, ', '.join(dropped))
            if not converters:
                del block['converters']
    return out


def _upgrade_layer(data, where):
    if isinstance(data, dict) and isinstance(data.get('overrides'), dict):
        return {**data, 'overrides': upgrade_overrides(data['overrides'], where)}
    return data


def rule_paths(paths=(), *, bundled_path=None):
    package = Path(tuya_interpreter.__file__).resolve().parent
    bundled = package / 'rules' / 'builtin'
    checkout = package.parents[1] / 'rules' / 'builtin'
    location = bundled_path or (bundled if bundled.is_dir() else checkout)
    return [Path(location), *map(Path, paths)]


def rule_files(locations):
    files = []
    for location in map(Path, locations):
        if location.is_file() and location.name != 'manifest.json':
            files.append(location)
        elif location.is_dir():
            files.extend(p for p in location.iterdir()
                         if p.is_file() and p.suffix == '.json' and p.name[0] not in '._'
                         and p.name != 'manifest.json')
    return sorted(files, key=lambda p: os.fsencode(p.name))


def merge_rule_files(locations, *, tolerant=False, base=None):
    layers, warnings = [], []
    files = rule_files(locations)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            layers.append({'source': str(path), 'rules': _upgrade_layer(data, f'{path.name}: ')})
        except (OSError, ValueError) as error:
            if not tolerant:
                raise ValueError(f'{path}: {error}') from error
            warnings.append(f'{path.name}: {error}')
    result = merge_rules(layers, base=base, tolerant=tolerant)
    names = {str(p): p.name for p in files}
    for warning in result['warnings']:
        source, _, message = warning.partition(': ')
        warnings.append(f'{names.get(source, source)}: {message}')
    return {**result, 'warnings': warnings}


def _load_rules(paths=(), *, bundled_path=None, tolerant=False):
    locations = rule_paths(paths, bundled_path=bundled_path)
    official = merge_rule_files(locations[:1])
    # Downloaded official rules refine the bundled pack; user files always run last.
    downloaded = [Path(p) / 'rules' / 'builtin' for p in paths if Path(p).is_dir()]
    official = merge_rule_files(downloaded, base=official['rules'], tolerant=tolerant)
    result = merge_rule_files(locations[1:], base=official['rules'], tolerant=tolerant)
    result['warnings'] = official['warnings'] + result['warnings']
    return result


def load_rules(paths=(), *, bundled_path=None):
    return _load_rules(paths, bundled_path=bundled_path)['rules']


def load_rules_tolerant(paths=(), *, bundled_path=None):
    return _load_rules(paths, bundled_path=bundled_path, tolerant=True)


@functools.cache
def rules():
    paths = [p for p in os.environ.get('TUYA_ENGINE_RULES', '').split(os.pathsep) if p]
    return load_rules(paths)
