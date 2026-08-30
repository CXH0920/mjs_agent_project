# -*- coding: utf-8 -*-
"""maintain_rag 指纹与变更检测测试。

背景：task_defs 中「武将攻略语料」的 sources 是目录 data/raw_guides/jinxia/guides/，
原 file_fingerprint 直接 open() 导致 Windows 上 PermissionError（--force 模式下
规划阶段也调用 task_changed，任何任务都不会执行）。
"""
import builtins

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

    def test_directory_unreadable_file_skipped_not_crash(self, fake_root, monkeypatch):
        """回归：目录内出现不可读文件（文件锁/断链竞态）时跳过而非整个流程崩溃。"""
        _write_guides(fake_root, {'a.md': 'x', 'b.md': 'y'})
        normal = maintain_rag.file_fingerprint('guides/')
        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith('b.md'):
                raise PermissionError(13, 'Access is denied')
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, 'open', fake_open)
        fp = maintain_rag.file_fingerprint('guides/')
        assert fp is not None and 'dir_md5' in fp
        assert fp != normal  # 跳过文件会改变指纹，下次增量重跑该任务（方向安全）


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


class TestUpdateStateFingerprints:
    TASK_CARD = {
        'name': '卡牌语料',
        'script': 'build_card_corpus.py',
        'sources': ['heroes.json', 'cards.json'],
        'outputs': [],
    }
    TASK_MODIFY = {
        'name': '加强削弱语料',
        'script': 'build_modify_corpus.py',
        'sources': ['cards.json'],
        'outputs': [],
    }
    FINGERPRINTS = {
        'heroes.json': {'md5': 'h'},
        'cards.json': {'md5': 'new-cards'},
        'src/scripts/build_card_corpus.py': {'md5': 's1'},
        'src/scripts/build_modify_corpus.py': {'md5': 's2'},
    }

    def test_failed_task_shared_source_fingerprint_frozen(self, fake_root, monkeypatch):
        """回归：共享源变更后一任务失败，成功任务不得把共享源新指纹写入 state，
        否则下次增量会永久跳过失败任务，坏语料驻留。"""
        monkeypatch.setattr(maintain_rag, 'file_fingerprint', lambda p: self.FINGERPRINTS.get(p))
        plan = [(self.TASK_CARD, 'changed'), (self.TASK_MODIFY, 'changed')]
        old_card_fp = {'md5': 'old-cards'}
        state = {'files': {'cards.json': old_card_fp}}

        maintain_rag.update_state_fingerprints(plan, ['卡牌语料'], force=False, state=state)

        assert state['files']['cards.json'] == old_card_fp  # 共享源指纹保持旧值
        assert 'src/scripts/build_card_corpus.py' not in state['files']  # 失败任务自身路径不记录
        assert state['files']['src/scripts/build_modify_corpus.py'] == {'md5': 's2'}

    def test_force_mode_records_failed_task_paths(self, fake_root, monkeypatch):
        """--force 视为已处理：失败任务的路径也记录新指纹。"""
        monkeypatch.setattr(maintain_rag, 'file_fingerprint', lambda p: self.FINGERPRINTS.get(p))
        plan = [(self.TASK_CARD, 'changed')]
        state = {'files': {}}

        maintain_rag.update_state_fingerprints(plan, ['卡牌语料'], force=True, state=state)

        assert state['files']['cards.json'] == {'md5': 'new-cards'}
        assert state['files']['src/scripts/build_card_corpus.py'] == {'md5': 's1'}

    def test_all_success_records_everything(self, fake_root, monkeypatch):
        """全成功时所有路径记录新指纹（原有行为不回退）。"""
        monkeypatch.setattr(maintain_rag, 'file_fingerprint', lambda p: self.FINGERPRINTS.get(p))
        plan = [(self.TASK_CARD, 'changed'), (self.TASK_MODIFY, 'changed')]
        state = {'files': {}}

        maintain_rag.update_state_fingerprints(plan, [], force=False, state=state)

        assert state['files'] == self.FINGERPRINTS
