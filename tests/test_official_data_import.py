"""官方榜单图片导入测试。"""

from __future__ import annotations

import csv

import cv2
import numpy as np
import pytest

from src.business.recognition import official_data_import_service as import_module
from src.business.recognition.official_data_import_service import OfficialDataImportService
from src.ocr import official_board_parser
from src.ocr.official_board_parser import LAYOUTS


def test_template_rows_are_split_by_horizontal_lines(monkeypatch) -> None:
    panel = np.full((1_800, 240, 3), 255, dtype=np.uint8)
    lines = np.array([
        [[0, y, 239, y]]
        for y in range(30, 30 + 163 * 10, 10)
    ])
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *_args, **_kwargs: lines)

    boundaries = official_board_parser.find_data_boundaries(
        panel, 1_000, LAYOUTS["exile"], 0,
    )

    assert len(boundaries) - 1 == 160


def test_missing_horizontal_line_is_restored_by_median_row_height() -> None:
    boundaries, repaired_ranks = official_board_parser.restore_missing_boundaries(
        [0, 10, 20, 40, 50],
    )

    assert boundaries == [0, 10, 20, 30, 40, 50]
    assert repaired_ranks == {4}


def test_paged_layout_restores_missing_leading_rank_rows() -> None:
    panel = np.zeros((500, 486, 3), dtype=np.uint8)
    for center in (190, 238, 286, 334):
        panel[center - 6:center + 7, 30:48] = 255

    boundaries = official_board_parser.find_data_boundaries(
        panel, 600, official_board_parser.PAGED_LAYOUTS["2v2"], 0,
    )

    assert len(boundaries) - 1 == 5
    assert boundaries[0] == 118
    assert boundaries[-1] == 358


def test_missing_rank_ocr_uses_table_row_rank_without_review() -> None:
    service = OfficialDataImportService(hero_names=["曹仁"])
    reasons = service._review_reasons(
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
    reasons = service._review_reasons(
        1, {"排名": ("1", 0.99), "武将": ("贾谢", confidence)}, name, {"排名": 1, "武将": name},
    )

    assert (name, confidence) == ("贾诩", 0.996)
    assert common_surname_name == "曹丕"
    assert reasons == ["武将名称已由词表校正"]


def test_compound_surname_prefix_stays_unresolved_when_candidates_are_ambiguous() -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "夏侯渊", "夏侯婴", "夏侯霸"])

    prefix_name, _ = service._normalize_name(("夏侯", 0.83))
    complete_typo, _ = service._normalize_name(("夏侯怀", 0.71))

    assert prefix_name == "夏侯"
    assert complete_typo == "夏侯怀"
    assert service._unresolved_name_reason(prefix_name) == (
        "武将名称候选不唯一：夏侯惇/夏侯渊/夏侯婴/夏侯霸"
    )
    assert service._unresolved_name_reason(complete_typo) == (
        "武将名称候选不唯一：夏侯惇/夏侯渊/夏侯婴/夏侯霸"
    )


def test_compound_surname_guard_does_not_change_common_single_surname_correction() -> None:
    service = OfficialDataImportService(
        hero_names=["曹植", "曹仁", "曹丕", "曹操", "夏侯惇", "夏侯渊"],
    )

    name, confidence = service._normalize_name(("曹不", 0.98))

    assert (name, confidence) == ("曹丕", 0.98)


def test_unknown_complete_name_is_marked_as_missing_from_dictionary() -> None:
    service = OfficialDataImportService(hero_names=["曹操"])

    assert service._unresolved_name_reason("新武将") == "武将名称未命中词表"


def test_name_cell_prefers_complete_candidate_in_hero_list(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["郭隗"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        service, "_recognize_cell_candidates", lambda _: [("郭隗", 0.76), ("郭", 0.999)],
    )

    text, confidence = service._recognize_name_cell(cell)

    assert (text, confidence) == ("郭隗", 0.76)


def test_name_cell_keeps_conflicting_exact_candidates_unresolved(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "夏侯渊"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    statuses = []
    monkeypatch.setattr(
        service,
        "_recognize_cell_candidates",
        lambda _: [("夏侯惇", 0.76), ("夏侯渊", 0.92)],
    )
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(OfficialDataImportService, "_rare_char_engine", property(lambda _: None))

    text, confidence = service._recognize_name_cell(cell, statuses.append)

    assert (text, confidence) == ("夏侯", 0.92)
    assert statuses == ["正在执行罕见字兜底识别"]


def test_name_cell_uses_rare_engine_for_ambiguous_compound_prefix(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "夏侯渊"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()
    statuses = []
    monkeypatch.setattr(service, "_recognize_cell_candidates", lambda _: [("夏侯", 0.99)])
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(
        OfficialDataImportService,
        "_rare_char_engine",
        property(lambda _: rare_character_engine),
    )
    monkeypatch.setattr(service, "_recognize_name_with_engine", lambda *_: ("夏侯惇", 0.80))

    text, confidence = service._recognize_name_cell(cell, statuses.append)

    assert (text, confidence) == ("夏侯惇", 0.80)
    assert statuses == ["正在执行罕见字兜底识别"]


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


def test_rare_character_engine_cannot_escape_primary_name_candidates(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["卫青", "卫玠", "周瑜"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()

    def recognize_candidates(_cell, engine=None):
        return [("正瑜", 0.71)] if engine is rare_character_engine else [("卫", 0.99)]

    monkeypatch.setattr(service, "_recognize_cell_candidates", recognize_candidates)
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(
        OfficialDataImportService,
        "_rare_char_engine",
        property(lambda _: rare_character_engine),
    )

    text, confidence = service._recognize_name_cell(cell)

    assert (text, confidence) == ("卫", 0.99)


def test_batch_uniqueness_resolves_the_only_unused_name_candidate() -> None:
    service = OfficialDataImportService(hero_names=["卫青", "卫玠", "周瑜"])
    batch = {
        "records": [
            {"排名": 1, "武将": "卫"},
            {"排名": 2, "武将": "卫青"},
            {"排名": 3, "武将": "周瑜"},
        ],
        "reviews": [{"期望排名": 1, "异常原因": "武将名称候选不唯一：卫青/卫玠"}],
    }

    service._resolve_batch_names(batch)

    assert batch["records"][0]["武将"] == "卫玠"
    assert batch["reviews"][0]["异常原因"].endswith("已按榜单唯一性由卫补全为卫玠")


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
    reasons = service._review_reasons(
        1, {"排名": ("1", 0.99), "武将": (text, confidence)}, text, {"排名": 1, "武将": text},
    )

    assert (text, confidence) == ("荀", 0.99)
    assert reasons == ["武将名称疑似缺字"]
    assert service._rare_char_engine_failed is True


def test_single_character_name_is_marked_for_review() -> None:
    service = OfficialDataImportService(hero_names=["郭"])
    reasons = service._review_reasons(
        1,
        {"排名": ("1", 0.99), "武将": ("郭", 0.99)},
        "郭",
        {"排名": 1, "武将": "郭"},
    )

    assert reasons == ["武将名称疑似缺字"]


def test_rate_cell_keeps_the_digit_next_to_the_column_separator() -> None:
    row = np.zeros((20, 486, 3), dtype=np.uint8)
    row[4:16, 331:349] = 255

    rate_cell = official_board_parser.split_row_cells(
        row, ("排名", "武将", "胜率"), (0.0, 0.29, 0.69, 1.0),
    )["胜率"]
    glyphs = official_board_parser.segment_glyphs(rate_cell)

    assert glyphs[0].shape[1] == 18


def test_rate_template_preparation_reports_each_processed_row(monkeypatch) -> None:
    service = OfficialDataImportService()
    panel = np.zeros((40, 100, 3), dtype=np.uint8)
    progress = []
    monkeypatch.setattr(service, "_recognize_cell", lambda *_: ("70.34%", 0.99))

    official_board_parser.prepare_rate_templates(
        panel, [0, 20, 40], ("排名", "武将", "胜率"), (0.0, 0.29, 0.69, 1.0),
        service._recognize_cell, lambda: progress.append(1),
    )

    assert len(progress) == 2


def test_rank_digit_templates_use_the_page_global_start(monkeypatch) -> None:
    glyph = np.ones((10, 6), dtype=np.uint8)
    monkeypatch.setattr(official_board_parser, "segment_glyphs", lambda _: [glyph, glyph])

    templates = official_board_parser.build_rank_digit_templates(
        np.zeros((20, 100, 3), dtype=np.uint8),
        [0, 20],
        ("排名", "武将", "胜率"),
        (0.0, 0.29, 0.69, 1.0),
        rank_start=51,
    )

    assert len(templates["5"]) == 1
    assert len(templates["1"]) == 1
    assert not templates["0"]


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

        cells = official_board_parser.split_row_cells(row, columns, breaks)

        assert set(np.unique(cells["排名"])) == {0, 100}
        assert set(np.unique(cells["武将"])) == {0, 200}


def test_import_keeps_formal_csv_when_a_name_cannot_be_confirmed(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr(official_board_parser, "read_image", lambda _: image)
    monkeypatch.setattr(
        official_board_parser, "extract_panels", lambda *_: [(0, 0, panel), (100, 0, panel)],
    )
    monkeypatch.setattr(official_board_parser, "find_data_boundaries", lambda *_: [0, 20, 40])
    monkeypatch.setattr(service, "_recognize_row", lambda *_: next(rows))
    def prepare_templates(*args):
        progress_callback = args[-2]
        progress_callback()
        progress_callback()
        return {1: ("70.34%", 0.99), 2: ("70.11%", 0.99)}, {}

    monkeypatch.setattr(official_board_parser, "prepare_rate_templates", prepare_templates)
    template_rates = iter([("70.34%", 0.99), ("70.11%", 0.99)])
    monkeypatch.setattr(
        official_board_parser, "recognize_rate_with_templates", lambda *_: next(template_rates),
    )
    monkeypatch.setattr("src.data.win_rate_repository.clear_win_rate_cache", lambda: None)
    stale_calls = []
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", stale_calls.append)

    progress = []
    output_path = tmp_path / "2v2胜率排行.csv"
    review_path = tmp_path / "2v2胜率排行_待复核.csv"
    attendance_path = tmp_path / "2v2出场排行.csv"
    original = "排名,武将,胜率\n1,旧数据,50.00%\n"
    output_path.write_text(original, encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match="存在未确认武将：2:空值"):
        service.import_file(
            "2v2", tmp_path / "official.png",
            lambda current, total: progress.append((current, total)),
        )

    assert output_path.read_text(encoding="utf-8") == original
    assert not attendance_path.exists()
    assert len(list(csv.DictReader(review_path.open(encoding="utf-8")))) == 1
    review = list(csv.DictReader(review_path.open(encoding="utf-8")))[0]
    assert review["来源图片"].endswith("official.png")
    assert review["页序号"] == "1"
    assert not output_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in output_path.read_bytes()
    assert progress == [(0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]
    assert stale_calls == []


def test_multiple_pages_merge_each_output_with_global_ranks(tmp_path, monkeypatch) -> None:
    names = {1: "甲一", 2: "甲二", 3: "甲三", 4: "甲四", 5: "甲五", 6: "甲六"}
    service = OfficialDataImportService(hero_names=list(names.values()))
    panel = np.zeros((60, 100, 3), dtype=np.uint8)
    counters = {}

    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "REVIEW_DIR", tmp_path / "review")
    monkeypatch.setattr(
        official_board_parser,
        "read_image",
        lambda path: np.full((200, 200, 3), 1 if path.stem == "page1" else 4, dtype=np.uint8),
    )
    monkeypatch.setattr(
        official_board_parser, "detect_layout", lambda *_: LAYOUTS["2v2"],
    )
    monkeypatch.setattr(
        official_board_parser,
        "extract_panels",
        lambda image, _layout: [
            (0, 0, np.full_like(panel, int(image[0, 0, 0]))),
            (100, 0, np.full_like(panel, int(image[0, 0, 0]))),
        ],
    )
    monkeypatch.setattr(official_board_parser, "find_data_boundaries", lambda *_: [0, 20, 40, 60])

    def recognize_row(row, columns, *_args):
        page_start = int(row[0, 0, 0])
        key = (page_start, columns)
        counters[key] = counters.get(key, 0) + 1
        rank = page_start + counters[key] - 1
        fields = {"排名": (str(rank), 0.99), "武将": (names[rank], 0.99)}
        if "胜率" in columns:
            fields["胜率"] = ("50.00%", 0.99)
        return fields

    monkeypatch.setattr(service, "_recognize_row", recognize_row)

    def prepare_templates(*args):
        for _ in range(3):
            args[-2]()
        return {rank: ("50.00%", 0.99) for rank in range(1, 4)}, {}

    monkeypatch.setattr(official_board_parser, "prepare_rate_templates", prepare_templates)
    monkeypatch.setattr(
        official_board_parser, "recognize_rate_with_templates", lambda *_: ("50.00%", 0.99),
    )
    monkeypatch.setattr("src.data.win_rate_repository.clear_win_rate_cache", lambda: None)
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", lambda *_: None)

    summary = service.import_pages("2v2", [tmp_path / "page1.png", tmp_path / "page2.png"])

    win_rows = list(csv.DictReader((tmp_path / "2v2胜率排行.csv").open(encoding="utf-8")))
    attendance_rows = list(csv.DictReader((tmp_path / "2v2出场排行.csv").open(encoding="utf-8")))
    assert summary["pages"] == 2
    assert [row["排名"] for row in win_rows] == ["1", "2", "3", "4", "5", "6"]
    assert [row["排名"] for row in attendance_rows] == ["1", "2", "3", "4", "5", "6"]


def test_out_of_order_pages_fail_before_writing_csv(tmp_path, monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["甲一", "甲二", "甲三"])
    panel = np.full((60, 100, 3), 4, dtype=np.uint8)
    calls = 0

    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(official_board_parser, "read_image", lambda _: panel)
    monkeypatch.setattr(official_board_parser, "detect_layout", lambda *_: LAYOUTS["2v2"])
    monkeypatch.setattr(
        official_board_parser, "extract_panels", lambda *_: [(0, 0, panel), (100, 0, panel)],
    )
    monkeypatch.setattr(official_board_parser, "find_data_boundaries", lambda *_: [0, 20, 40, 60])

    def recognize_row(_row, columns, *_args):
        nonlocal calls
        calls += 1
        local_rank = (calls - 1) % 3
        fields = {"排名": (str(4 + local_rank), 0.99), "武将": ("甲一", 0.99)}
        if "胜率" in columns:
            fields["胜率"] = ("50.00%", 0.99)
        return fields

    monkeypatch.setattr(service, "_recognize_row", recognize_row)

    def prepare_templates(*args):
        for _ in range(3):
            args[-2]()
        return {rank: ("50.00%", 0.99) for rank in range(1, 4)}, {}

    monkeypatch.setattr(official_board_parser, "prepare_rate_templates", prepare_templates)
    monkeypatch.setattr(
        official_board_parser, "recognize_rate_with_templates", lambda *_: ("50.00%", 0.99),
    )

    with pytest.raises(ValueError, match="期望从 1 开始，识别为从 4 开始"):
        service.import_pages("2v2", [tmp_path / "page2.png"])

    assert not (tmp_path / "2v2胜率排行.csv").exists()
    assert not (tmp_path / "2v2出场排行.csv").exists()

# ---------------------------------------------------------------

# ---------------------------------------------------------------
# OCR 混淆字对抵底 / 跨榜单一致性 / 失败会话持久化
# ---------------------------------------------------------------


def test_confusion_swap_corrects_swapped_compound_surname() -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇"])

    assert service._normalize_name(("夏候", 0.79)) == ("夏侯惇", 0.79)
    assert service._normalize_name(("夏候怀", 0.70)) == ("夏侯惇", 0.70)


def test_confusion_swap_review_reason() -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇"])

    name, confidence = service._normalize_name(("夏候怀", 0.70))
    reasons = service._review_reasons(
        28, {"排名": ("28", 0.99), "武将": ("夏候怀", 0.70)}, name, {"排名": 28, "武将": name},
    )

    assert name == "夏侯惇"
    assert reasons[0] == "武将名称已由词表校正（混淆字对）"


def test_confusion_swap_skips_when_reachable_is_ambiguous() -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "夏侯渊"])

    assert service._normalize_name(("夏候怀", 0.70)) == ("夏候怀", 0.70)
    assert service._normalize_name(("夏侯怀", 0.70)) == ("夏侯怀", 0.70)


def test_confusion_swap_does_not_touch_known_name() -> None:
    service = OfficialDataImportService(hero_names=["侯嬴", "夏侯惇"])

    assert service._normalize_name(("侯嬴", 0.99)) == ("侯嬴", 0.99)
    assert service._corrected_via_confusion_swap("侯嬴", "侯嬴") is False


def test_plain_wordlist_correction_has_no_confusion_marker() -> None:
    service = OfficialDataImportService(hero_names=["曹丕"])

    name, _confidence = service._normalize_name(("曹不", 0.98))
    reasons = service._review_reasons(
        1, {"排名": ("1", 0.99), "武将": ("曹不", 0.98)}, name, {"排名": 1, "武将": name},
    )

    assert name == "曹丕"
    assert reasons[0] == "武将名称已由词表校正"


def test_name_cell_runs_glyph_fallback_for_unknown_name(monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(service, "_recognize_cell_candidates", lambda _: [("夏候", 0.79)])
    monkeypatch.setattr(service, "_recognize_name_glyphs", lambda _: ("夏侯", 0.85))

    text, confidence = service._recognize_name_cell(cell)

    assert (text, confidence) == ("夏侯惇", 0.85)


def test_cross_output_resolution_aligns_unknown_names() -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "白起"])
    outputs = {
        "2v2胜率排行.csv": {
            "records": [{"排名": 1, "武将": "夏候"}, {"排名": 2, "武将": "白起"}],
            "reviews": [{"期望排名": 1, "异常原因": "武将名称未命中词表"}],
        },
        "2v2出场排行.csv": {
            "records": [{"排名": 1, "武将": "夏候怀"}, {"排名": 2, "武将": "白起"}],
            "reviews": [{"期望排名": 1, "异常原因": "武将名称未命中词表"}],
        },
    }

    service._resolve_names_across_outputs(outputs)

    assert outputs["2v2胜率排行.csv"]["records"][0]["武将"] == "夏侯惇"
    assert outputs["2v2出场排行.csv"]["records"][0]["武将"] == "夏侯惇"
    assert "跨榜单一致性" in outputs["2v2胜率排行.csv"]["reviews"][0]["异常原因"]
    assert service._validate_output_names(outputs) == []


def test_cross_output_resolution_skips_when_no_common_candidate() -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "夏侯渊"])
    outputs = {
        "2v2胜率排行.csv": {
            "records": [{"排名": 1, "武将": "夏侯怀"}, {"排名": 2, "武将": "白起"}],
            "reviews": [{"期望排名": 1, "异常原因": "武将名称候选不唯一"}],
        },
        "2v2出场排行.csv": {
            "records": [{"排名": 1, "武将": "夏侯惇"}, {"排名": 2, "武将": "白起"}],
            "reviews": [],
        },
    }

    service._resolve_names_across_outputs(outputs)

    assert outputs["2v2胜率排行.csv"]["records"][0]["武将"] == "夏侯怀"


def test_save_pending_session_roundtrip(tmp_path) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇"])
    outputs = {
        "2v2胜率排行.csv": {
            "review_name": "2v2胜率排行_待复核.csv",
            "columns": ("排名", "武将", "胜率"),
            "records": [{"排名": 1, "武将": "夏候", "胜率": "54.40%"}],
            "reviews": [{"期望排名": 1, "OCR名称": "夏候", "异常原因": "未命中", "行截图路径": "x.png"}],
        },
    }

    session_path = service._save_pending_session(
        "2v2", ["a.png"], 1, ["paged"], ["校验失败"], outputs, tmp_path / "pending.json",
    )
    payload = import_module.load_pending_session(session_path)

    assert payload is not None
    assert payload["outputs"]["2v2胜率排行.csv"]["records"][0]["武将"] == "夏候"


def test_load_pending_session_returns_none_when_missing(tmp_path) -> None:
    assert import_module.load_pending_session(tmp_path / "missing.json") is None


def test_apply_reviewed_records_writes_fixed_records(tmp_path, monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "白起"])
    pending = {
        "outputs": {
            "2v2胜率排行.csv": {
                "review_name": "2v2胜率排行_待复核.csv",
                "records": [{"排名": 1, "武将": "夏候", "胜率": "54.40%"}],
                "reviews": [],
            },
        },
    }
    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", lambda *_: None)
    monkeypatch.setattr("src.data.win_rate_repository.clear_win_rate_cache", lambda: None)
    cleared: list = []
    monkeypatch.setattr(import_module, "clear_pending_session", cleared.append)

    summary = service.apply_reviewed_records(
        pending, {("2v2胜率排行.csv", 1): "夏侯惇"},
    )

    rows = list(csv.DictReader((tmp_path / "2v2胜率排行.csv").open(encoding="utf-8")))
    assert rows[0]["武将"] == "夏侯惇"
    assert summary["records"] == 1
    assert cleared == [None]


def test_apply_reviewed_records_rejects_unknown_after_fix(tmp_path, monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "白起"])
    pending = {
        "outputs": {
            "2v2胜率排行.csv": {
                "review_name": "2v2胜率排行_待复核.csv",
                "records": [{"排名": 1, "武将": "夏候", "胜率": "54.40%"}],
                "reviews": [],
            },
        },
    }
    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", lambda *_: None)

    with pytest.raises(ValueError, match="存在未确认武将"):
        service.apply_reviewed_records(pending, {})

    assert not (tmp_path / "2v2胜率排行.csv").exists()


def test_apply_reviewed_records_rejects_duplicate_after_fix(tmp_path, monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "白起"])
    pending = {
        "outputs": {
            "2v2胜率排行.csv": {
                "review_name": "2v2胜率排行_待复核.csv",
                "records": [
                    {"排名": 1, "武将": "夏候", "胜率": "54.40%"},
                    {"排名": 2, "武将": "白起", "胜率": "53.00%"},
                ],
                "reviews": [],
            },
        },
    }
    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", lambda *_: None)

    with pytest.raises(ValueError, match="存在重复武将"):
        service.apply_reviewed_records(pending, {("2v2胜率排行.csv", 1): "白起"})

    assert not (tmp_path / "2v2胜率排行.csv").exists()

def test_import_succeeds_when_confusion_swap_resolves_names(tmp_path, monkeypatch) -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "白起"])
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    panel = np.zeros((60, 100, 3), dtype=np.uint8)
    rows = iter([
        {"排名": ("1", 0.99), "武将": ("夏候", 0.79), "胜率": ("54.40%", 0.99)},
        {"排名": ("2", 0.99), "武将": ("白起", 0.99), "胜率": ("50.00%", 0.99)},
        {"排名": ("1", 0.99), "武将": ("夏候怀", 0.70)},
        {"排名": ("2", 0.99), "武将": ("白起", 0.99)},
    ])
    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "REVIEW_DIR", tmp_path / "review")
    monkeypatch.setattr(official_board_parser, "read_image", lambda _: image)
    monkeypatch.setattr(
        official_board_parser, "extract_panels", lambda *_: [(0, 0, panel), (100, 0, panel)],
    )
    monkeypatch.setattr(official_board_parser, "find_data_boundaries", lambda *_: [0, 20, 40])
    monkeypatch.setattr(service, "_recognize_row", lambda *_: next(rows))

    def prepare_templates(*args):
        progress_callback = args[-2]
        progress_callback()
        progress_callback()
        return {1: ("54.40%", 0.99), 2: ("50.00%", 0.99)}, {}

    monkeypatch.setattr(official_board_parser, "prepare_rate_templates", prepare_templates)
    template_rates = iter([("54.40%", 0.99), ("50.00%", 0.99)])
    monkeypatch.setattr(
        official_board_parser, "recognize_rate_with_templates", lambda *_: next(template_rates),
    )
    monkeypatch.setattr("src.data.win_rate_repository.clear_win_rate_cache", lambda: None)
    stale: list = []
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", stale.append)

    summary = service.import_file("2v2", tmp_path / "official.png")

    win = list(csv.DictReader((tmp_path / "2v2胜率排行.csv").open(encoding="utf-8")))
    appear = list(csv.DictReader((tmp_path / "2v2出场排行.csv").open(encoding="utf-8")))
    assert win[0]["武将"] == "夏侯惇"
    assert appear[0]["武将"] == "夏侯惇"
    assert win[0]["胜率"] == "54.40%"
    assert summary["records"] == 4
    assert stale == [True]
    review = list(csv.DictReader((tmp_path / "2v2胜率排行_待复核.csv").open(encoding="utf-8")))[0]
    assert "混淆字对" in review["异常原因"]

# ---------------------------------------------------------------
# 放逐榜页末右栏不满的行数校验放宽
# ---------------------------------------------------------------


def test_validate_exile_row_counts_accepts_full_and_short_right() -> None:
    official_board_parser.validate_exile_row_counts([50, 50])
    official_board_parser.validate_exile_row_counts([50, 20])


def test_validate_exile_row_counts_rejects_invalid_layouts() -> None:
    with pytest.raises(ValueError, match="行数异常"):
        official_board_parser.validate_exile_row_counts([20, 50])
    with pytest.raises(ValueError, match="行数异常"):
        official_board_parser.validate_exile_row_counts([30, 30])


def test_detect_layout_accepts_exile_short_right_panel(monkeypatch) -> None:
    image = np.zeros((2657, 1080, 3), dtype=np.uint8)
    panel = np.zeros((100, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        official_board_parser, "extract_panels", lambda *_: [(0, 0, panel), (100, 0, panel)],
    )

    def fake_boundaries(_panel, _image_height, _layout, panel_index):
        rows = 50 if panel_index == 0 else 20
        return list(range(0, (rows + 1) * 20, 20))

    monkeypatch.setattr(official_board_parser, "find_data_boundaries", fake_boundaries)

    layout = official_board_parser.detect_layout(image, "exile")

    assert layout.variant == "paged"


def test_detect_layout_rejects_2v2_uneven_panels(monkeypatch) -> None:
    image = np.zeros((2657, 1080, 3), dtype=np.uint8)
    panel = np.zeros((100, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        official_board_parser, "extract_panels", lambda *_: [(0, 0, panel), (100, 0, panel)],
    )

    def fake_boundaries(_panel, _image_height, _layout, panel_index):
        rows = 50 if panel_index == 0 else 49
        return list(range(0, (rows + 1) * 20, 20))

    monkeypatch.setattr(official_board_parser, "find_data_boundaries", fake_boundaries)

    with pytest.raises(ValueError, match="无法识别2v2榜单版式"):
        official_board_parser.detect_layout(image, "2v2")


def test_detect_layout_rejects_exile_right_heavier_panel(monkeypatch) -> None:
    image = np.zeros((2657, 1080, 3), dtype=np.uint8)
    panel = np.zeros((100, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        official_board_parser, "extract_panels", lambda *_: [(0, 0, panel), (100, 0, panel)],
    )

    def fake_boundaries(_panel, _image_height, _layout, panel_index):
        rows = 20 if panel_index == 0 else 50
        return list(range(0, (rows + 1) * 20, 20))

    monkeypatch.setattr(official_board_parser, "find_data_boundaries", fake_boundaries)

    with pytest.raises(ValueError, match="无法识别exile榜单版式"):
        official_board_parser.detect_layout(image, "exile")


def test_import_pages_merges_exile_short_right_panel_pages(tmp_path, monkeypatch) -> None:
    import json as _json

    all_names = [
        hero["name"]
        for hero in _json.loads((import_module.DATA_DIR / "heroes.json").read_text(encoding="utf-8"))
    ][:170]
    names = {rank: all_names[rank - 1] for rank in range(1, 171)}
    service = OfficialDataImportService(hero_names=all_names)
    panel = np.zeros((1100, 100, 3), dtype=np.uint8)

    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "REVIEW_DIR", tmp_path / "review")
    monkeypatch.setattr(
        official_board_parser,
        "read_image",
        lambda path: np.full((200, 200, 3), 1 if path.stem == "page1" else 4, dtype=np.uint8),
    )

    def fake_extract_panels(image, _layout):
        page = int(image[0, 0, 0])
        return [
            (0, 0, np.full_like(panel, page * 10)),
            (100, 0, np.full_like(panel, page * 10 + 1)),
        ]

    monkeypatch.setattr(official_board_parser, "extract_panels", fake_extract_panels)

    def fake_boundaries(panel, _image_height, _layout, _panel_index):
        rows = {10: 50, 11: 50, 40: 50, 41: 20}[int(panel[0, 0, 0])]
        return list(range(0, (rows + 1) * 20, 20))

    monkeypatch.setattr(official_board_parser, "find_data_boundaries", fake_boundaries)
    counters: dict[int, int] = {}

    def recognize_row(row, _columns, *_args):
        value = int(row[0, 0, 0])
        counters[value] = counters.get(value, 0) + 1
        base = {10: 1, 11: 51, 40: 101, 41: 151}[value]
        rank = base + counters[value] - 1
        return {"排名": (str(rank), 0.99), "武将": (names[rank], 0.99)}

    monkeypatch.setattr(service, "_recognize_row", recognize_row)

    summary = service.import_pages("exile", [tmp_path / "page1.png", tmp_path / "page2.png"])

    rows = list(csv.DictReader((tmp_path / "武将放逐.csv").open(encoding="utf-8")))
    assert len(rows) == 170
    assert [int(row["排名"]) for row in rows] == list(range(1, 171))
    assert rows[-1]["武将"] == names[170]
    assert summary["records"] == 170
