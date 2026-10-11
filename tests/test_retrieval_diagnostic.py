# -*- coding: utf-8 -*-
"""检索诊断（business 服务 + 抽屉对话框）测试：纯 dict 输出、trace 透传、表格渲染。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from src.business.rag import retrieval_diagnostic as diag_module
from src.business.rag.retrieval_diagnostic import diagnose
from src.ui.maintenance.retrieval_diagnostic_dialog import (
    DROP_LABELS,
    RetrievalDiagnosticDialog,
)


class _FakeRetriever:
    """固定检索结果的替身：保留 1 块 + 各类丢弃记录。"""

    def __init__(self):
        self.last_query = None
        self.last_kwargs = None

    def search(self, query, heroes=None, top_k=None, trace=None):
        self.last_query = query
        self.last_kwargs = {"heroes": heroes, "top_k": top_k}
        trace.append({
            "block_id": "hero_甲_1", "kind": "hero", "hero": "甲",
            "score": 0.8123, "source": "vector", "rrf": 0.01639,
            "is_current": "true", "as_of": "2026-08-28", "dropped": None,
        })
        trace.append({
            "block_id": "faq_005", "kind": "faq", "hero": "",
            "score": 0.11, "source": "vector", "rrf": 0.0,
            "is_current": "true", "as_of": "", "dropped": "below_threshold",
        })
        return [{
            "block_id": "hero_甲_1", "text": "【武将技能】甲……",
            "metadata": {"kind": "hero", "hero": "甲", "is_current": "true",
                         "as_of": "2026-08-28"},
            "score": 0.8123, "source": "vector", "rrf": 0.01639,
        }]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_diagnose_returns_pure_dict(monkeypatch):
    fake = _FakeRetriever()
    monkeypatch.setattr("src.rag.retriever.Retriever", lambda: fake)

    result = diagnose("出牌阶段摸牌", top_k=8, heroes=["甲"])

    assert fake.last_query == "出牌阶段摸牌"
    assert fake.last_kwargs == {"heroes": ["甲"], "top_k": 8}
    assert result["query"] == "出牌阶段摸牌"
    assert result["heroes"] == ["甲"] and result["top_k"] == 8
    assert result["elapsed_ms"] >= 0
    assert isinstance(result["trace"], list) and len(result["trace"]) == 2
    # items 行字段齐全且 dropped=None；文本截断为可展示长度
    item = result["items"][0]
    assert item["block_id"] == "hero_甲_1" and item["dropped"] is None
    assert item["kind"] == "hero" and item["rrf"] == 0.01639
    assert set(item) == {"block_id", "kind", "hero", "score", "source",
                         "rrf", "is_current", "as_of", "dropped", "text"}
    # backend 字段来自 config（真实读取，非 monkeypatch）
    assert result["backend"] in ("onnx", "st")


def test_dialog_renders_trace_with_drop_labels(monkeypatch):
    _app()
    dialog = RetrievalDiagnosticDialog()
    fake = _FakeRetriever()
    monkeypatch.setattr("src.rag.retriever.Retriever", lambda: fake)

    dialog.query_edit.setText("出牌阶段摸牌")
    dialog._on_result(diagnose("出牌阶段摸牌", top_k=8))

    assert dialog.table.rowCount() == 2
    kept_row = 0
    dropped_row = 1
    assert dialog.table.item(kept_row, 0).text() == "hero_甲_1"
    assert dialog.table.item(kept_row, 8).text() == DROP_LABELS[None]
    assert dialog.table.item(dropped_row, 0).text() == "faq_005"
    assert dialog.table.item(dropped_row, 8).text() == DROP_LABELS["below_threshold"]
    assert "保留 1 条 / 丢弃 1 条" in dialog.status_label.text()
    assert "backend=" in dialog.status_label.text()


def test_dialog_query_with_empty_text_is_noop(monkeypatch):
    _app()
    dialog = RetrievalDiagnosticDialog()
    started = []
    monkeypatch.setattr(
        diag_module.DiagnosticWorker, "start",
        lambda self: started.append(self))

    dialog._run_query()  # 空查询

    assert started == []
    assert dialog.status_label.text()


def test_dialog_worker_failure_shows_message():
    _app()
    dialog = RetrievalDiagnosticDialog()

    dialog._on_failed("boom")

    assert "boom" in dialog.status_label.text()


def test_drop_labels_cover_all_trace_reasons():
    """表格剔除原因映射必须覆盖 retriever 插桩的全部枚举，新增丢弃点须同步。"""
    from src.rag.retriever import _trace_append

    reasons = set()
    entry = {"block_id": "x", "metadata": {}}
    probe = []
    # 从插桩实现反查全部原因（below_threshold 等 5 种）
    for reason in ("below_threshold", "not_current", "hero_filter",
                   "max_keyword_only", "kind_quota", None):
        _trace_append(probe, entry, reason)
    reasons = {e["dropped"] for e in probe}
    assert reasons <= set(DROP_LABELS)


def _trace_entry(bid, kind, hero, score, rrf, dropped):
    return {"block_id": bid, "kind": kind, "hero": hero, "score": score,
            "source": "vector", "rrf": rrf, "is_current": "true",
            "as_of": "2026-08-28", "dropped": dropped}


def test_dialog_fill_keeps_row_integrity_when_sorting_enabled():
    """回归（排序错行）：trace 填充顺序与列头排序不一致时，每行 9 列数据
    必须仍归属同一 block_id——sortingEnabled 下逐 setItem 的即时重排曾把
    后续行写进已移位的网格（数据串行/丢列）。"""
    _app()
    dialog = RetrievalDiagnosticDialog()
    assert dialog.table.isSortingEnabled()  # 前置：问题场景在排序开启态

    # 乱序 trace：与 block_id 默认排序（降序）不一致的填充顺序
    trace = [
        _trace_entry("aaa_低id", "hero", "甲", 0.5, 0.01, None),
        _trace_entry("zzz_高id_1", "faq", "", 0.1, 0.0, "below_threshold"),
        _trace_entry("mmm_中id", "card", "", 0.7, 0.02, None),
        _trace_entry("zzz_高id_2", "guide", "乙", 0.8, 0.03, None),
    ]
    dialog._on_result({"trace": trace, "backend": "onnx", "elapsed_ms": 5})

    assert dialog.table.rowCount() == 4
    for row in range(dialog.table.rowCount()):
        bid = dialog.table.item(row, 0).text()
        row_text = "".join(dialog.table.item(row, col).text()
                           for col in range(dialog.table.columnCount()))
        # 该行不得混入其他 block_id 的关键字段（kind/hero 得与 bid 匹配）
        for other in trace:
            if other["block_id"] != bid:
                assert other["hero"] not in row_text or not other["hero"]
    # below_threshold 行的 rrf 列显示占位符（RRF 融合前被丢弃，无融合分）
    bt_row = next(row for row in range(4)
                  if "below_threshold" == trace_row_of(dialog, row))
    assert dialog.table.item(bt_row, 5).text() == "—"
    # 填充完恢复排序能力
    assert dialog.table.isSortingEnabled()


def trace_row_of(dialog, row):
    label = dialog.table.item(row, 8).text()
    for key, text in DROP_LABELS.items():
        if text == label:
            return key
    return label


def test_dialog_close_with_running_worker_does_not_crash(monkeypatch):
    """回归（qFatal 崩溃）：检索进行中关闭对话框，运行中的 worker 由
    LIVE_WORKERS 兜底持有，进程不得因 running QThread 析构被强杀。"""
    import time

    _app()
    monkeypatch.setattr(
        diag_module, "diagnose",
        lambda *a, **k: (time.sleep(0.15) or {"trace": [], "items": []}))
    dialog = RetrievalDiagnosticDialog()
    dialog.query_edit.setText("慢查询")
    dialog._run_query()
    worker = dialog._worker
    assert worker is not None and worker.isRunning()

    dialog.close()  # 不等线程结束直接关（真实用户路径）

    # dialog 侧已摘引用；线程由 LIVE_WORKERS 持有，等待自然结束（进程不崩即过）
    assert dialog._worker is None
    assert worker.wait(5000)
    assert worker not in diag_module.LIVE_WORKERS
