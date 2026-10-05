# -*- coding: utf-8 -*-
"""maintain_rag 指纹与变更检测测试。

背景：task_defs 中「武将攻略语料」的 sources 是目录 data/raw_guides/jinxia/guides/，
原 file_fingerprint 直接 open() 导致 Windows 上 PermissionError（--force 模式下
规划阶段也调用 task_changed，任何任务都不会执行）。
"""
import builtins
import sys
from pathlib import Path

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


class TestMainLoop:
    """main() 执行循环回归测试。

    回归背景：c88bf63 加台账时把 `if v_ok` 的 else 误缩进成 for-else——
    全部成功也会把最后一个任务记为"块数校验未通过"，而真实的块数校验
    失败反而不进 failed（且不写台账）。两用例分别命中这两种错误形态。
    """

    @staticmethod
    def _patch_main_dependencies(monkeypatch, *, run_script, verify_outputs):
        monkeypatch.setattr(maintain_rag, 'TASKS', [
            {'name': '任务A', 'script': 'a.py', 'sources': ['a'], 'outputs': ['a.out'], 'expected': None},
            {'name': '任务B', 'script': 'b.py', 'sources': ['b'], 'outputs': ['b.out'], 'expected': None},
        ])
        monkeypatch.setattr(maintain_rag, 'task_changed', lambda task, state: (True, '测试'))
        monkeypatch.setattr(maintain_rag, 'run_script', run_script)
        monkeypatch.setattr(maintain_rag, 'verify_outputs', verify_outputs)
        monkeypatch.setattr(maintain_rag, 'load_state', lambda: {'files': {}})
        monkeypatch.setattr(maintain_rag, 'save_state', lambda state: None)
        monkeypatch.setattr(maintain_rag, 'update_state_fingerprints', lambda *a, **k: None)
        monkeypatch.setattr(maintain_rag, 'install_crash_logger', lambda *a, **k: None)
        monkeypatch.setattr(maintain_rag, 'summarize_counts', lambda: None)
        monkeypatch.setattr(maintain_rag.rag_audit, 'audit_hero_coverage', lambda root: [])
        monkeypatch.setattr(maintain_rag.rag_audit, 'audit_version_timeline', lambda root: [])
        monkeypatch.setattr(maintain_rag.audit_rule_doc, 'audit', lambda **k: [])
        monkeypatch.setattr(sys, 'argv', ['maintain_rag'])
        # record_task 在 main() 内经 `from ... import record_task` 局部导入，
        # 必须打在源模块上才会被拿到
        calls: list = []
        monkeypatch.setattr(
            'src.business.common.task_ledger.record_task',
            lambda task, **kw: calls.append((task, kw)),
        )
        return calls

    def test_all_success_records_no_failure(self, fake_root, monkeypatch, capsys):
        """全成功：不得出现任何 ok=False 台账，终局汇总 ok=True。"""
        calls = self._patch_main_dependencies(
            monkeypatch,
            run_script=lambda script, timeout=180: (True, ''),
            verify_outputs=lambda task: (True, []),
        )
        maintain_rag.main()
        assert [task for task, kw in calls if kw.get('ok') is False] == []
        final = [kw for task, kw in calls if task == 'maintain_rag'][-1]
        assert final['ok'] is True and final['failed'] == 0
        assert '全部任务执行成功' in capsys.readouterr().out

    def test_verify_failure_recorded_as_failed(self, fake_root, monkeypatch, capsys):
        """块数校验失败：任务进 failed、写 ok=False 台账，终局汇总携带原因。"""
        calls = self._patch_main_dependencies(
            monkeypatch,
            run_script=lambda script, timeout=180: (True, ''),
            verify_outputs=lambda task: (False, ['块数 3 != 5']),
        )
        maintain_rag.main()
        failed_tasks = [task for task, kw in calls if kw.get('ok') is False]
        assert failed_tasks == ['maintain_rag:任务A', 'maintain_rag:任务B', 'maintain_rag']
        final = [kw for task, kw in calls if task == 'maintain_rag'][-1]
        assert final['ok'] is False and final['failed'] == 2
        out = capsys.readouterr().out
        assert '块数校验未通过' in out and '任务A' in final['reason']


class TestVerifyOutputsSnapshot:
    """expected='snapshot' 的快照基线校验（武将语料 2026-10 切换，元规则先例）。"""

    BASELINE = {'武将RAG语料.json': 647}

    @pytest.fixture
    def docs_dir(self, fake_root, monkeypatch):
        docs = fake_root / 'rag_corpus'
        docs.mkdir()
        monkeypatch.setattr(maintain_rag, 'DOCS_DIR', str(docs))
        return docs

    @staticmethod
    def _task():
        return {'name': '武将语料', 'outputs': ['武将RAG语料.json'], 'expected': 'snapshot'}

    def test_first_run_without_baseline_passes(self, docs_dir, monkeypatch):
        """首次运行快照未建立：只报不拦（首次成功后由 _record_corpus_baseline 建基线）。"""
        (docs_dir / '武将RAG语料.json').write_text('[1, 2, 3]', encoding='utf-8')
        monkeypatch.setattr(maintain_rag.audit_rule_doc, 'snapshot_counts', lambda: {})

        ok, details = maintain_rag.verify_outputs(self._task())

        assert ok is True
        assert '快照未建立' in details[0]

    def test_growth_over_baseline_passes(self, docs_dir, monkeypatch):
        """加将增长（只增）：通过。"""
        (docs_dir / '武将RAG语料.json').write_text('[1, 2, 3, 4]', encoding='utf-8')
        monkeypatch.setattr(maintain_rag.audit_rule_doc, 'snapshot_counts',
                            lambda: {'武将RAG语料.json': 3})

        ok, details = maintain_rag.verify_outputs(self._task())

        assert ok is True
        assert '只增允许' in details[0]

    def test_drop_below_baseline_fails(self, docs_dir, monkeypatch):
        """低于基线：拦截（疑似丢块）。"""
        (docs_dir / '武将RAG语料.json').write_text('[1]', encoding='utf-8')
        monkeypatch.setattr(maintain_rag.audit_rule_doc, 'snapshot_counts', lambda: self.BASELINE)

        ok, details = maintain_rag.verify_outputs(self._task())

        assert ok is False
        assert '疑似丢块' in details[0]


class TestRecordCorpusBaseline:
    """snapshot 任务成功后的基线写入（快照 corpus_counts 段）。"""

    TASK = {'name': '武将语料', 'outputs': ['武将RAG语料.json'], 'expected': 'snapshot'}

    @pytest.fixture
    def snap_path(self, fake_root, monkeypatch):
        docs = fake_root / 'rag_corpus'
        docs.mkdir()
        monkeypatch.setattr(maintain_rag, 'DOCS_DIR', str(docs))
        path = fake_root / 'snap.json'
        monkeypatch.setattr(maintain_rag.audit_rule_doc, 'DEFAULT_SNAPSHOT', str(path))
        return path

    def test_writes_corpus_counts_preserving_other_keys(self, snap_path):
        Path(maintain_rag.DOCS_DIR, '武将RAG语料.json').write_text('[1, 2, 3]', encoding='utf-8')
        import json as _json
        snap_path.write_text(_json.dumps({'counts': {'sections': 7}, 'chapters': []}),
                             encoding='utf-8')

        maintain_rag._record_corpus_baseline(self.TASK)

        snap = maintain_rag.audit_rule_doc.load_snapshot(str(snap_path))
        assert snap['corpus_counts'] == {'武将RAG语料.json': 3}
        assert snap['counts'] == {'sections': 7}  # 元规则段不被触碰

    def test_missing_output_warns_without_partial_write(self, snap_path, capsys):
        import json as _json
        snap_path.write_text(_json.dumps({'counts': {'sections': 7}}), encoding='utf-8')
        task = {'name': '武将语料', 'outputs': ['不存在.json'], 'expected': 'snapshot'}

        maintain_rag._record_corpus_baseline(task)  # 不应抛异常

        assert '基线更新失败' in capsys.readouterr().out
        snap = maintain_rag.audit_rule_doc.load_snapshot(str(snap_path))
        assert 'corpus_counts' not in snap  # 部分失败不写基线
