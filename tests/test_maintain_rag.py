# -*- coding: utf-8 -*-
"""maintain_rag 指纹与变更检测测试。

背景：task_defs 中「武将攻略语料」的 sources 是目录 data/raw_guides/jinxia/guides/，
原 file_fingerprint 直接 open() 导致 Windows 上 PermissionError（--force 模式下
规划阶段也调用 task_changed，任何任务都不会执行）。
"""
import pytest

from src.scripts import maintain_rag


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """把 maintain_rag.ROOT 指向临时目录，隔离项目真实文件。"""
    monkeypatch.setattr(maintain_rag, 'ROOT', str(tmp_path))
    return tmp_path


def _write_guides(root, files):
    guides = root / 'guides'
    guides.mkdir(exist_ok=True)
    for name, content in files.items():
        (guides / name).write_text(content, encoding='utf-8')
    return guides


class TestFileFingerprint:
    def test_missing_path_returns_none(self, fake_root):
        assert maintain_rag.file_fingerprint('不存在.json') is None

    def test_file_returns_md5_size_mtime(self, fake_root):
        (fake_root / 'a.json').write_text('hello', encoding='utf-8')
        fp = maintain_rag.file_fingerprint('a.json')
        assert fp['md5'] == '5d41402abc4b2a76b9719d911017c592'
        assert fp['size'] == 5
        assert 'mtime' in fp

    def test_directory_source_no_crash(self, fake_root):
        """回归：目录源不应抛 PermissionError（Windows 上 open() 目录必炸）。"""
        _write_guides(fake_root, {'曹操.md': '# 攻略A', '刘备.md': '# 攻略B'})
        fp = maintain_rag.file_fingerprint('guides/')
        assert fp is not None and 'dir_md5' in fp

    def test_directory_fingerprint_deterministic(self, fake_root):
        _write_guides(fake_root, {'a.md': 'x', 'b.md': 'y'})
        first = maintain_rag.file_fingerprint('guides/')
        second = maintain_rag.file_fingerprint('guides/')
        assert first == second

    def test_directory_content_change_detected(self, fake_root):
        _write_guides(fake_root, {'a.md': 'x'})
        before = maintain_rag.file_fingerprint('guides/')
        _write_guides(fake_root, {'a.md': 'x2'})
        after = maintain_rag.file_fingerprint('guides/')
        assert before != after

    def test_directory_file_added_detected(self, fake_root):
        _write_guides(fake_root, {'a.md': 'x'})
        before = maintain_rag.file_fingerprint('guides/')
        _write_guides(fake_root, {'b.md': 'y'})
        after = maintain_rag.file_fingerprint('guides/')
        assert before != after

    def test_directory_file_removed_detected(self, fake_root):
        _write_guides(fake_root, {'a.md': 'x', 'b.md': 'y'})
        before = maintain_rag.file_fingerprint('guides/')
        (fake_root / 'guides' / 'b.md').unlink()
        after = maintain_rag.file_fingerprint('guides/')
        assert before != after

    def test_directory_same_content_same_fingerprint(self, fake_root):
        """文件集合与内容一致时指纹一致（与文件创建顺序无关）。"""
        _write_guides(fake_root, {'a.md': 'x', 'b.md': 'y'})
        first = maintain_rag.file_fingerprint('guides/')
        (fake_root / 'guides2').mkdir()
        (fake_root / 'guides2' / 'b.md').write_text('y', encoding='utf-8')
        (fake_root / 'guides2' / 'a.md').write_text('x', encoding='utf-8')
        second = maintain_rag.file_fingerprint('guides2/')
        assert first == second


class TestTaskChanged:
    def test_directory_source_not_crash_and_reports_changed(self, fake_root):
        """回归：sources 含目录时 task_changed 不应崩溃（原实现在此抛 PermissionError）。"""
        _write_guides(fake_root, {'a.md': 'x'})
        (fake_root / 'heroes.json').write_text('{}', encoding='utf-8')
        task = {
            'name': '武将攻略语料',
            'script': 'build_guide_corpus.py',
            'sources': ['guides/', 'heroes.json'],
            'outputs': ['武将攻略RAG语料.json'],
        }
        changed, reason = maintain_rag.task_changed(task, {})
        assert changed is True
        assert reason is not None

    def test_directory_source_incremental_skip(self, fake_root):
        """指纹已记录且未变时，增量模式应跳过（不误报变更）。"""
        _write_guides(fake_root, {'a.md': 'x'})
        task = {
            'name': '武将攻略语料',
            'script': 'build_guide_corpus.py',
            'sources': ['guides/'],
            'outputs': ['武将攻略RAG语料.json'],
        }
        state = {'files': {'guides/': maintain_rag.file_fingerprint('guides/')}}
        changed, reason = maintain_rag.task_changed(task, state)
        assert changed is False
