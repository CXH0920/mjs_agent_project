# -*- coding: utf-8 -*-
"""语料新鲜度判定（business 层单一事实源，P0-2 下沉）。

自 src/ui/maintenance/rag_maintenance_panel.py 平移 task_states / index_state
（含块数缓存），供三个消费方同源共用：知识库维护面板（原宿主）、数据新鲜度
芯片菜单、AI 生成发起前的知情预检。纯函数无 Qt 依赖，范式同
src/business/freshness.py（业务核心 + UI 消费）。

本模块刻意不 import audit_service（其经 refinement_service 拉入 src.scraper
重依赖链），CORPUS_DIR 在此同值自带（字符串常量，两处同步维护）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from src.business.rag.task_defs import TASKS as _RAG_TASKS
from src.config.env import PROJECT_ROOT

CORPUS_DIR = "data/rag_corpus"

# 语料任务：名称 -> (源文件, 输出语料文件)；定义与 scripts/maintain_rag.py 共用
# （单一事实源见 src/business/rag/task_defs.py，改动请同步维护该处）
TASK_DEFS: list[tuple[str, list[str], list[str]]] = [
    (task["name"], task["sources"], task["outputs"]) for task in _RAG_TASKS
]

# 语料块数缓存：path -> (mtime, size, count)，mtime/size 未变时不重复解析
_COUNT_CACHE: dict[str, tuple[float, int, int | None]] = {}


def _output_count(path: Path) -> int | None:
    """读取语料文件的块数（带 (mtime, size) 缓存，避免每次切页全量解析）。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    cached = _COUNT_CACHE.get(str(path))
    if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        count = len(data) if isinstance(data, list) else None
    except (OSError, json.JSONDecodeError):
        count = None
    _COUNT_CACHE[str(path)] = (stat.st_mtime, stat.st_size, count)
    return count


def task_states(root: Path, with_counts: bool = True) -> list[dict]:
    """计算各语料任务状态；供 UI 展示与测试。

    with_counts=False 时跳过块数统计（不解析语料 JSON），供毫秒级预检调用。
    """
    rows = []
    for name, sources, outputs in TASK_DEFS:
        source_mtime = 0.0
        missing_sources = []
        for rel in sources:
            path = root / rel
            if path.exists():
                source_mtime = max(source_mtime, path.stat().st_mtime)
            else:
                missing_sources.append(rel)
        output_mtimes = []
        for rel in outputs:
            path = root / CORPUS_DIR / rel
            output_mtimes.append(path.stat().st_mtime if path.exists() else 0.0)
        if missing_sources:
            status = "缺源"
        elif not output_mtimes or any(t == 0.0 for t in output_mtimes):
            status = "待重建"
        elif source_mtime > max(output_mtimes) + 1:
            status = "待重建"
        else:
            status = "最新"
        count = None
        if with_counts and output_mtimes and output_mtimes[0] > 0:
            count = _output_count(root / CORPUS_DIR / outputs[0])
        rows.append({"name": name, "status": status, "count": count,
                     "sources": sources, "outputs": outputs})
    return rows


# 向量索引左栏项（不是 maintain_rag 语料任务，↻ 走 indexer 命令而非 --only）
INDEX_TASK_NAME = "向量索引"
INDEX_DIR = "data/rag_index/chroma"


def index_state(root: Path) -> dict:
    """计算向量索引新鲜度；供 UI 展示与测试。

    构建时间取段目录（chroma/<uuid>/）二进制文件的 mtime——chroma.sqlite3 的
    mtime 会被查询路径的 PersistentClient（无只读设置）触碰，实测查询后即变，
    不能作判定信号；段文件 mtime 与构建台账（task_results.jsonl）精确吻合。
    """
    seg_root = root / INDEX_DIR
    built = 0.0
    if seg_root.is_dir():
        for sub in seg_root.iterdir():
            if not sub.is_dir():
                continue  # chroma.sqlite3 落在根层，只遍历段子目录
            for file in sub.iterdir():
                if file.is_file():
                    try:
                        built = max(built, file.stat().st_mtime)
                    except OSError:
                        continue
    if built == 0.0:
        status = "未构建"
    else:
        corpus_max = 0.0
        for _name, _sources, outputs in TASK_DEFS:
            for rel in outputs:
                try:
                    corpus_max = max(corpus_max, (root / CORPUS_DIR / rel).stat().st_mtime)
                except OSError:
                    continue
        # 容差与 task_states 的 +1 同口径，避免同秒写入的抖动误报
        status = "待同步" if corpus_max > built + 1 else "最新"
    return {"name": INDEX_TASK_NAME, "status": status, "built": built}


@dataclass(frozen=True)
class CorpusStaleSummary:
    """语料欠账摘要（mtime-only 快照，供采集联动提醒与生成前预检消费）。"""

    stale_tasks: list[str]   # 状态为「待重建」的任务名（「缺源」不入：首装缺源≠过期）
    index_status: str        # index_state 的 status（最新/待同步/未构建）
    corpus_built_at: str     # 全部语料输出最近一次构建时间（"%m-%d %H:%M"）


def stale_summary(root: Path | None = None) -> CorpusStaleSummary:
    """毫秒级欠账摘要：mtime-only（with_counts=False，不解析语料 JSON）。"""
    root = root or PROJECT_ROOT
    rows = task_states(root, with_counts=False)
    stale = [row["name"] for row in rows if row["status"] == "待重建"]
    built = 0.0
    for row in rows:
        for rel in row["outputs"]:
            try:
                built = max(built, (root / CORPUS_DIR / rel).stat().st_mtime)
            except OSError:
                continue
    return CorpusStaleSummary(
        stale_tasks=stale,
        index_status=index_state(root)["status"],
        corpus_built_at=time.strftime("%m-%d %H:%M", time.localtime(built)) if built
        else "从未构建",
    )


def rag_generation_enabled() -> bool:
    """UI 侧读取 RAG_ENABLED（src/rag/config 有 import 副作用，在此隔离包装）。

    经模块属性读取，测试可 monkeypatch rag_config.RAG_ENABLED。
    """
    from src.rag import config as rag_config  # noqa: PLC0415 — 延迟导入隔离副作用

    return bool(rag_config.RAG_ENABLED)
