"""Host-side rule artifact discovery and file loading. The Rust core only composes decoded data."""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import tuya2ildevice
from tuya2ildevice import merge_rules


def rule_paths(paths=(), *, bundled_path=None):
    package = Path(tuya2ildevice.__file__).resolve().parent
    bundled = package / 'rules'
    checkout = package.parents[1] / 'rules'
    location = bundled_path or (bundled if bundled.is_dir() else checkout)
    return [Path(location), *map(Path, paths)]


def rule_files(locations):
    files = []
    for location in map(Path, locations):
        if location.is_file():
            files.append(location)
        elif location.is_dir():
            files.extend(p for p in location.iterdir() if p.is_file() and p.suffix == '.json'
                         and p.name != 'schema.json' and p.name[0] not in '._')
    return sorted(files, key=lambda p: os.fsencode(p.name))


def merge_rule_files(locations, *, tolerant=False):
    layers, warnings = [], []
    files = rule_files(locations)
    for path in files:
        try:
            layers.append({'source': str(path), 'rules': json.loads(path.read_text(encoding='utf-8'))})
        except (OSError, ValueError) as error:
            if not tolerant:
                raise ValueError(f'{path}: {error}') from error
            warnings.append(f'{path.name}: {error}')
    result = merge_rules(layers, tolerant=tolerant)
    names = {str(p): p.name for p in files}
    for warning in result['warnings']:
        source, _, message = warning.partition(': ')
        warnings.append(f'{names.get(source, source)}: {message}')
    return {**result, 'warnings': warnings}


def load_rules(paths=(), *, bundled_path=None):
    return merge_rule_files(rule_paths(paths, bundled_path=bundled_path))['rules']


def load_rules_tolerant(paths=(), *, bundled_path=None):
    return merge_rule_files(rule_paths(paths, bundled_path=bundled_path), tolerant=True)


@functools.cache
def rules():
    paths = [p for p in os.environ.get('TUYA_ENGINE_RULES', '').split(os.pathsep) if p]
    return load_rules(paths)
