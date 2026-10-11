# -*- coding: utf-8 -*-
"""检索诊断业务服务：单条查询的候选去向、后端与耗时（纯 dict 输出，不碰 Qt 渲染）。

worker 沿用 suggest_controller 的 QThread 模式——首次检索要加载编码器与
语料（秒级），不能占 GUI 线程。
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("retrieval_diagnostic")

# 持有运行中的 worker：dialog 关闭销毁后 Python 引用丢失不会连带析构
# 运行中的 QThread（Qt6 对 running QThread 析构是 qFatal），线程结束自移除
LIVE_WORKERS: set = set()


def diagnose(query: str, top_k: int = 12, heroes: list[str] | None = None) -> dict:
    """执行一次检索并返回诊断结果：items=最终保留块，trace=每条候选去向
    （含被 below_threshold / not_current / hero_filter / max_keyword_only /
    kind_quota 剔除的块）。"""
    from src.rag import config
    from src.rag.retriever import Retriever

    retriever = Retriever()
    trace: list[dict] = []
    t0 = time.perf_counter()
    items = retriever.search(query, heroes=heroes, top_k=top_k, trace=trace)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    result_rows = []
    for it in items:
        meta = it.get("metadata") or {}
        result_rows.append({
            "block_id": it.get("block_id", ""),
            "kind": meta.get("kind", ""),
            "hero": meta.get("hero", ""),
            "score": round(float(it.get("score", 0.0)), 4),
            "source": it.get("source", ""),
            "rrf": round(float(it.get("rrf", 0.0)), 5),
            "is_current": meta.get("is_current", "true"),
            "as_of": meta.get("as_of", ""),
            "dropped": None,
            "text": (it.get("text") or "")[:160],
        })
    return {
        "query": query,
        "heroes": list(heroes or []),
        "top_k": top_k,
        "items": result_rows,
        "trace": trace,
        "backend": config.EMBEDDING_BACKEND,
        "elapsed_ms": elapsed_ms,
    }


class DiagnosticWorker(QThread):
    """后台检索线程：结果/失败经信号回 GUI 线程。

    parent=None + LIVE_WORKERS 持有：对话框关闭不连带析构运行中的线程
    （Qt6 对 running QThread 析构是 qFatal，同 suggest_controller #60/#61）。
    """

    result_ready = Signal(object)  # dict（diagnose 输出）
    failed = Signal(str)

    def __init__(self, query: str, top_k: int = 12,
                 heroes: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._query = query
        self._top_k = top_k
        self._heroes = list(heroes or [])

    def run(self) -> None:
        LIVE_WORKERS.add(self)
        try:
            self.result_ready.emit(
                diagnose(self._query, self._top_k, self._heroes))
        except Exception as error:  # noqa: BLE001 — 失败原因需回 GUI 呈现
            logger.error("检索诊断失败", exc_info=True)
            self.failed.emit(str(error))
        finally:
            LIVE_WORKERS.discard(self)
