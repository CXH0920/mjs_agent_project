"""白名单配置：用户层合并、频次记录、分类与对话框逻辑测试。"""

from __future__ import annotations

import json

import pytest
from src.business.recognition import pending_stats
from src.ocr.character_similarity import (
    CharacterSimilarityService,
    find_whitelist_conflicts,
)
from src.ui.configuration import whitelist_config_dialog as wcd
from src.ui.configuration.whitelist_config_dialog import WhitelistConfigDialog, classify_entry


@pytest.fixture
def overrides_path(tmp_path, monkeypatch):
    path = tmp_path / "ocr_confusion_overrides.json"
    monkeypatch.setattr("src.ocr.character_similarity.OVERRIDES_PATH", path)
    return path


@pytest.fixture
def stats_path(tmp_path, monkeypatch):
    path = tmp_path / "ocr_name_pending_stats.json"
    monkeypatch.setattr(pending_stats, "STATS_PATH", path)
    return path


# ── R1 白名单基线 ─────────────────────────────────────────────────────


def test_huai_dun_whitelist_hit_and_direction() -> None:
    service = CharacterSimilarityService()
    assert service.single_substitution_similarity("夏侯怀", "夏侯惇") == 1.0
    assert service.is_safe_single_substitution("夏侯怀", "夏侯惇")
    # 单向性：OCR 读出「夏侯惇」时不会被反向拉成「夏侯怀」
    assert service.single_substitution_similarity("夏侯惇", "夏侯怀") != 1.0


# ── R4 用户层白名单合并 ───────────────────────────────────────────────


def test_overrides_merge_into_effective_whitelist(overrides_path) -> None:
    overrides_path.write_text(
        json.dumps({"version": 1, "pairs": {"早": "卓"}}), encoding="utf-8"
    )
    service = CharacterSimilarityService()
    assert service.single_substitution_similarity("早文君", "卓文君") == 1.0
    # 基线对不受影响
    assert service.single_substitution_similarity("樊会", "樊哙") == 1.0


def test_overrides_missing_file_falls_back_to_baseline(overrides_path) -> None:
    service = CharacterSimilarityService()
    assert service.single_substitution_similarity("樊会", "樊哙") == 1.0


def test_overrides_corrupt_file_degrades_to_baseline(overrides_path) -> None:
    overrides_path.write_text("{not json", encoding="utf-8")
    service = CharacterSimilarityService()
    assert service.single_substitution_similarity("樊会", "樊哙") == 1.0


def test_overrides_invalid_entries_skipped(overrides_path) -> None:
    overrides_path.write_text(
        json.dumps({"version": 1, "pairs": {"早": "卓", "好": "好", "两字": "合"}}),
        encoding="utf-8",
    )
    service = CharacterSimilarityService()
    assert service.single_substitution_similarity("早文君", "卓文君") == 1.0
    # 合法性存疑条目被跳过：好→好 无意义，两字→合 长度非法
    assert service._effective_whitelist.get("好") is None
    assert service._effective_whitelist.get("两字") is None


def test_reload_whitelist_picks_up_new_file(overrides_path) -> None:
    service = CharacterSimilarityService()
    assert service.single_substitution_similarity("早文君", "卓文君") != 1.0
    overrides_path.write_text(
        json.dumps({"version": 1, "pairs": {"早": "卓"}}), encoding="utf-8"
    )
    service.reload_whitelist()
    assert service.single_substitution_similarity("早文君", "卓文君") == 1.0


# ── R6 静态冲突检查 ───────────────────────────────────────────────────


def test_find_whitelist_conflicts_detects_risky_pair() -> None:
    conflicts = find_whitelist_conflicts(
        ["杨雨", "杨羽"], {"雨": "羽"},
    )
    assert conflicts == [("杨雨", "杨羽", "雨→羽")]


def test_find_whitelist_conflicts_ignores_unrelated_names() -> None:
    assert find_whitelist_conflicts(["诸葛亮", "司马懿"], {"会": "哙"}) == []


# ── R2 频次记录 ───────────────────────────────────────────────────────


def test_record_pending_aggregates_counts(stats_path) -> None:
    pending_stats.record_pending("夏侯怀", ["夏侯惇"], "match_guide")
    pending_stats.record_pending("夏侯怀", ["夏侯惇"], "match_guide")  # 同窗节流合并
    document = json.loads(stats_path.read_text(encoding="utf-8"))
    entry = document["entries"]["夏侯怀"]
    assert entry["count"] == 1
    assert entry["candidates"] == ["夏侯惇"]
    assert entry["is_truncation"] is False


def test_record_pending_throttles_repeated_reads(stats_path, monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(pending_stats.time, "time", lambda: clock["now"])
    pending_stats.record_pending("夏侯怀", ["夏侯惇"], "match_guide")
    clock["now"] += 5  # 同局 2 秒一拍的重复轮询：60 秒窗口内静默
    pending_stats.record_pending("夏侯怀", ["夏侯惇"], "match_guide")
    clock["now"] += 120  # 窗口外重新计数
    pending_stats.record_pending("夏侯怀", ["夏侯惇"], "match_guide")
    document = json.loads(stats_path.read_text(encoding="utf-8"))
    assert document["entries"]["夏侯怀"]["count"] == 2


def test_record_pending_skips_empty_and_marks_truncation(stats_path) -> None:
    pending_stats.record_pending("", ["夏侯惇"], "match_guide")
    pending_stats.record_pending("荀", ["荀勖", "荀彧", "荀灌"], "hero_selection")
    document = json.loads(stats_path.read_text(encoding="utf-8"))
    assert "" not in document["entries"]
    assert document["entries"]["荀"]["is_truncation"] is True


def test_record_confirmation_requires_candidate_and_diff(stats_path) -> None:
    pending_stats.record_confirmation("夏侯怀", "夏侯惇", ["夏侯惇"])
    pending_stats.record_confirmation("夏侯怀", "曹操", ["夏侯惇"])   # 候选外，拒绝
    pending_stats.record_confirmation("夏侯惇", "夏侯惇", ["夏侯惇"])  # 无错字对，不记
    document = json.loads(stats_path.read_text(encoding="utf-8"))
    entry = document["entries"]["夏侯怀"]
    assert entry["confirmed"] == "夏侯惇"
    assert entry["confirmed_count"] == 1


# ── R5 对话框逻辑 ─────────────────────────────────────────────────────


def test_classify_entry_categories() -> None:
    assert classify_entry("夏侯怀", {"confirmed": "夏侯惇", "candidates": ["夏侯惇"]})[0] == "A+"
    assert classify_entry("早文君", {"candidates": ["卓文君"]})[0] == "A"
    assert classify_entry("卫珍", {"candidates": ["卫玠", "卫青"]})[0] == "B"
    assert classify_entry("荀", {"is_truncation": True, "candidates": ["荀勖"]})[0] == "C"


def test_classify_entry_whole_name_difference_is_manual() -> None:
    # 确认答案与读数差异不是「恰一字」时，无法归约为单字对，转人工
    category, source, target = classify_entry(
        "夏侯怀", {"confirmed": "诸葛亮", "candidates": ["诸葛亮"]},
    )
    assert category == "B"
    assert source == "" and target == ""


def test_overrides_round_trip(tmp_path) -> None:
    path = tmp_path / "overrides.json"
    wcd.save_overrides({"早": "卓", "珍": "玠"}, path)
    assert wcd.load_overrides(path) == {"早": "卓", "珍": "玠"}


def test_dialog_lists_and_applies_pair(qapp, tmp_path, monkeypatch) -> None:
    stats = tmp_path / "stats.json"
    stats.write_text(
        json.dumps({"version": 1, "entries": {
            "夏侯怀": {
                "count": 2, "candidates": ["夏侯惇"], "scenes": ["match_guide"],
                "confirmed": "夏侯惇", "confirmed_count": 2, "is_truncation": False,
            },
            "荀": {
                "count": 4, "candidates": ["荀勖", "荀彧", "荀灌"],
                "scenes": ["hero_selection"], "is_truncation": True,
            },
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(wcd, "STATS_PATH", stats)
    overrides = tmp_path / "overrides.json"
    monkeypatch.setattr(wcd, "_OVERRIDES_PATH", overrides)
    resets: list[int] = []
    dialog = WhitelistConfigDialog(
        ["荀勖", "荀彧", "荀灌", "夏侯惇"], reset_ocr_cache=lambda: resets.append(1),
    )

    # A+ 行排在最前，建议对已预填且展示确认历史
    assert dialog._pending_table.item(0, 0).text() == "夏侯怀"
    assert "怀=惇" in dialog._pending_table.item(0, 4).text()
    assert "夏侯惇" in dialog._pending_table.item(0, 2).text()
    dialog._pending_table.selectRow(0)  # 选中触发建议对预填
    assert dialog._source_edit.text() == "怀" and dialog._target_edit.text() == "惇"
    # C 类（截断）排在最后
    assert dialog._pending_table.item(1, 0).text() == "荀"

    # 选中药勖行并加入建议对：静态检查通过 → 写入 overrides → 缓存重置
    dialog._source_edit.setText("怀")
    dialog._target_edit.setText("惇")
    monkeypatch.setattr(wcd.QMessageBox, "information", lambda *a, **k: None)
    dialog._add_pair()
    assert wcd.load_overrides(overrides) == {"怀": "惇"}
    assert resets == [1]


def test_dialog_rejects_pair_conflicting_with_vocabulary(qapp, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wcd, "STATS_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(wcd, "_OVERRIDES_PATH", tmp_path / "overrides.json")
    warnings: list[str] = []
    monkeypatch.setattr(
        wcd.QMessageBox, "warning", lambda *a, **k: warnings.append(a[1]),
    )
    dialog = WhitelistConfigDialog(
        ["杨雨", "杨羽"], reset_ocr_cache=lambda: None,
    )
    dialog._source_edit.setText("雨")
    dialog._target_edit.setText("羽")
    dialog._add_pair()

    assert warnings == ["无法加入"]
    assert wcd.load_overrides(tmp_path / "overrides.json") == {}
