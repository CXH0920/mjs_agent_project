# -*- coding: utf-8 -*-
"""语料新鲜度判定（src/business/rag/corpus_freshness.py）纯函数测试。

task_states / index_state 用例自 tests/test_rag_maintenance_panel.py 平移
（P0-2 判定下沉 business 层，测试随模块走）；新增 stale_summary 欠账摘要与
rag_generation_enabled 包装。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.business.rag.corpus_freshness import (
    INDEX_TASK_NAME,
    index_state,
    stale_summary,
    task_states,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _utime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    now = time.time()
    # 数据源
    _write(root / "data" / "heroes.json", [{"id": 1, "name": "乐广"}, {"id": 2, "name": "张华"}])
    _write(root / "data" / "cards.json", [{"id": "8", "name": "杀"}])
    _write(root / "data" / "card_annotations.json", {"annotations": []})
    _write(root / "data" / "special_cards.json", [{"category": "专属牌", "name": "龙泉剑", "hero": "赵云"}])
    _write(root / "data" / "hero_classification.json", {"hero_categories": {"乐广": ["爆发型"]}})
    _write(root / "data" / "card_points.json", {"cards": [], "judge_rules": []})
    _write(root / "data" / "equip_attrs.json", [])
    # 武将变更时间轴（武将语料/武将攻略语料任务的数据源）
    _write(root / "data" / "mjs_adjustments.json", {"events": []})
    _write(root / "docs" / "元规则整理-完整版.md", "# 元规则")
    # 组合语料源（build_combo_corpus 依赖的社区 combo 材料）
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "bilibili_videos_weijiang.csv", "url\n")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "强力组合.md", "# combo")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "巴清搭配.md", "# combo")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "平阳公主强势组合盘点.md", "# combo")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "孟尝君 + 黄月英.md", "# combo")
    # 武将攻略语料源（目录源，建目录+占位即可）
    _write(root / "data" / "raw_guides" / "jinxia" / "guides" / "曹操.md", "# guide")
    # 语料输出（全部已生成）
    for name in ("武将RAG语料.json", "卡牌RAG语料.json", "卡牌点数花色语料.json",
                 "装备属性语料.json", "加强削弱语料.json", "元规则RAG语料-章节块.json",
                 "术语表.json", "FAQ裁定块.json", "特殊机制语料.json", "武将分类语料.json",
                 "组合RAG语料.json", "武将攻略RAG语料.json"):
        _write(root / "data" / "rag_corpus" / name, [{"block_id": "x"}])
    # 向量索引段文件（index_state 判定源；与语料同 mtime → 状态「最新」）
    seg = root / "data" / "rag_index" / "chroma" / "seg-uuid" / "data_level0.bin"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_text("x", encoding="utf-8")
    for path in root.rglob("*"):
        _utime(path, now - 100)
    return root


# ---------------------------------------------------------------
# task_states（自 test_rag_maintenance_panel 平移）
# ---------------------------------------------------------------


def test_all_tasks_up_to_date(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    rows = task_states(root)
    assert len(rows) == 10
    assert all(row["status"] == "最新" for row in rows)
    assert all(row["count"] == 1 for row in rows)


def test_stale_when_source_newer(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _utime(root / "data" / "heroes.json", time.time() + 60)
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["武将语料"] == "待重建"
    assert rows["武将分类语料"] == "待重建"
    assert rows["卡牌语料"] == "最新"


def test_missing_output_marks_rebuild(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "data" / "rag_corpus" / "特殊机制语料.json").unlink()
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["特殊机制语料"] == "待重建"


def test_missing_source(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "data" / "special_cards.json").unlink()
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["特殊机制语料"] == "缺源"


def test_task_states_tolerates_file_vanishing_mid_check(
    tmp_path: Path, monkeypatch) -> None:
    """判定途中文件消失不得抛异常：修改时间须一步取到，不能「先判存在、再取」。

    分两步时文件恰在缝隙被删，会抛未捕获 FileNotFoundError 冒泡到 Qt 槽，中断
    整次刷新/胶囊菜单构建。模拟：目标文件首次访问成功（还在），其后视作已删除。
    """
    root = _make_root(tmp_path)
    before = {row["name"]: row["status"] for row in task_states(root, with_counts=False)}
    # 取只被一个任务访问的源与输出（特殊机制语料；with_counts=False 不另取块数），
    # 目标文件在判定中恰好只访问一次，模拟才不受跨任务多次访问的干扰
    vanishing = {root / "data" / "special_cards.json",
                 root / "data" / "rag_corpus" / "特殊机制语料.json"}
    seen: set[Path] = set()
    real_stat = Path.stat

    def _vanishing_stat(self: Path, *args, **kwargs):
        if self in vanishing:
            if self in seen:
                raise FileNotFoundError(2, "模拟：判定途中被删", str(self))
            seen.add(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _vanishing_stat)

    after = {row["name"]: row["status"] for row in task_states(root, with_counts=False)}

    assert after == before  # 不抛异常，且判定与文件未消失时一致


def test_task_defs_include_card_corpus_link(tmp_path: Path) -> None:
    """#1 回归：任务表为单一事实源，装备属性语料必须声明对卡牌语料的依赖。"""
    from src.business.rag.task_defs import TASKS
    equip = next(t for t in TASKS if t["name"] == "装备属性语料")
    assert "data/rag_corpus/卡牌RAG语料.json" in equip["sources"]


def test_equip_task_tracks_card_corpus_dependency(tmp_path: Path) -> None:
    """#1 回归：卡牌RAG语料.json 变更后装备属性语料应标记待重建（跨语料联动）。"""
    root = _make_root(tmp_path)
    _utime(root / "data" / "rag_corpus" / "卡牌RAG语料.json", time.time() + 60)
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["装备属性语料"] == "待重建"
    assert rows["卡牌语料"] == "最新"


# ---------------------------------------------------------------
# index_state（自 test_rag_maintenance_panel 平移）
# ---------------------------------------------------------------


def test_index_state_fresh(tmp_path: Path) -> None:
    """段文件与语料同 mtime（fixture 统一设定）→ 索引最新。"""
    root = _make_root(tmp_path)
    row = index_state(root)
    assert row["name"] == INDEX_TASK_NAME
    assert row["status"] == "最新"
    assert row["built"] > 0


def test_index_state_missing_dir(tmp_path: Path) -> None:
    """索引目录不存在 → 未构建（不与语料比较）。"""
    import shutil

    root = _make_root(tmp_path)
    shutil.rmtree(root / "data" / "rag_index")
    assert index_state(root)["status"] == "未构建"


# ---------------------------------------------------------------
# stale_summary / with_counts / rag_generation_enabled（P0-2 新增）
# ---------------------------------------------------------------


def test_stale_summary_reports_stale_and_index(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _utime(root / "data" / "heroes.json", time.time() + 60)
    summary = stale_summary(root)
    assert "武将语料" in summary.stale_tasks
    assert "卡牌语料" not in summary.stale_tasks
    assert summary.corpus_built_at  # 有语料输出 → 构建时间非空
    # fixture 中索引与语料同 mtime → 最新
    assert summary.index_status == "最新"


def test_stale_summary_all_fresh(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    summary = stale_summary(root)
    assert summary.stale_tasks == []
    assert summary.index_status == "最新"


def test_stale_summary_excludes_missing_source(tmp_path: Path) -> None:
    """缺源 ≠ 过期：缺源任务不计入欠账清单（首装未部署资料的口径）。"""
    root = _make_root(tmp_path)
    (root / "data" / "special_cards.json").unlink()
    summary = stale_summary(root)
    assert "特殊机制语料" not in summary.stale_tasks


def test_stale_summary_no_corpus_dir(tmp_path: Path) -> None:
    """语料目录整体缺失：其余任务全部待重建、构建时间为「从未构建」，不抛异常。

    装备属性语料不计入：其源声明含卡牌RAG语料.json（跨语料依赖），目录删除后
    判「缺源」而非「待重建」——欠账提示排除缺源的口径恰好覆盖此形态。
    """
    import shutil

    root = _make_root(tmp_path)
    shutil.rmtree(root / "data" / "rag_corpus")
    summary = stale_summary(root)
    assert len(summary.stale_tasks) == 9
    assert "装备属性语料" not in summary.stale_tasks
    assert summary.corpus_built_at == "从未构建"


def test_task_states_without_counts_skips_json_parse(
        tmp_path: Path, monkeypatch) -> None:
    """with_counts=False：不解析语料 JSON（mtime-only 毫秒级预检路径）。"""
    from src.business.rag import corpus_freshness as cf

    root = _make_root(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(cf, "_output_count", lambda p: calls.append(p) or 0)
    rows = task_states(root, with_counts=False)
    assert calls == []
    assert all(row["count"] is None for row in rows)
    # 默认路径仍走块数统计
    rows = task_states(root)
    assert calls and all(row["count"] == 0 for row in rows)


def test_rag_generation_enabled_reads_config(monkeypatch) -> None:
    """包装透传 src.rag.config.RAG_ENABLED（经模块属性，可 monkeypatch）。"""
    from src.business.rag.corpus_freshness import rag_generation_enabled
    from src.rag import config as rag_config

    monkeypatch.setattr(rag_config, "RAG_ENABLED", True, raising=False)
    assert rag_generation_enabled() is True
    monkeypatch.setattr(rag_config, "RAG_ENABLED", False, raising=False)
    assert rag_generation_enabled() is False
