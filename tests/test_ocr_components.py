"""OCR 拆分组件的边界测试。"""

from __future__ import annotations

import json
import logging
import sys
from types import ModuleType

import numpy as np

from src.ocr.character_feature_repository import CharacterFeatureRepository
from src.ocr.character_similarity import CharacterSimilarityService
from src.ocr.image_preprocessor import ImagePreprocessor
from src.ocr.recognizer import GeneralRecognizer
from scripts.build_character_feature_cache import COMMON_OCR_CONFUSION_CHARACTERS, required_characters


def test_image_preprocessor_outputs_tripled_grayscale_image() -> None:
    roi = np.full((4, 5, 3), 128, dtype=np.uint8)

    prepared = ImagePreprocessor.preprocess_roi(roi)

    assert prepared.shape == (12, 15)
    assert prepared.dtype == np.uint8


def test_character_feature_repository_saves_injected_cache_path_with_utf8_lf(tmp_path) -> None:
    cache_path = tmp_path / "char_info_cache.json"
    cache_path.write_text(
        json.dumps({"曹": {"pinyin": "cao"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    repository = CharacterFeatureRepository(cache_path)

    entries = repository.load()
    entries["操"] = {"pinyin": "cao", "total_strokes": "16"}
    repository.save()

    content = cache_path.read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in content
    assert json.loads(content.decode("utf-8"))["操"]["total_strokes"] == "16"


def test_character_feature_repository_reads_unihan_csv_from_options_destination(tmp_path, monkeypatch) -> None:
    csv_path = tmp_path / "export" / "unihan.csv"
    csv_path.parent.mkdir()
    csv_path.write_text(
        "char,kCangjie,kFourCornerCode\n曹,HV,5500.0\n",
        encoding="utf-8",
    )

    class FakeOptions:
        def __init__(self, **_kwargs) -> None:
            self.destination = csv_path

    class FakePackager:
        def __init__(self, _options) -> None:
            pass

        def export(self) -> None:
            return None

    module = ModuleType("unihan_etl.core")
    module.Options = FakeOptions
    module.Packager = FakePackager
    monkeypatch.setitem(sys.modules, "unihan_etl.core", module)
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")

    assert repository._query_unihan("曹") == {"cangjie": "HV", "four_corner": "5500."}


def test_character_feature_repository_logs_pinyin_warmup_failure(tmp_path, monkeypatch, caplog) -> None:
    module = ModuleType("pypinyin")
    module.Style = type("Style", (), {"NORMAL": "normal"})

    def fail_pinyin(*_args, **_kwargs):
        raise RuntimeError("pinyin unavailable")

    module.pinyin = fail_pinyin
    monkeypatch.setitem(sys.modules, "pypinyin", module)
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    caplog.set_level(logging.WARNING)

    repository.warmup()

    assert repository._pinyin_available is False
    assert "pypinyin 预热失败" in caplog.text


def test_character_feature_repository_warms_missing_wordlist_characters(tmp_path, monkeypatch) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load()["曹"] = {"pinyin": "cao"}
    built: list[str] = []
    monkeypatch.setattr(
        repository,
        "_build_feature",
        lambda char: built.append(char) or {"pinyin": char},
    )

    assert repository.warmup_characters(["曹", "操", "操"]) == 1
    assert built == ["操"]
    assert repository.get_feature("操") == {"pinyin": "操"}


def test_character_feature_cache_character_set_includes_names_and_common_misreads(tmp_path) -> None:
    heroes_path = tmp_path / "heroes.json"
    heroes_path.write_text(
        json.dumps([{"name": "乐毅"}, {"name": "嬴政"}], ensure_ascii=False),
        encoding="utf-8",
    )

    assert required_characters(heroes_path) == set("乐毅嬴政") | set(COMMON_OCR_CONFUSION_CHARACTERS)


def test_static_character_feature_cache_covers_current_hero_names_and_common_misreads() -> None:
    entries = CharacterFeatureRepository().load()

    assert required_characters() <= entries.keys()


def test_character_feature_repository_logs_pinyin_query_failure_once(tmp_path, monkeypatch, caplog) -> None:
    module = ModuleType("pypinyin")
    module.Style = type("Style", (), {"NORMAL": "normal"})

    def fail_pinyin(*_args, **_kwargs):
        raise RuntimeError("pinyin unavailable")

    module.pinyin = fail_pinyin
    monkeypatch.setitem(sys.modules, "pypinyin", module)
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    caplog.set_level(logging.WARNING)

    assert repository._get_pinyin("曹") == ""
    assert repository._get_pinyin("操") == ""

    assert repository._pinyin_available is False
    assert caplog.text.count("pypinyin 查询失败") == 1


def test_character_feature_repository_logs_radical_query_failure(tmp_path, monkeypatch, caplog) -> None:
    class BrokenRadical:
        def trans_ch(self, _char: str) -> str:
            raise RuntimeError("radical unavailable")

    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository._radical_client = BrokenRadical()
    monkeypatch.setattr(repository, "_query_unihan", lambda _char: {})
    monkeypatch.setattr(repository, "_get_pinyin", lambda _char: "")
    monkeypatch.setattr(repository, "_get_stroke", lambda _char: 0)
    caplog.set_level(logging.WARNING)

    feature = repository._build_feature("曹")

    assert feature["radical"] == ""
    assert "cnradical 查询失败" in caplog.text
    assert "曹" in caplog.text


def test_character_similarity_service_corrects_with_injected_repository(tmp_path) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load().update({
        "曹": {"four_corner": "5500", "cangjie": "HV", "radical": "曰", "pinyin": "cao", "total_strokes": "11"},
        "不": {"four_corner": "1090", "cangjie": "MF", "radical": "一", "pinyin": "bu", "total_strokes": "4"},
        "丕": {"four_corner": "1090", "cangjie": "MF", "radical": "一", "pinyin": "pi", "total_strokes": "5"},
        "仁": {"four_corner": "2121", "cangjie": "OMM", "radical": "人", "pinyin": "ren", "total_strokes": "4"},
    })
    service = CharacterSimilarityService(repository)

    assert service.correct_hero_name("曹不", ["曹仁", "曹丕"]) == "曹丕"


def test_character_similarity_requires_safe_glyph_for_single_substitution() -> None:
    service = CharacterSimilarityService()

    assert service.is_safe_single_substitution("赢政", "嬴政")
    assert not service.is_safe_single_substitution("正瑜", "周瑜")


def test_general_recognizer_delegates_preprocessing_and_name_correction() -> None:
    calls: list[object] = []

    class FakePreprocessor:
        def preprocess_roi(self, roi):
            calls.append(("preprocess", roi.shape))
            return np.array([[42]], dtype=np.uint8)

    class FakeCorrector:
        def correct_hero_name(self, text, hero_names):
            calls.append(("correct", text, hero_names))
            return "曹操"

        def warmup(self) -> None:
            return None

    class FakeEngine:
        def ocr(self, image, cls):
            calls.append(("ocr", image.tolist(), cls))
            return [[[None, ("曹还", 0.8)]]]

    recognizer = GeneralRecognizer(
        hero_names=["曹操"],
        preprocessor=FakePreprocessor(),
        similarity_service=FakeCorrector(),
    )
    recognizer._ocr = FakeEngine()

    assert recognizer._recognize_single(np.zeros((2, 3, 3), dtype=np.uint8), 1) == ("曹操", 0.8)
    assert calls == [
        ("preprocess", (2, 3, 3)),
        ("ocr", [[42]], False),
        ("correct", "曹还", ["曹操"]),
    ]


def test_general_recognizer_corrects_high_confidence_candidate_and_keeps_unknown_name() -> None:
    corrected_texts: list[str] = []

    class FakePreprocessor:
        def preprocess_roi(self, _roi):
            return np.array([[42]], dtype=np.uint8)

    class FakeCorrector:
        def correct_hero_name(self, text, _hero_names):
            corrected_texts.append(text)
            return "嬴政" if text == "赢政" else text

        def warmup(self) -> None:
            return None

    class FakeEngine:
        def __init__(self) -> None:
            self._results = iter([("赢政", 0.995309), ("新武将", 0.999)])

        def ocr(self, _image, cls):
            text, confidence = next(self._results)
            assert cls is False
            return [[[None, (text, confidence)]]]

    recognizer = GeneralRecognizer(
        hero_names=["嬴政"],
        preprocessor=FakePreprocessor(),
        similarity_service=FakeCorrector(),
    )
    recognizer._ocr = FakeEngine()
    roi = np.zeros((2, 3, 3), dtype=np.uint8)

    assert recognizer._recognize_single(roi, 1) == ("嬴政", 0.995309)
    assert recognizer._recognize_single(roi, 2) == ("新武将", 0.999)
    assert corrected_texts == ["赢政", "新武将"]


def test_general_recognizer_maps_batch_boxes_by_slot_center() -> None:
    class FakeEngine:
        def ocr(self, _image, cls):
            assert cls is False
            return [[
                [[[43, 0], [53, 0], [53, 8], [43, 8]], ("第二", 0.9)],
                [[[2, 0], [12, 0], [12, 8], [2, 8]], ("第一", 0.8)],
            ]]

    recognizer = GeneralRecognizer()
    recognizer._ocr = FakeEngine()

    assert recognizer._recognize_prepared_batch({1: np.zeros((10, 10), dtype=np.uint8), 2: np.zeros((10, 10), dtype=np.uint8)}, "name") == {
        1: ("第一", 0.8),
        2: ("第二", 0.9),
    }


def test_general_recognizer_rejects_ambiguous_or_low_confidence_batch_slot() -> None:
    class FakeEngine:
        def ocr(self, _image, cls):
            assert cls is False
            return [[
                [[[2, 0], [12, 0], [12, 8], [2, 8]], ("低", 0.4)],
                [[[43, 0], [53, 0], [53, 8], [43, 8]], ("重", 0.9)],
                [[[44, 0], [54, 0], [54, 8], [44, 8]], ("复", 0.9)],
            ]]

    recognizer = GeneralRecognizer()
    recognizer._ocr = FakeEngine()

    assert recognizer._recognize_prepared_batch({1: np.zeros((10, 10), dtype=np.uint8), 2: np.zeros((10, 10), dtype=np.uint8)}, "name") == {}


def test_general_recognizer_rejects_truncated_name_with_multiple_corrections() -> None:
    class FakeEngine:
        def ocr(self, _image, cls):
            assert cls is False
            return [[[[[2, 0], [12, 0], [12, 8], [2, 8]], ("王", 0.99)]]]

    recognizer = GeneralRecognizer(hero_names=["王异", "王翦"])
    recognizer._ocr = FakeEngine()

    assert recognizer._recognize_prepared_batch({1: np.zeros((10, 10), dtype=np.uint8)}, "name") == {}


def test_general_recognizer_keeps_ambiguous_prefix_unresolved() -> None:
    recognizer = GeneralRecognizer(hero_names=["夏侯惇", "夏侯渊", "夏侯婴", "夏侯霸"])

    result = recognizer._resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "夏侯", "confidence": 0.98},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["candidates"] == ["夏侯婴", "夏侯惇", "夏侯渊", "夏侯霸"]


def test_general_recognizer_confirms_unique_prefix_but_not_unsafe_similarity() -> None:
    prefix = GeneralRecognizer(hero_names=["夏侯惇"])._resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "夏侯", "confidence": 0.98},
    ])
    unsafe = GeneralRecognizer(hero_names=["周瑜"])._resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "正瑜", "confidence": 0.99},
    ])

    assert (prefix["name"], prefix["resolution"]) == ("夏侯惇", "unique_prefix")
    assert unsafe["name"] == ""
    assert unsafe["resolution"] == "unresolved"
    assert unsafe["candidates"] == ["周瑜"]


def test_general_recognizer_resolves_slot_unique_candidate_without_competition() -> None:
    recognizer = GeneralRecognizer(hero_names=["卫子夫", "卫青", "卫玠"])
    results = [
        recognizer._resolve_name_evidence(1, [{"text": "卫子夫", "confidence": 0.9}]),
        recognizer._resolve_name_evidence(2, [{"text": "卫青", "confidence": 0.9}]),
        recognizer._resolve_name_evidence(3, [{"text": "卫", "confidence": 0.9}]),
    ]

    recognizer._resolve_page_names(results)

    assert (results[2]["name"], results[2]["resolution"]) == ("卫玠", "slot_unique")


def test_general_recognizer_does_not_resolve_competing_slot_candidates() -> None:
    recognizer = GeneralRecognizer(hero_names=["卫子夫", "卫青", "卫玠"])
    results = [
        recognizer._resolve_name_evidence(1, [{"text": "卫子夫", "confidence": 0.9}]),
        recognizer._resolve_name_evidence(2, [{"text": "卫青", "confidence": 0.9}]),
        recognizer._resolve_name_evidence(3, [{"text": "卫", "confidence": 0.9}]),
        recognizer._resolve_name_evidence(4, [{"text": "卫", "confidence": 0.9}]),
    ]

    recognizer._resolve_page_names(results)

    assert [item["name"] for item in results[2:]] == ["", ""]
    assert [item["candidates"] for item in results[2:]] == [["卫玠"], ["卫玠"]]


def test_general_recognizer_rolls_weaker_duplicate_back_to_conflict() -> None:
    recognizer = GeneralRecognizer()
    results = [
        {"index": 1, "name": "周瑜", "candidates": ["周瑜"], "resolution": "exact"},
        {
            "index": 2,
            "name": "周瑜",
            "candidates": ["周瑜"],
            "resolution": "unique_similarity",
        },
    ]

    recognizer._resolve_page_names(results)

    assert results[0]["name"] == "周瑜"
    assert results[1]["name"] == ""
    assert results[1]["resolution"] == "conflict"
