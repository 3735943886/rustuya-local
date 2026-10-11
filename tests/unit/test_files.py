"""Concurrent replacements and failures leave a complete file and no temporary artifacts."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from rustuya_local import files


def test_concurrent_replacements_keep_a_complete_document(tmp_path):
    path = tmp_path / 'settings.json'
    documents = [json.dumps({'writer': i, 'value': '한글' * 1000}) for i in range(20)]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda data: files.atomic_write(path, data), documents))
    assert path.read_text('utf-8') in documents
    assert list(tmp_path.iterdir()) == [path]


def test_failed_replacement_keeps_the_original_and_removes_the_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    path.write_text('original')

    def fail(*args):
        raise OSError('cannot replace')

    monkeypatch.setattr(files.os, 'replace', fail)
    with pytest.raises(OSError, match='cannot replace'):
        files.atomic_write(path, 'replacement')
    assert path.read_text() == 'original'
    assert list(tmp_path.iterdir()) == [path]
