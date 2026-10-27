"""4 个相性生成循环的特征化测试：锁定协议行、failed_items 格式与提交时机。

这是 run_synergy_* 同构循环合并（_run_synergy_pairs）的安全网：先按当前实现
锁定行为，重构后必须零改动通过；将来要改任何输出格式，先改这里再改实现。

协议行同时是 QProcess 父进程的进度契约（fetch 服务与进度窗依赖），样本与
tests/test_fetch_utils.py 的解析协议一一对应。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from src.scraper.ai import generation as gen_module
from src.scraper.ai.generation import (
    run_synergy_generation,
    run_synergy_list_generation,
    run_synergy_pair_generation,
    run_synergy_single_generation,
)


class FakeGenerator:
    """按预设顺序返回相性结果，避免真实网络调用。"""

    def __init__(self, synergies) -> None:
        self._synergies = iter(synergies)
        self.calls: list[tuple[dict, dict]] = []

    def generate_synergy(self, hero_a: dict, hero_b: dict):
        self.calls.append((hero_a, hero_b))
        return next(self._synergies), {"prompt_tokens": 1, "completion_tokens": 2}


@pytest.fixture
def record_commits(monkeypatch):
    """记录每次分批提交的数据量，保留真实落盘行为。"""
    commits: list[int] = []
    real_commit = gen_module._commit_generation_batch

    def recording_commit(result, path, data):
        commits.append(len(data))
        real_commit(result, path, data)

    monkeypatch.setattr(gen_module, "_commit_generation_batch", recording_commit)
    return commits


def test_pair_mode_locks_protocol_failed_labels_and_commit_timing(
    tmp_path: Path, capsys, record_commits, monkeypatch
) -> None:
    """指定武将模式：跳过/成功/失败/无评分占位、a<->b 失败标签、分批提交时机。"""
    monkeypatch.setattr(gen_module, "SYNERGY_BATCH_SAVE_INTERVAL", 2)
    synergy_path = tmp_path / "synergies.json"
    pair_file = tmp_path / "pairs.json"
    pair_file.write_text(json.dumps([
        {"id": 1, "name": "甲"},
        {"id": 2, "name": "乙"},
        {"id": 3, "name": "丙"},
        {"id": 4, "name": "丁"},
    ]), encoding="utf-8")
    generator = FakeGenerator([
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5},
        None,
        {"hero_a_id": 2, "hero_b_id": 3, "score": 8},
        {"hero_a_id": 2, "hero_b_id": 4},
        None,
    ])

    result = run_synergy_pair_generation(
        pair_file=str(pair_file), heroes=[], generator=generator, synergy_path=synergy_path,
        existing_synergy_dict={(1, 4): {"hero_a_id": 1, "hero_b_id": 4, "score": 1}},
        existing_synergy_keys={(1, 4)},
    )

    assert capsys.readouterr().out == (
        "\n  相性配对生成 (指定武将)...\n"
        "  所选武将: 4 个, 共 6 对\n"
        "  [1/6] 甲 <-> 乙 START\n"
        "  [1/6] 甲 <-> 乙 OK - 评分: 5\n"
        "  [2/6] 甲 <-> 丙 START\n"
        "  [2/6] 甲 <-> 丙 FAIL\n"
        "  [3/6] 甲 <-> 丁 SKIP（已有相性）\n"
        "  [4/6] 乙 <-> 丙 START\n"
        "  [4/6] 乙 <-> 丙 OK - 评分: 8\n"
        "  [5/6] 乙 <-> 丁 START\n"
        "  [5/6] 乙 <-> 丁 OK - 评分: ?\n"
        "  [6/6] 丙 <-> 丁 START\n"
        "  [6/6] 丙 <-> 丁 FAIL\n"
        "  相性完成: 新增 3 对，跳过 1 对，共 4 对\n"
    )
    assert result.failed_items == ["甲<->丙", "丙<->丁"]
    assert record_commits == [3, 4]  # 每 2 个成功提交一次（工作副本含已有记录）；末尾补交剩余
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == [
        {"hero_a_id": 1, "hero_b_id": 4, "score": 1},
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5, "last_updated": date.today().isoformat()},
        {"hero_a_id": 2, "hero_b_id": 3, "score": 8, "last_updated": date.today().isoformat()},
        {"hero_a_id": 2, "hero_b_id": 4, "last_updated": date.today().isoformat()},
    ]


def test_single_mode_locks_bare_name_failure_label(tmp_path: Path, capsys) -> None:
    """选定武将模式：START/OK/FAIL/SKIP 全部用对方单名，失败项是裸武将名（非 a<->b）。"""
    synergy_path = tmp_path / "synergies.json"
    single_file = tmp_path / "single.json"
    single_file.write_text(json.dumps([{"id": 1, "name": "甲"}]), encoding="utf-8")
    generator = FakeGenerator([
        None,
        {"hero_a_id": 1, "hero_b_id": 4, "score": 6},
    ])

    result = run_synergy_single_generation(
        single_file=str(single_file),
        heroes=[{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"},
                {"id": 3, "name": "丙"}, {"id": 4, "name": "丁"}],
        generator=generator,
        synergy_path=synergy_path,
        existing_synergy_dict={},
        existing_synergy_keys={(1, 3)},
    )

    assert capsys.readouterr().out == (
        "\n  相性配对生成 (选定武将 x 全体)...\n"
        "  甲 <-> 3 个武将\n"
        "  [1/3] 乙 START\n"
        "  [1/3] 乙 FAIL\n"
        "  [2/3] 丙 SKIP（已有相性）\n"
        "  [3/3] 丁 START\n"
        "  [3/3] 丁 OK - 评分: 6\n"
        "  相性完成: 新增 1 对，跳过 1 对，失败 1 对, 共 1 对\n"
    )
    assert result.failed_items == ["乙"]


def test_full_mode_locks_score_threshold_pop_and_zero_placeholder(
    tmp_path: Path, capsys,
) -> None:
    """全量模式：永不跳过已有对；低于下限移除旧记录；评分缺失按 0 展示。"""
    synergy_path = tmp_path / "synergies.json"
    old12 = {"hero_a_id": 1, "hero_b_id": 2, "score": 1}
    old13 = {"hero_a_id": 1, "hero_b_id": 3, "score": 2}
    generator = FakeGenerator([
        {"hero_a_id": 1, "hero_b_id": 2, "score": 9},
        {"hero_a_id": 1, "hero_b_id": 3, "score": 2},
        {"hero_a_id": 2, "hero_b_id": 3},
    ])

    result = run_synergy_generation(
        heroes=[{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}, {"id": 3, "name": "丙"}],
        generator=generator,
        synergy_path=synergy_path,
        existing_synergy_dict={(1, 2): old12, (1, 3): old13},
        existing_synergy_keys={(1, 2), (1, 3)},
        score_threshold=5,
        api_config={"model": "m1"},
    )

    sep = "=" * 55
    assert capsys.readouterr().out == (
        f"\n{sep}\n"
        "  生成相性评分 -- m1 (3 对)\n"
        f"{sep}\n"
        "  [1/3] 甲 <-> 乙 START\n"
        "  [1/3] 甲 <-> 乙 OK - 评分: 9\n"
        "  [2/3] 甲 <-> 丙 START\n"
        "  [2/3] 甲 <-> 丙 OK - 评分: 2\n"
        "  [3/3] 乙 <-> 丙 START\n"
        "  [3/3] 乙 <-> 丙 OK - 评分: 0\n"
        "\n  相性完成: 成功 3 对，共 1 对\n"
    )
    assert result.failed_items == []
    assert result.completed == 3
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == [
        {"hero_a_id": 1, "hero_b_id": 2, "score": 9, "last_updated": date.today().isoformat()},
    ]


def test_list_mode_locks_invalid_pair_labels_and_skip(tmp_path: Path, capsys) -> None:
    """实战配队清单模式：无效配对记 #a<->#b 标签，已有对跳过，失败按“项”计数。"""
    synergy_path = tmp_path / "synergies.json"
    pairs_file = tmp_path / "pairs_list.json"
    pairs_file.write_text(json.dumps([
        {"hero_a_id": 1, "hero_b_id": 2},
        {"hero_a_id": 1, "hero_b_id": 9},
        {"hero_a_id": 2, "hero_b_id": 2},
        {"hero_a_id": 3, "hero_b_id": 4},
    ]), encoding="utf-8")
    generator = FakeGenerator([
        {"hero_a_id": 1, "hero_b_id": 2, "score": 7},
    ])

    result = run_synergy_list_generation(
        pairs_file=str(pairs_file),
        heroes=[{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"},
                {"id": 3, "name": "丙"}, {"id": 4, "name": "丁"}],
        generator=generator,
        synergy_path=synergy_path,
        existing_synergy_dict={},
        existing_synergy_keys={(3, 4)},
    )

    assert capsys.readouterr().out == (
        "\n  相性配对生成 (实战配队清单)...\n"
        "  配对清单: 2 对\n"
        "  [1/2] 甲 <-> 乙 START\n"
        "  [1/2] 甲 <-> 乙 OK - 评分: 7\n"
        "  [2/2] 丙 <-> 丁 SKIP（已有相性）\n"
        "  相性完成: 新增 1 对，跳过 1 对，失败 2 项, 共 1 对\n"
    )
    assert result.failed_items == ["#1<->#9（配对无效）", "#2<->#2（配对无效）"]


def test_list_mode_empty_pairs_fails_fast(tmp_path: Path, capsys) -> None:
    """清单为空或全部无效时直接失败返回，不进入生成循环。"""
    synergy_path = tmp_path / "synergies.json"
    pairs_file = tmp_path / "pairs_list.json"
    pairs_file.write_text(json.dumps([
        {"hero_a_id": 1, "hero_b_id": 1},
    ]), encoding="utf-8")

    result = run_synergy_list_generation(
        pairs_file=str(pairs_file), heroes=[{"id": 1, "name": "甲"}],
        generator=FakeGenerator([]), synergy_path=synergy_path,
        existing_synergy_dict={}, existing_synergy_keys=set(),
    )

    assert capsys.readouterr().out == "\n  相性配对生成 (实战配队清单)...\n"
    assert result.failed_items == ["#1<->#1（配对无效）", "配对清单为空或全部无效"]
    assert not result.committed


def test_pair_mode_skips_invalid_and_duplicate_entries(
    tmp_path: Path, record_commits,
) -> None:
    """指定武将文件缺 id/name 或同 id 重复：记失败项跳过，不再裸 KeyError 中断。"""
    synergy_path = tmp_path / "synergies.json"
    pair_file = tmp_path / "pairs.json"
    pair_file.write_text(json.dumps([
        {"id": 1, "name": "甲"},
        {"name": "缺id"},
        {"id": 3},
        "不是对象",
        {"id": 1, "name": "甲替身"},
        {"id": 2, "name": "乙"},
    ], ensure_ascii=False), encoding="utf-8")
    generator = FakeGenerator([{"score": 5, "reason": "ok"}] * 10)

    summary = run_synergy_pair_generation(
        str(pair_file), [], generator, str(synergy_path), {}, set(),
        update_mode=False,
    )

    # 坏条目全部进 failed_items（含可定位的条目序号），好坏条目去重后 2 人 1 对正常生成
    joined = "\n".join(summary.failed_items)
    assert "第 2 条缺少 id/name" in joined
    assert "第 3 条缺少 id/name" in joined
    assert "第 4 条缺少 id/name" in joined
    assert "第 5 条 id=1（甲替身）重复" in joined
    assert [(a["id"], b["id"]) for a, b in generator.calls] == [(1, 2)]


def test_single_mode_rejects_entry_missing_fields(tmp_path: Path) -> None:
    """选定武将缺 id/name：记失败项返回，不再 target["id"] 裸下标崩溃。"""
    synergy_path = tmp_path / "synergies.json"
    single_file = tmp_path / "single.json"
    single_file.write_text(json.dumps([{"name": "只有名字"}]), encoding="utf-8")

    summary = run_synergy_single_generation(
        str(single_file), [], FakeGenerator([]), str(synergy_path), {}, set(),
    )

    assert "选定武将第 1 条缺少 id/name 字段，已跳过" in summary.failed_items
    # 坏条目被跳过后数量不足 1，随后按既有数量校验收尾
    assert "指定武将数量无效" in summary.failed_items
