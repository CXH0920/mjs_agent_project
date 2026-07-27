"""官方榜单图片导入测试。"""

from __future__ import annotations

import csv

import cv2
import numpy as np

from src.business import official_data_import_service as import_module
from src.business.official_data_import_service import LAYOUTS, OfficialDataImportService


def test_template_rows_are_split_by_horizontal_lines(monkeypatch) -> None:
    panel = np.full((1_800, 240, 3), 255, dtype=np.uint8)
    lines = np.array([
        [[0, y, 239, y]]
        for y in range(30, 30 + 163 * 10, 10)
    ])
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *_args, **_kwargs: lines)

    boundaries = OfficialDataImportService._find_data_boundaries(
        panel, 1_000, LAYOUTS["exile"], 0,
    )

    assert len(boundaries) - 1 == 160


def test_missing_horizontal_line_is_restored_by_median_row_height() -> None:
    boundaries, repaired_ranks = OfficialDataImportService._restore_missing_boundaries(
        [0, 10, 20, 40, 50],
    )

    assert boundaries == [0, 10, 20, 30, 40, 50]
    assert repaired_ranks == {4}


def test_missing_rank_ocr_uses_table_row_rank_without_review() -> None:
    reasons = OfficialDataImportService._review_reasons(
        1,
        {"排名": ("", 0.0), "武将": ("曹仁", 0.99), "胜率": ("72.73%", 0.99)},
        "曹仁",
        {"排名": 1, "武将": "曹仁", "胜率": "72.73%"},
    )

    assert reasons == []


def test_hero_name_reuses_template_two_stage_correction() -> None:
    service = OfficialDataImportService(hero_names=["曹操"])

    name, confidence = service._normalize_name(("曹还", 0.80))

    assert name == "曹操"
    assert confidence == 0.80


def test_high_confidence_unknown_complete_name_is_corrected_and_reviewed() -> None:
    service = OfficialDataImportService(hero_names=["曹植", "曹仁", "曹丕", "曹操", "贾诩"])

    name, confidence = service._normalize_name(("贾谢", 0.996))
    common_surname_name, _ = service._normalize_name(("曹不", 0.996))
    reasons = OfficialDataImportService._review_reasons(
        1, {"排名": ("1", 0.99), "武将": ("贾谢", confidence)}, name, {"排名": 1, "武将": name},
    )

    assert (name, confidence) == ("贾诩", 0.996)
    assert common_surname_name == "曹丕"
    assert reasons == ["武将名称已由词表校正"]


def test_name_cell_prefers_complete_candidate_in_hero_list(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["郭隗"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        service, "_recognize_cell_candidates", lambda _: [("郭隗", 0.76), ("郭", 0.999)],
    )

    text, confidence = service._recognize_name_cell(cell)

    assert (text, confidence) == ("郭隗", 0.76)


def test_name_cell_corrects_single_character_result_from_glyphs(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["樊哙"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(service, "_recognize_cell_candidates", lambda _: [("樊", 0.999)])
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda _: ("樊会", 0.70))

    text, confidence = service._recognize_name_cell(cell)

    assert (text, confidence) == ("樊哙", 0.70)


def test_name_cell_uses_unique_prefix_when_glyph_recognition_fails(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["樊哙", "郭嘉"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(service, "_recognize_cell_candidates", lambda _: [("樊", 0.999)])
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda _: ("", 0.0))

    text, confidence = service._recognize_name_cell(cell)

    assert (text, confidence) == ("樊哙", 0.999)


def test_name_cell_uses_rare_character_engine_for_ambiguous_single_character(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["荀彧", "荀勖"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()
    statuses = []

    def recognize_candidates(_cell, engine=None):
        return [("菀勖", 0.82)] if engine is rare_character_engine else [("荀", 0.99)]

    monkeypatch.setattr(service, "_recognize_cell_candidates", recognize_candidates)
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(
        OfficialDataImportService,
        "_rare_char_engine",
        property(lambda _: rare_character_engine),
    )

    text, confidence = service._recognize_name_cell(cell, statuses.append)

    assert (text, confidence) == ("荀勖", 0.82)
    assert statuses == ["正在执行罕见字兜底识别"]


def test_name_cell_keeps_single_character_for_review_when_rare_engine_fails(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["荀彧", "荀勖"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()
    monkeypatch.setattr(service, "_recognize_cell_candidates", lambda _: [("荀", 0.99)])
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(OfficialDataImportService, "_rare_char_engine", property(lambda _: rare_character_engine))
    monkeypatch.setattr(
        service,
        "_recognize_name_with_engine",
        lambda *_: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    text, confidence = service._recognize_name_cell(cell)
    reasons = OfficialDataImportService._review_reasons(
        1, {"排名": ("1", 0.99), "武将": (text, confidence)}, text, {"排名": 1, "武将": text},
    )

    assert (text, confidence) == ("荀", 0.99)
    assert reasons == ["武将名称疑似缺字"]
    assert service._rare_char_engine_failed is True


def test_single_character_name_is_marked_for_review() -> None:
    reasons = OfficialDataImportService._review_reasons(
        1,
        {"排名": ("1", 0.99), "武将": ("郭", 0.99)},
        "郭",
        {"排名": 1, "武将": "郭"},
    )

    assert reasons == ["武将名称疑似缺字"]


def test_rate_cell_keeps_the_digit_next_to_the_column_separator() -> None:
    row = np.zeros((20, 486, 3), dtype=np.uint8)
    row[4:16, 331:349] = 255

    rate_cell = OfficialDataImportService._split_row_cells(
        row, ("排名", "武将", "胜率"), (0.0, 0.29, 0.69, 1.0),
    )["胜率"]
    glyphs = OfficialDataImportService._segment_glyphs(rate_cell)

    assert glyphs[0].shape[1] == 18


def test_rate_template_preparation_reports_each_processed_row(monkeypatch) -> None:
    service = OfficialDataImportService()
    panel = np.zeros((40, 100, 3), dtype=np.uint8)
    progress = []
    monkeypatch.setattr(service, "_recognize_cell", lambda *_: ("70.34%", 0.99))

    service._prepare_rate_templates(
        panel, [0, 20, 40], ("排名", "武将", "胜率"), (0.0, 0.29, 0.69, 1.0),
        lambda: progress.append(1),
    )

    assert len(progress) == 2


def test_two_column_layouts_keep_rank_and_hero_in_separate_cells() -> None:
    cases = (
        ("2v2", 1, (147, 199), (306, 393)),
        ("exile", 0, (139, 175), (307, 365)),
        ("exile", 1, (123, 175), (296, 355)),
    )
    for key, panel_index, rank_range, hero_range in cases:
        row = np.zeros((20, 486, 3), dtype=np.uint8)
        row[:, rank_range[0]:rank_range[1]] = 100
        row[:, hero_range[0]:hero_range[1]] = 200
        columns = LAYOUTS[key].columns[panel_index]
        breaks = LAYOUTS[key].column_breaks[panel_index]

        cells = OfficialDataImportService._split_row_cells(row, columns, breaks)

        assert set(np.unique(cells["排名"])) == {0, 100}
        assert set(np.unique(cells["武将"])) == {0, 200}


def test_import_overwrites_csv_and_keeps_abnormal_rows_for_review(tmp_path, monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["白起", "赵奢"])
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    panel = np.zeros((60, 100, 3), dtype=np.uint8)
    rows = iter([
        {"排名": ("1", 0.99), "武将": ("白起", 0.99), "胜率": ("70.34%", 0.99)},
        {"排名": ("2", 0.99), "武将": ("", 0.0), "胜率": ("", 0.0)},
        {"排名": ("1", 0.99), "武将": ("白起", 0.99)},
        {"排名": ("2", 0.99), "武将": ("赵奢", 0.99)},
    ])

    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "REVIEW_DIR", tmp_path / "review")
    monkeypatch.setattr(service, "_read_image", lambda _: image)
    monkeypatch.setattr(service, "_extract_panels", lambda *_: [(0, 0, panel), (100, 0, panel)])
    monkeypatch.setattr(service, "_find_data_boundaries", lambda *_: [0, 20, 40])
    monkeypatch.setattr(service, "_recognize_row", lambda *_: next(rows))
    def prepare_templates(*args):
        progress_callback = args[-1]
        progress_callback()
        progress_callback()
        return {1: ("70.34%", 0.99), 2: ("70.11%", 0.99)}, {}

    monkeypatch.setattr(service, "_prepare_rate_templates", prepare_templates)
    template_rates = iter([("70.34%", 0.99), ("70.11%", 0.99)])
    monkeypatch.setattr(service, "_recognize_rate_with_templates", lambda *_: next(template_rates))
    monkeypatch.setattr("src.data.win_rate_repository.clear_win_rate_cache", lambda: None)
    stale_calls = []
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", stale_calls.append)

    progress = []
    summary = service.import_file("2v2", tmp_path / "official.png", lambda current, total: progress.append((current, total)))
    output_path = tmp_path / "2v2胜率排行.csv"
    review_path = tmp_path / "2v2胜率排行_待复核.csv"
    attendance_path = tmp_path / "2v2出场排行.csv"

    assert summary["records"] == 4
    assert summary["reviews"] == 1
    assert list(csv.DictReader(output_path.open(encoding="utf-8"))) == [
        {"排名": "1", "武将": "白起", "胜率": "70.34%"},
        {"排名": "2", "武将": "", "胜率": "70.11%"},
    ]
    assert list(csv.DictReader(attendance_path.open(encoding="utf-8"))) == [
        {"排名": "1", "武将": "白起"},
        {"排名": "2", "武将": "赵奢"},
    ]
    assert len(list(csv.DictReader(review_path.open(encoding="utf-8")))) == 1
    assert not output_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in output_path.read_bytes()
    assert progress == [(0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]
    assert stale_calls == [True]
