# -*- coding: utf-8 -*-
"""eval_history 落盘契约：追加不覆盖 / --no-record 逃生 / 退出码不变。"""

from __future__ import annotations

import json
import sys

import pytest
from src.scripts import rag_eval_history
from src.scripts.rag_eval_history import append_eval_history


def _history_lines(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_append_does_not_overwrite_previous_records(tmp_path, monkeypatch):
    history = tmp_path / "eval_history.jsonl"
    monkeypatch.setattr(rag_eval_history, "HISTORY_PATH", history)

    append_eval_history("eval_rule_faqs", {"hit_rate": 0.95})
    append_eval_history("eval_rule_faqs", {"hit_rate": 0.97})

    entries = _history_lines(history)
    assert len(entries) == 2
    assert entries[0]["hit_rate"] == 0.95 and entries[1]["hit_rate"] == 0.97
    assert all(e["script"] == "eval_rule_faqs" and "ts" in e for e in entries)


def test_append_record_false_writes_nothing(tmp_path, monkeypatch):
    history = tmp_path / "eval_history.jsonl"
    monkeypatch.setattr(rag_eval_history, "HISTORY_PATH", history)

    append_eval_history("eval_rule_faqs", {"hit_rate": 0.95}, record=False)

    assert not history.exists()


def test_append_failure_does_not_raise(tmp_path, monkeypatch, caplog):
    """历史目标不可写（路径是目录）等 OSError 只告警，不拖垮评估。"""
    monkeypatch.setattr(rag_eval_history, "HISTORY_PATH", tmp_path)  # 目录：open 即失败

    with caplog.at_level("WARNING", logger="src.scripts.rag_eval_history"):
        append_eval_history("eval_rule_faqs", {"hit_rate": 0.95})

    assert "eval 历史追加失败" in caplog.text


# ---------------------------------------------------------------
# eval_rule_faqs.main：--no-record 与退出码契约（周更步骤 7 依赖）
# ---------------------------------------------------------------

class _FakeEvalRetriever:
    """run_eval 只需要 _vector_search；blocks 供历史记录字段使用。"""

    def __init__(self, hit):
        self._hit = hit
        self.blocks = [("faq_001", "文本", {"kind": "faq"})]

    def _vector_search(self, query, where=None, n=30):
        bid = "faq_001" if self._hit else "faq_999"
        return [{"block_id": bid, "text": "t", "metadata": {"kind": "faq"},
                 "score": 0.9, "source": "vector"}]


def _run_eval_main(monkeypatch, tmp_path, hit, argv):
    """跑 eval_rule_faqs.main 并返回 (history_path, exit_code)。"""
    dataset = tmp_path / "ds.json"
    dataset.write_text(json.dumps(
        {"version": "test", "k": 5,
         "items": [{"question": "q?", "expected": "faq_001"}]}), encoding="utf-8")
    history = tmp_path / "eval_history.jsonl"
    monkeypatch.setattr(rag_eval_history, "HISTORY_PATH", history)
    import src.rag.retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "Retriever",
                        lambda: _FakeEvalRetriever(hit))
    monkeypatch.setattr(sys, "argv", ["eval_rule_faqs", "--dataset",
                                      str(dataset), *argv])
    import src.scripts.eval_rule_faqs as eval_script

    with pytest.raises(SystemExit) as exc:
        eval_script.main()
    return history, exc.value.code


def test_eval_rule_faqs_exit_zero_and_records_on_pass(monkeypatch, tmp_path):
    history, code = _run_eval_main(monkeypatch, tmp_path, hit=True, argv=[])
    assert code == 0

    entries = _history_lines(history)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["passed"] == 1 and entry["hit_rate"] == 1.0
    assert entry["corpus_blocks"] == 1


def test_eval_rule_faqs_exit_one_on_miss(monkeypatch, tmp_path):
    _, code = _run_eval_main(monkeypatch, tmp_path, hit=False, argv=[])
    assert code == 1


def test_eval_rule_faqs_no_record_writes_no_history(monkeypatch, tmp_path):
    history, code = _run_eval_main(monkeypatch, tmp_path, hit=True, argv=["--no-record"])
    assert code == 0

    assert not history.exists()
