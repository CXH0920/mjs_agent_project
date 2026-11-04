"""官方榜单单格识别策略测试（快通道 / 候选管线 / 逐字与罕见字兜底）。"""

from __future__ import annotations

import numpy as np
import pytest
from src.business.recognition.name_resolution import HeroNameResolver
from src.business.recognition.official_cell_recognition import OfficialCellRecognizer
from src.business.recognition.official_data_import_service import OfficialDataImportService
from src.business.recognition.official_ocr_engines import OfficialOcrEngines


def _fake_ocr_engine(lines):
    """按 rapidocr 完整流程结构（[box, (text, conf)]）吐出指定行的假引擎。"""
    box = [[0, 0], [1, 0], [1, 1], [0, 1]]

    class _Engine:
        def ocr(self, _image, cls=False):
            return [[[box, (text, confidence)] for text, confidence in lines]]

    return _Engine()


def _make_recognizer(hero_names: list[str], ocr_engine=None) -> OfficialCellRecognizer:
    return OfficialCellRecognizer(
        OfficialOcrEngines(ocr_engine), HeroNameResolver(hero_names),
    )


# ---------------------------------------------------------------
# 单格识别快通道（2x 单候选，命中即免 4x 双候选）
# ---------------------------------------------------------------

def test_fast_path_accepts_numeric_cells_without_full_candidates(monkeypatch) -> None:
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    for text in ("7", "191", "71.24%", "100.00%"):
        recognizer = _make_recognizer(
            ["曹仁"], ocr_engine=_fake_ocr_engine([(text, 0.99)]),
        )
        monkeypatch.setattr(
            recognizer, "recognize_cell_candidates",
            lambda *_args, **_kwargs: pytest.fail("快通道命中时不应回退双候选"),
        )

        assert recognizer.recognize_cell(cell) == (text, 0.99)


def test_fast_path_numeric_falls_back_on_non_numeric_or_low_confidence(monkeypatch) -> None:
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    for fast_lines in ([("7.", 0.99)], [("7", 0.85)], [("甲", 0.99)], [("7", 0.91), ("8", 0.90)]):
        recognizer = _make_recognizer(
            ["曹仁"], ocr_engine=_fake_ocr_engine(fast_lines),
        )
        monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda *_args: [("7", 0.95)])

        assert recognizer.recognize_cell(cell) == ("7", 0.95)


def test_fast_path_accepts_wordlist_exact_name(monkeypatch) -> None:
    recognizer = _make_recognizer(
        ["曹仁", "曹丕"], ocr_engine=_fake_ocr_engine([("曹仁", 0.97)]),
    )
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        recognizer, "recognize_cell_candidates",
        lambda *_args, **_kwargs: pytest.fail("快通道命中时不应回退双候选"),
    )

    assert recognizer.recognize_name_cell(cell) == ("曹仁", 0.97)


def test_fast_path_name_falls_back_when_not_in_wordlist(monkeypatch) -> None:
    recognizer = _make_recognizer(
        ["曹仁"], ocr_engine=_fake_ocr_engine([("新武将", 0.99)]),
    )
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda *_args: [("曹仁", 0.90)])

    assert recognizer.recognize_name_cell(cell) == ("曹仁", 0.90)


def test_fast_path_skipped_without_injected_engine(monkeypatch) -> None:
    # 单测/懒加载场景未注入引擎原始值，快通道不启用，行为与既有路径一致
    recognizer = _make_recognizer(["曹仁"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)

    assert recognizer._fast_path_cell(cell) == []
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda *_args: [("7", 0.90)])
    assert recognizer.recognize_cell(cell) == ("7", 0.90)


# ---------------------------------------------------------------
# 名称格识别策略（候选优先 / 冲突保留 / 逐字与罕见字兜底）
# ---------------------------------------------------------------

def test_name_cell_prefers_complete_candidate_in_hero_list(monkeypatch) -> None:
    recognizer = _make_recognizer(["郭隗"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        recognizer, "recognize_cell_candidates", lambda _: [("郭隗", 0.76), ("郭", 0.999)],
    )

    text, confidence = recognizer.recognize_name_cell(cell)

    assert (text, confidence) == ("郭隗", 0.76)


def test_name_cell_keeps_conflicting_exact_candidates_unresolved(monkeypatch) -> None:
    recognizer = _make_recognizer(["夏侯惇", "夏侯渊"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    statuses = []
    monkeypatch.setattr(
        recognizer,
        "recognize_cell_candidates",
        lambda _: [("夏侯惇", 0.76), ("夏侯渊", 0.92)],
    )
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(OfficialCellRecognizer, "_rare_char_engine", property(lambda _: None))

    text, confidence = recognizer.recognize_name_cell(cell, statuses.append)

    assert (text, confidence) == ("夏侯", 0.92)
    # 引擎不可用且未熔断（如复核开关关闭）：不谎报"正在执行"，亦无降级提示
    assert statuses == []


def test_name_cell_uses_rare_engine_for_ambiguous_compound_prefix(monkeypatch) -> None:
    recognizer = _make_recognizer(["夏侯惇", "夏侯渊"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()
    statuses = []
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda _: [("夏侯", 0.99)])
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(
        OfficialCellRecognizer,
        "_rare_char_engine",
        property(lambda _: rare_character_engine),
    )
    monkeypatch.setattr(recognizer, "_recognize_name_with_engine", lambda *_: ("夏侯惇", 0.80))

    text, confidence = recognizer.recognize_name_cell(cell, statuses.append)

    assert (text, confidence) == ("夏侯惇", 0.80)
    assert statuses == ["正在执行罕见字兜底识别"]


def test_name_cell_corrects_single_character_result_from_glyphs(monkeypatch) -> None:
    recognizer = _make_recognizer(["樊哙"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda _: [("樊", 0.999)])
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda _: ("樊会", 0.70))

    text, confidence = recognizer.recognize_name_cell(cell)

    assert (text, confidence) == ("樊哙", 0.70)


def test_name_cell_uses_unique_prefix_when_glyph_recognition_fails(monkeypatch) -> None:
    recognizer = _make_recognizer(["樊哙", "郭嘉"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda _: [("樊", 0.999)])
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda _: ("", 0.0))

    text, confidence = recognizer.recognize_name_cell(cell)

    assert (text, confidence) == ("樊哙", 0.999)


def test_name_cell_uses_rare_character_engine_for_ambiguous_single_character(monkeypatch) -> None:
    recognizer = _make_recognizer(["荀彧", "荀勖"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()
    statuses = []

    def recognize_candidates(_cell, engine=None):
        return [("菀勖", 0.82)] if engine is rare_character_engine else [("荀", 0.99)]

    monkeypatch.setattr(recognizer, "recognize_cell_candidates", recognize_candidates)
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(
        OfficialCellRecognizer,
        "_rare_char_engine",
        property(lambda _: rare_character_engine),
    )

    text, confidence = recognizer.recognize_name_cell(cell, statuses.append)

    assert (text, confidence) == ("荀勖", 0.82)
    assert statuses == ["正在执行罕见字兜底识别"]


def test_rare_character_engine_cannot_escape_primary_name_candidates(monkeypatch) -> None:
    recognizer = _make_recognizer(["卫青", "卫玠", "周瑜"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()

    def recognize_candidates(_cell, engine=None):
        return [("正瑜", 0.71)] if engine is rare_character_engine else [("卫", 0.99)]

    monkeypatch.setattr(recognizer, "recognize_cell_candidates", recognize_candidates)
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(
        OfficialCellRecognizer,
        "_rare_char_engine",
        property(lambda _: rare_character_engine),
    )

    text, confidence = recognizer.recognize_name_cell(cell)

    assert (text, confidence) == ("卫", 0.99)


def test_name_cell_keeps_single_character_for_review_when_rare_engine_fails(monkeypatch) -> None:
    # 识别器取自服务实例：本用例同时验证「单字保留待复核」的复核原因标注
    # （_review_reasons 属服务编排域，随服务构造取用）
    service = OfficialDataImportService(hero_names=["荀彧", "荀勖"])
    recognizer = service._cells
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    rare_character_engine = object()
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda _: [("荀", 0.99)])
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(OfficialCellRecognizer, "_rare_char_engine", property(lambda _: rare_character_engine))
    monkeypatch.setattr(
        recognizer,
        "_recognize_name_with_engine",
        lambda *_: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    text, confidence = recognizer.recognize_name_cell(cell)
    reasons = service._review_reasons(
        1, {"排名": ("1", 0.99), "武将": (text, confidence)}, text, {"排名": 1, "武将": text},
    )

    assert (text, confidence) == ("荀", 0.99)
    assert reasons == ["武将名称疑似缺字"]
    # 回归（TD-A5-001）：单次瞬时失败只损失本格兜底，不再整批熔断
    assert recognizer._rare_char_engine_failed is False


def test_name_cell_reports_degraded_status_when_circuit_tripped(monkeypatch) -> None:
    """罕见字熔断生效后，status_callback 明示降级而不是谎报"正在执行"。"""
    recognizer = _make_recognizer(["荀彧", "荀勖"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda _: [("荀", 0.99)])
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda *_: ("", 0.0))
    monkeypatch.setattr(OfficialCellRecognizer, "_rare_char_engine", property(lambda _: None))
    for _ in range(3):
        recognizer._engines.note_rare_char_failure()
    statuses: list[str] = []

    text, confidence = recognizer.recognize_name_cell(cell, statuses.append)

    assert (text, confidence) == ("荀", 0.99)
    assert statuses == ["罕见字兜底已降级，名称保留原结果待复核"]


def test_name_cell_runs_glyph_fallback_for_unknown_name(monkeypatch) -> None:
    recognizer = _make_recognizer(["夏侯惇"])
    cell = np.zeros((20, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(recognizer, "recognize_cell_candidates", lambda _: [("夏候", 0.79)])
    monkeypatch.setattr(recognizer, "recognize_name_glyphs", lambda _: ("夏侯", 0.85))

    text, confidence = recognizer.recognize_name_cell(cell)

    assert (text, confidence) == ("夏侯惇", 0.85)
