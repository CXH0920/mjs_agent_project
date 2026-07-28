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
