# -*- coding: utf-8 -*-
"""B2 复核模式（v6 复核插槽）单元测试。

覆盖：复核触发条件、候选闭包接受纪律、占用名排除、开关关闭旁路、
RapidOCR 适配层输出翻译、复核引擎惰性加载与熔断、官方导入罕见字兜底接线。
"""

import sys
import types

import numpy as np
import pytest
from src.business.recognition import official_data_import_service as official_mod
from src.config.env import load_env_config
from src.ocr.paddle_loader import RapidOcrEngine, create_rapidocr_ocr
from src.ocr.paddle_loader import get_recheck_ocr_engine as _loader_get_recheck
from src.ocr.recognizer import GeneralRecognizer
from src.ocr.roi_config import OcrRoiLayout, OcrRoiSlot

_RAPIDOCR_MODEL_FILES = (
    "PP-OCRv6_det_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "PP-OCRv6_rec_small.onnx",
)


def _enable_recheck(monkeypatch, engine) -> None:
    """打开复核开关并注入复核引擎，主引擎加载路径不被触碰。"""
    monkeypatch.setattr(
        "src.config.env.get_mumu_config",
        lambda: {"mumu_ocr_recheck_enabled": True},
    )
    monkeypatch.setattr("src.ocr.paddle_loader.get_recheck_ocr_engine", lambda: engine)


def _unresolved_slot(index: int, candidates: list[str]) -> dict:
    return {
        "index": index,
        "raw_name": "王",
        "name": "",
        "confidence": 0.99,
        "candidates": list(candidates),
        "resolution": "unresolved",
        "length_mode": "missing",
        "evidence": [
            {"source": "batch_plain", "text": "王", "confidence": 0.99},
            {"source": "single_plain", "text": "王", "confidence": 0.98},
        ],
    }


def test_recheck_confirms_reading_within_candidate_closure(monkeypatch) -> None:
    _enable_recheck(monkeypatch, _SlotTextEngine("王濬", 0.9967))
    recognizer = GeneralRecognizer(hero_names=["王濬", "王翦"], page_type="hero_selection")
    results = [_unresolved_slot(1, ["王濬", "王翦"])]

    recognizer._recheck_unresolved_slots(results, {1: _strip()})

    assert results[0]["name"] == "王濬"
    assert results[0]["resolution"] == "exact"
    assert [e for e in results[0]["evidence"] if e["source"] == "recheck"] == [
        {"source": "recheck", "text": "王濬", "confidence": 0.9967},
    ]


def test_recheck_rejects_reading_outside_candidate_closure(monkeypatch) -> None:
    _enable_recheck(monkeypatch, _SlotTextEngine("王允", 0.99))
    recognizer = GeneralRecognizer(hero_names=["王濬", "王翦", "王允"], page_type="hero_selection")
    results = [_unresolved_slot(1, ["王濬", "王翦"])]

    recognizer._recheck_unresolved_slots(results, {1: _strip()})

    assert results[0]["name"] == ""
    assert results[0]["resolution"] == "unresolved"
    assert all(e["source"] != "recheck" for e in results[0]["evidence"])


def test_recheck_does_not_bind_name_occupied_by_other_slot(monkeypatch) -> None:
    _enable_recheck(monkeypatch, _SlotTextEngine("王濬", 0.99))
    recognizer = GeneralRecognizer(hero_names=["王濬", "王翦"], page_type="hero_selection")
    results = [
        _unresolved_slot(1, ["王濬", "王翦"]),
        {
            "index": 2, "raw_name": "王濬", "name": "王濬", "confidence": 0.99,
            "candidates": ["王濬"], "resolution": "exact", "length_mode": "complete",
            "evidence": [{"source": "batch_plain", "text": "王濬", "confidence": 0.99}],
        },
    ]

    recognizer._recheck_unresolved_slots(results, {1: _strip(), 2: _strip()})

    assert results[0]["name"] == ""
    assert results[0]["resolution"] == "unresolved"


def test_recheck_occupies_confirmed_names_across_pending_slots(monkeypatch) -> None:
    _enable_recheck(monkeypatch, _SameTextEverySlotEngine("王濬", 0.99))
    recognizer = GeneralRecognizer(hero_names=["王濬", "王翦"], page_type="hero_selection")
    results = [_unresolved_slot(1, ["王濬", "王翦"]), _unresolved_slot(2, ["王濬", "王翦"])]

    recognizer._recheck_unresolved_slots(results, {1: _strip(), 2: _strip()})

    assert (results[0]["name"], results[0]["resolution"]) == ("王濬", "exact")
    assert (results[1]["name"], results[1]["resolution"]) == ("", "unresolved")


def test_recheck_disabled_never_touches_engine(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.config.env.get_mumu_config",
        lambda: {"mumu_ocr_recheck_enabled": False},
    )

    def _boom():
        raise AssertionError("复核关闭时不应请求复核引擎")

    monkeypatch.setattr("src.ocr.paddle_loader.get_recheck_ocr_engine", _boom)
    recognizer = GeneralRecognizer(hero_names=["王濬", "王翦"], page_type="hero_selection")
    results = [_unresolved_slot(1, ["王濬", "王翦"])]

    recognizer._recheck_unresolved_slots(results, {1: _strip()})

    assert results[0]["resolution"] == "unresolved"


def test_recheck_skips_slots_without_candidate_closure(monkeypatch) -> None:
    _enable_recheck(monkeypatch, _SlotTextEngine("王濬", 0.99))
    recognizer = GeneralRecognizer(hero_names=["王濬"], page_type="hero_selection")
    results = [_unresolved_slot(1, [])]
    results[0]["resolution"] = "unknown_new_hero"

    recognizer._recheck_unresolved_slots(results, {1: _strip()})

    assert results[0]["resolution"] == "unknown_new_hero"


def test_recognize_hooks_recheck_after_page_resolution(monkeypatch) -> None:
    """端到端：页面消解后仍未决的槽位进入复核并确认，已决槽保持原判。"""
    _enable_recheck(monkeypatch, _SlotTextEngine("王濬", 0.9967))
    recognizer = GeneralRecognizer(
        hero_names=["王濬", "王翦", "王允", "王陵"], page_type="hero_selection",
    )
    recognizer.adopt_engine(_BatchCanvasEngine())
    image = np.zeros((40, 200, 3), dtype=np.uint8)
    layout = OcrRoiLayout(
        (200, 40),
        (OcrRoiSlot(name_roi=(0, 0, 100, 40)), OcrRoiSlot(name_roi=(100, 0, 100, 40))),
    )
    recognizer._layout = layout

    results = recognizer.recognize(image)

    assert (results[0]["name"], results[0]["resolution"]) == ("王濬", "exact")
    assert any(e["source"] == "recheck" for e in results[0]["evidence"])
    assert (results[1]["name"], results[1]["resolution"]) == ("王翦", "exact")


class _SlotTextEngine:
    """复核引擎替身：对任何画布在首个条带区内返回一个固定读数。"""

    def __init__(self, text: str, confidence: float) -> None:
        self._text = text
        self._confidence = confidence

    def ocr(self, _image, cls=False):
        assert cls is False
        box = [[2, 2], [4, 2], [4, 4], [2, 4]]
        lines = [[box, (self._text, self._confidence)]]
        return [lines]  # paddleocr 2.x：result[0] 为行列表


class _SameTextEverySlotEngine:
    """复核引擎替身：画布内每个条带区（10px 条 + 30px 间隙）各返回一个同文读数。"""

    def __init__(self, text: str, confidence: float) -> None:
        self._text = text
        self._confidence = confidence

    def ocr(self, image, cls=False):
        assert cls is False
        lines = []
        left = 0
        while left < image.shape[1]:
            cx = left + 3
            lines.append([[[cx, 2], [cx + 2, 2], [cx + 2, 4], [cx, 4]], (self._text, self._confidence)])
            left += 40
        return [lines]


def _strip() -> np.ndarray:
    return np.zeros((10, 10), dtype=np.uint8)


class _BatchCanvasEngine:
    """主引擎替身：批量画布返回槽1截断"王"+槽2完整"王翦"，单条回退返回"王"。"""

    def ocr(self, image, cls=False):
        assert cls is False
        if image.shape[1] > 400:  # 双槽画布（2×300 + 30 间隙）
            lines = [
                [[[10, 10], [20, 10], [20, 30], [10, 30]], ("王", 0.99)],
                [[[350, 10], [360, 10], [360, 30], [350, 30]], ("王翦", 0.99)],
            ]
        else:
            lines = [[[[10, 10], [20, 10], [20, 30], [10, 30]], ("王", 0.99)]]
        return [lines]


def test_recognize_prepared_batch_uses_injected_engine() -> None:
    class _RefusingMain:
        def ocr(self, *_args, **_kwargs):
            raise AssertionError("注入复核引擎后不应触碰主引擎")

    recognizer = GeneralRecognizer()
    recognizer.adopt_engine(_RefusingMain())

    recognized = recognizer._recognize_prepared_batch(
        {1: _strip()}, "name", engine=_SlotTextEngine("王濬", 0.99),
    )

    assert recognized == {1: ("王濬", 0.99)}


# ── RapidOCR 适配层 ──────────────────────────────────────────────────────


def test_rapidocr_engine_translates_to_paddle_style() -> None:
    class _FakeRapid:
        def __call__(self, img, use_det, use_cls, use_rec):
            assert use_det is True and use_cls is False and use_rec is True
            assert img.ndim == 3  # 灰度画布已转三通道
            return types.SimpleNamespace(
                txts=(" 王濬 ", "x"),
                boxes=(np.array([[1, 2], [3, 2], [3, 4], [1, 4]]), "box2"),
                scores=(0.9967, 0.5),
            )

    engine = RapidOcrEngine(_FakeRapid())

    assert engine.ocr(np.zeros((4, 4), dtype=np.uint8), cls=False) == [
        [
            ([[1, 2], [3, 2], [3, 4], [1, 4]], ("王濬", 0.9967)),  # np 框转纯 list
            ("box2", ("x", 0.5)),
        ],
    ]


def test_rapidocr_engine_translates_empty_result() -> None:
    class _FakeRapid:
        def __call__(self, *_args, **_kwargs):
            return types.SimpleNamespace(txts=None, boxes=None, scores=None)

    assert RapidOcrEngine(_FakeRapid()).ocr(np.zeros((4, 4), dtype=np.uint8)) == [None]


def _make_fake_rapidocr_module(tmp_path, with_models: bool):
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    models = pkg / "models"
    models.mkdir(exist_ok=True)
    if with_models:
        for name in _RAPIDOCR_MODEL_FILES:
            (models / name).write_bytes(b"model")
    module = types.ModuleType("rapidocr")
    module.__file__ = str(pkg / "main.py")
    module.__path__ = [str(pkg)]

    class _FakeRapidOCR:
        def __init__(self, params=None):
            self.params = params or {}

    module.RapidOCR = _FakeRapidOCR
    return module, models


def test_create_rapidocr_ocr_pins_det_params_and_bundled_models(tmp_path, monkeypatch) -> None:
    module, models = _make_fake_rapidocr_module(tmp_path, with_models=True)
    monkeypatch.setitem(sys.modules, "rapidocr", module)

    engine = create_rapidocr_ocr()

    assert isinstance(engine, RapidOcrEngine)
    params = engine._engine.params
    assert params["Det.limit_type"] == "max"
    assert params["Det.limit_side_len"] == 960
    assert params["Det.model_path"] == str(models / "PP-OCRv6_det_small.onnx")
    assert params["Cls.model_path"] == str(models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
    assert params["Rec.model_path"] == str(models / "PP-OCRv6_rec_small.onnx")


def test_create_rapidocr_ocr_fails_without_bundled_models(tmp_path, monkeypatch) -> None:
    module, _models = _make_fake_rapidocr_module(tmp_path, with_models=False)
    monkeypatch.setitem(sys.modules, "rapidocr", module)

    with pytest.raises(FileNotFoundError):
        create_rapidocr_ocr()


@pytest.fixture(autouse=True)
def _reset_recheck_engine_globals(monkeypatch):
    """隔离模块级复核引擎缓存，避免用例间串扰。"""
    import src.ocr.paddle_loader as loader

    monkeypatch.setattr(loader, "_RECHECK_ENGINE", None)
    monkeypatch.setattr(loader, "_RECHECK_ENGINE_FAILED", False)


def test_get_recheck_ocr_engine_caches_single_engine(monkeypatch) -> None:
    import src.ocr.paddle_loader as loader

    calls = []
    monkeypatch.setattr(loader, "create_rapidocr_ocr", lambda: calls.append(1) or "engine")

    assert loader.get_recheck_ocr_engine() == "engine"
    assert loader.get_recheck_ocr_engine() == "engine"
    assert len(calls) == 1


def test_get_recheck_ocr_engine_breaks_on_load_failure(monkeypatch) -> None:
    import src.ocr.paddle_loader as loader

    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("rapidocr 不可用")

    monkeypatch.setattr(loader, "create_rapidocr_ocr", _boom)

    assert _loader_get_recheck() is None
    assert _loader_get_recheck() is None
    assert len(calls) == 1


# ── 官方导入罕见字兜底接线 ────────────────────────────────────────────────


def _make_service() -> official_mod.OfficialDataImportService:
    return official_mod.OfficialDataImportService(hero_names=["王濬"])


def test_rare_char_engine_prefers_v6_when_recheck_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        official_mod, "get_mumu_config", lambda: {"mumu_ocr_recheck_enabled": True},
    )
    monkeypatch.setattr(
        "src.ocr.paddle_loader.get_recheck_ocr_engine", lambda: "v6-engine",
    )

    def _boom(**_kwargs):
        raise AssertionError("v6 可用时不应回退创建 cht 引擎")

    monkeypatch.setattr(official_mod, "create_paddle_ocr", _boom)

    assert _make_service()._rare_char_engine == "v6-engine"


def test_rare_char_engine_falls_back_to_cht_when_v6_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        official_mod, "get_mumu_config", lambda: {"mumu_ocr_recheck_enabled": True},
    )
    monkeypatch.setattr("src.ocr.paddle_loader.get_recheck_ocr_engine", lambda: None)
    created = {}

    def _fake_create(**kwargs):
        created.update(kwargs)
        return "cht-engine"

    monkeypatch.setattr(official_mod, "create_paddle_ocr", _fake_create)

    assert _make_service()._rare_char_engine == "cht-engine"
    assert created["lang"] == "chinese_cht"


def test_rare_char_engine_uses_cht_when_recheck_disabled(monkeypatch) -> None:
    monkeypatch.setattr(official_mod, "get_mumu_config", lambda: {})
    created = {}

    def _fake_create(**kwargs):
        created.update(kwargs)
        return "cht-engine"

    monkeypatch.setattr(official_mod, "create_paddle_ocr", _fake_create)

    assert _make_service()._rare_char_engine == "cht-engine"
    assert created["lang"] == "chinese_cht"


def test_rare_char_engine_failure_breaks_further_requests(monkeypatch) -> None:
    monkeypatch.setattr(
        official_mod, "get_mumu_config", lambda: {"mumu_ocr_recheck_enabled": True},
    )
    monkeypatch.setattr("src.ocr.paddle_loader.get_recheck_ocr_engine", lambda: None)

    def _boom(**_kwargs):
        raise RuntimeError("模型缺失")

    monkeypatch.setattr(official_mod, "create_paddle_ocr", _boom)
    service = _make_service()

    assert service._rare_char_engine is None
    assert service._rare_char_engine is None
    assert service._rare_char_engine_failed is True


# ── 配置解析 ─────────────────────────────────────────────────────────────


def test_env_config_maps_recheck_switch(tmp_path) -> None:
    env_file = tmp_path / "config.env"
    env_file.write_text("MUMU_OCR_RECHECK_ENABLED=true\n", encoding="utf-8")

    config = load_env_config(env_file)

    assert config["mumu_ocr_recheck_enabled"] is True
