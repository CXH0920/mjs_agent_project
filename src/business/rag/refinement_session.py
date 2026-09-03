# -*- coding: utf-8 -*-
"""索引精化会话状态：三池清单、磁盘/LLM 双基线、行状态与 curated 持久化写回。

自 IndexRefinementDialog 下沉（F2）的纯 Python 状态层（无 Qt 依赖）：对话框只
负责渲染与交互确认，清单归属、基线判定与写盘全部经本类完成。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from src.business.rag.refinement_service import (
    INDEX_FIELDS,
    PendingBlock,
    RefinementUpdate,
    apply_curated,
    clear_curated,
    scan_blocks,
)

logger = logging.getLogger("index_refinement")


class RefinementSession:
    """一次索引精化工作台会话的清单状态与持久化。

    行状态取值：pending 未处理 / suggested 已建议 / modified 已修改 /
    refined 已精化 / generated 已生成（文案与颜色映射归 UI 层）。
    """

    def __init__(self, corpus_dir: Path):
        self._corpus_dir = Path(corpus_dir)
        blocks = scan_blocks(self._corpus_dir)
        self._pending: list[PendingBlock] = blocks["pending"]  # 待精化（现状语义）
        self._curated: list[PendingBlock] = blocks["curated"]  # 已精化（curated 块）
        self._normal: list[PendingBlock] = blocks["normal"]    # 普通块（字段已满，未精化）
        self._total = len(self._pending)  # 初始待精化总数（进度条分母，不随保存/跳过变化）
        # 磁盘基线：block_id -> {field: 文本}，保存是否 no-op 与字段状态判定的依据
        self._saved_baseline: dict[str, dict[str, str]] = {}
        self._row_states: dict[str, str] = {}  # block_id -> 行状态
        for block in self._curated:
            self._row_states[block.block_id] = "refined"
        for block in self._normal:
            self._row_states[block.block_id] = "generated"
        for block in self._pending + self._curated + self._normal:
            self._saved_baseline[block.block_id] = {
                f: "\n".join(block.fields[f]) for f in INDEX_FIELDS}
        self._llm_baseline: dict[str, dict[str, str]] = {}  # 本次会话 LLM 建议内容
        self._skipped_count = 0  # 跳过的条目数（进度文案区分 #34）

    # ── 只读状态（对话框渲染与既有测试直读） ─────────────────────────

    @property
    def pending(self) -> list[PendingBlock]:
        return self._pending

    @property
    def curated(self) -> list[PendingBlock]:
        return self._curated

    @property
    def normal(self) -> list[PendingBlock]:
        return self._normal

    @property
    def total(self) -> int:
        return self._total

    @property
    def skipped_count(self) -> int:
        return self._skipped_count

    @property
    def saved_baseline(self) -> dict[str, dict[str, str]]:
        return self._saved_baseline

    @property
    def llm_baseline(self) -> dict[str, dict[str, str]]:
        return self._llm_baseline

    @property
    def row_states(self) -> dict[str, str]:
        return self._row_states

    def blocks_for_scope(self, scope: str) -> list[PendingBlock]:
        if scope == "curated":
            return list(self._curated)
        if scope == "all":
            return self._pending + self._curated + self._normal
        return list(self._pending)

    def is_pending(self, block_id: str) -> bool:
        return any(b.block_id == block_id for b in self._pending)

    # ── LLM 建议 ─────────────────────────────────────────────────────

    def note_suggested(self, block: PendingBlock, update: RefinementUpdate) -> None:
        """记录批量建议结果：写入 LLM 基线并置行状态（不回填编辑器）。"""
        baseline = {field: "\n".join(getattr(update, field)) for field in INDEX_FIELDS}
        self._llm_baseline[block.block_id] = baseline
        self._row_states[block.block_id] = "suggested"

    def record_llm_baseline(self, block_id: str, baseline: dict[str, str]) -> None:
        """单块建议回填编辑器时同步基线（不改行状态）。"""
        self._llm_baseline[block_id] = baseline

    def baseline_update(self, block_id: str) -> RefinementUpdate | None:
        """把本次 LLM 建议基线还原为 RefinementUpdate；该块无建议返回 None。"""
        baseline = self._llm_baseline.get(block_id)
        if baseline is None:
            return None
        values = {field: [line.strip() for line in baseline[field].splitlines() if line.strip()]
                  for field in INDEX_FIELDS}
        return RefinementUpdate(
            timing=values["timing"],
            trigger_condition=values["trigger_condition"],
            keywords=values["keywords"],
            related=values["related"],
            method="llm",
        )

    # ── 收集 / 保存 ──────────────────────────────────────────────────

    def collect_update(self, block_id: str, texts: dict[str, str]) -> RefinementUpdate | None:
        """把字段文本收集为 RefinementUpdate；与磁盘基线一致（无改动）返回 None。

        method 判定沿用现状：与本次 LLM 建议完全一致 → llm，否则 manual。

        Args:
            block_id: 目标块 id；
            texts: {field: 已 strip 的编辑器文本}，由调用方（对话框）收集。
        """
        saved = self._saved_baseline.get(block_id, {})
        llm = self._llm_baseline.get(block_id)
        values: dict[str, list[str]] = {}
        changed = False
        for field in INDEX_FIELDS:
            text = texts[field]
            values[field] = [line.strip() for line in text.splitlines() if line.strip()]
            if text != saved.get(field, ""):
                changed = True
        if not changed:
            return None
        if llm is not None:
            modified = any(texts[f] != llm.get(f, "") for f in INDEX_FIELDS)
            method = "manual" if modified else "llm"
        else:
            method = "manual"
        return RefinementUpdate(
            timing=values["timing"],
            trigger_condition=values["trigger_condition"],
            keywords=values["keywords"],
            related=values["related"],
            method=method,
        )

    def sync_saved(self, block: PendingBlock, update: RefinementUpdate) -> None:
        """保存成功后的内存同步：更新磁盘基线、行状态、列表归属（pending/normal → curated）。"""
        baseline = {f: "\n".join(getattr(update, f)) for f in INDEX_FIELDS}
        self._saved_baseline[block.block_id] = baseline
        self._llm_baseline.pop(block.block_id, None)
        self._row_states[block.block_id] = "refined"
        block.fields = {f: list(getattr(update, f)) for f in INDEX_FIELDS}
        block.missing = [f for f in INDEX_FIELDS if not block.fields[f]]
        block.method = update.method
        block.updated_at = update.updated_at or date.today().isoformat()
        if any(b.block_id == block.block_id for b in self._pending):
            self._pending = [b for b in self._pending if b.block_id != block.block_id]
            self._curated.append(block)
        elif any(b.block_id == block.block_id for b in self._normal):
            self._normal = [b for b in self._normal if b.block_id != block.block_id]
            self._curated.append(block)

    def apply_updates(
        self, updates_by_file: dict[str, dict[str, RefinementUpdate]],
    ) -> tuple[int, dict[str, str]]:
        """按语料文件分组批量写回并同步内存。

        Returns:
            (成功保存块数, {文件名: 错误信息})——出错的文件不迁移其任何块。
        """
        saved = 0
        errors: dict[str, str] = {}
        for fname, updates in updates_by_file.items():
            try:
                apply_curated(self._corpus_dir, updates, fname)
            except (OSError, ValueError) as error:
                logger.error("保存精化失败 %s: %s", fname, error)
                errors[fname] = str(error)
                continue
            for block_id, update in updates.items():
                # 待精化/普通块保存后迁移 curated；curated 块原地保留（sync_saved 内判定）
                block = next(b for b in (*self._pending, *self._curated, *self._normal)
                             if b.block_id == block_id)
                self.sync_saved(block, update)
                saved += 1
        return saved, errors

    # ── 跳过 / 取消精化 ──────────────────────────────────────────────

    def skip_block(self, block: PendingBlock) -> None:
        """跳过待精化块：移出清单并清理建议基线与行状态。"""
        self._skipped_count += 1
        self._pending = [item for item in self._pending if item.block_id != block.block_id]
        self._llm_baseline.pop(block.block_id, None)
        self._row_states.pop(block.block_id, None)

    def clear_curated_block(self, block: PendingBlock) -> None:
        """取消精化：删除 curated 字段，按字段空缺退回待精化池或转为普通块。

        磁盘写入失败（OSError/ValueError）原样上抛，调用方提示后可安全重试——
        本方法在写盘成功前不做任何内存状态变更。
        """
        clear_curated(self._corpus_dir, block.block_id, block.corpus)
        logger.info("取消精化 %s（%s）", block.name, block.block_id)
        self._curated = [b for b in self._curated if b.block_id != block.block_id]
        self._llm_baseline.pop(block.block_id, None)
        self._row_states.pop(block.block_id, None)
        # 磁盘顶层字段未变：保留 saved_baseline，切回该块时字段状态仍显示「已精化」
        block.method = ""
        block.updated_at = ""
        if block.missing:
            self._pending.append(block)
            self._row_states[block.block_id] = "pending"
        else:
            self._normal.append(block)
            self._row_states[block.block_id] = "generated"
