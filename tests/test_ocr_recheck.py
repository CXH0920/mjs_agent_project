# -*- coding: utf-8 -*-
"""复核模式（与主引擎互为异构的复核插槽，B1 起为 v4）单元测试。

覆盖：复核触发条件、候选闭包接受纪律、占用名排除、开关关闭旁路、
RapidOCR 适配层输出翻译、双套件参数与模型定位、引擎惰性加载与熔断、
主/复核角色互斥、%TEMP% 同步指纹加固、官方导入罕见字兜底接线。
"""

import sys
import types

import numpy as np
import pytest
from src.business.recognition import official_ocr_engines as engines_mod
from src.config.env import load_env_config
from src.ocr.engine_loader import RapidOcrEngine, create_rapidocr_ocr, primary_suite
from src.ocr.engine_loader import get_recheck_ocr_engine as _loader_get_recheck
from src.ocr.recognizer import GeneralRecognizer
from src.ocr.roi_config import OcrRoiLayout, OcrRoiSlot

_V6_MODEL_FILES = (
    "PP-OCRv6_det_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "PP-OCRv6_rec_small.onnx",
)
_V4_MODEL_FILES = (
    "ch_PP-OCRv4_det_mobile.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "ch_PP-OCRv4_rec_mobile.onnx",
    "ppocr_keys_v1.txt",
)


def _enable_recheck(monkeypatch, engine) -> None:
    """打开复核开关并注入复核引擎，主引擎加载路径不被触碰。"""
    monkeypatch.setattr(
        "src.config.env.get_mumu_config",
        lambda: {"mumu_ocr_recheck_enabled": True},
    )
    monkeypatch.setattr("src.ocr.engine_loader.get_recheck_ocr_engine", lambda: engine)


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

    monkeypatch.setattr("src.ocr.engine_loader.get_recheck_ocr_engine", _boom)
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
        return [lines]  # paddleocr 2.x 风格：result[0] 为行列表


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


def test_team_label_retries_single_then_recheck_on_garbage(monkeypatch) -> None:
    """team 批量读出非空乱码（归一化失败）也须触发单条重试，直至复核引擎救回。"""
    _enable_recheck(monkeypatch, _SlotTextEngine("汉军", 0.9))

    class _GarbageEverywhereEngine:
        # 主引擎：批量画布与单条都读出不含楚/汉的乱码
        def ocr(self, _image, cls=False):
            box = [[2, 2], [20, 2], [20, 10], [2, 10]]
            return [[[box, ("電", 0.9)]]]

    recognizer = GeneralRecognizer(
        hero_names=["王濬", "王翦"], page_type="match_guide",
        layout=OcrRoiLayout(
            (200, 40),
            (OcrRoiSlot(name_roi=(0, 0, 80, 40), team_roi=(120, 0, 60, 40)),
             OcrRoiSlot(name_roi=(0, 0, 80, 40))),
        ),
    )
    recognizer.adopt_engine(_GarbageEverywhereEngine())
    image = np.zeros((40, 200, 3), dtype=np.uint8)

    results = recognizer._recognize_match_guide(image)

    assert results[0]["team"] == "汉军"
    assert results[1]["team"] == ""


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


# ── 双套件构造（v6 主 / v4 复核）────────────────────────────────────────


def _make_fake_rapidocr_module(tmp_path, with_models: bool):
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    models = pkg / "models"
    models.mkdir(exist_ok=True)
    if with_models:
        for name in {*_V6_MODEL_FILES, *_V4_MODEL_FILES}:
            (models / name).write_bytes(b"model")
    module = types.ModuleType("rapidocr")
    module.__file__ = str(pkg / "main.py")
    module.__path__ = [str(pkg)]

    class _FakeRapidOCR:
        def __init__(self, params=None):
            self.params = params or {}

    module.RapidOCR = _FakeRapidOCR
    return module, models


def _pin_threads(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.config.env.get_mumu_config",
        lambda: {"mumu_ocr_cpu_threads": 4},
    )


@pytest.mark.parametrize("suite", ["v6", "v4"])
def test_create_rapidocr_ocr_pins_params_and_model_paths(tmp_path, monkeypatch, suite) -> None:
    module, models = _make_fake_rapidocr_module(tmp_path, with_models=True)
    monkeypatch.setitem(sys.modules, "rapidocr", module)
    _pin_threads(monkeypatch)

    engine = create_rapidocr_ocr(suite)

    assert isinstance(engine, RapidOcrEngine)
    params = engine._engine.params
    assert params["Det.limit_type"] == "max"
    assert params["Det.limit_side_len"] == 960
    assert params["EngineConfig.onnxruntime.intra_op_num_threads"] == 4
    if suite == "v6":
        assert params["Det.model_path"] == str(models / "PP-OCRv6_det_small.onnx")
        assert params["Cls.model_path"] == str(models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
        assert params["Rec.model_path"] == str(models / "PP-OCRv6_rec_small.onnx")
        assert "Rec.rec_keys_path" not in params  # v6 字表内嵌于 ONNX 元数据
    else:
        assert params["Det.model_path"] == str(models / "ch_PP-OCRv4_det_mobile.onnx")
        assert params["Rec.model_path"] == str(models / "ch_PP-OCRv4_rec_mobile.onnx")
        assert params["Rec.rec_keys_path"] == str(models / "ppocr_keys_v1.txt")


def test_create_rapidocr_ocr_rejects_unknown_suite(tmp_path, monkeypatch) -> None:
    with pytest.raises(ValueError):
        create_rapidocr_ocr("v5")


@pytest.mark.parametrize("suite", ["v6", "v4"])
def test_create_rapidocr_ocr_fails_without_models(tmp_path, monkeypatch, suite) -> None:
    module, _models = _make_fake_rapidocr_module(tmp_path, with_models=False)
    monkeypatch.setitem(sys.modules, "rapidocr", module)

    with pytest.raises(FileNotFoundError):
        create_rapidocr_ocr(suite)


def test_missing_v4_models_hint_points_to_fetch_script(tmp_path, monkeypatch) -> None:
    module, models = _make_fake_rapidocr_module(tmp_path, with_models=False)
    # 仅放 v6 三件，v4 缺失：报错应指引预取脚本
    for name in _V6_MODEL_FILES:
        (models / name).write_bytes(b"model")
    monkeypatch.setitem(sys.modules, "rapidocr", module)

    with pytest.raises(FileNotFoundError, match="fetch_recheck_models"):
        create_rapidocr_ocr("v4")


@pytest.fixture(autouse=True)
def _reset_engine_globals(monkeypatch):
    """隔离模块级引擎缓存，避免用例间串扰。"""
    import src.ocr.engine_loader as loader

    monkeypatch.setattr(loader, "_ENGINES", {})
    monkeypatch.setattr(loader, "_FAILED_SUITES", set())


def test_get_recheck_ocr_engine_caches_single_engine(monkeypatch) -> None:
    import src.ocr.engine_loader as loader

    calls = []
    monkeypatch.setattr(loader, "create_rapidocr_ocr", lambda suite="v4": calls.append(suite) or "engine")

    assert _loader_get_recheck() == "engine"
    assert _loader_get_recheck() == "engine"
    assert calls == ["v4"]  # 主引擎默认 v6 → 复核套件 v4


def test_get_recheck_ocr_engine_breaks_on_load_failure(monkeypatch) -> None:
    import src.ocr.engine_loader as loader

    calls = []

    def _boom(suite="v4"):
        calls.append(suite)
        raise RuntimeError("rapidocr 不可用")

    monkeypatch.setattr(loader, "create_rapidocr_ocr", _boom)

    assert _loader_get_recheck() is None
    assert _loader_get_recheck() is None
    assert len(calls) == 1


def test_primary_and_recheck_suites_swap_by_config(monkeypatch) -> None:
    """主/复核角色互斥：MUMU_OCR_PRIMARY_ENGINE=v4 时主=v4、复核=v6（回滚档）。"""
    import src.ocr.engine_loader as loader

    monkeypatch.setattr(
        "src.config.env.get_mumu_config",
        lambda: {"mumu_ocr_primary_engine": "v4"},
    )
    calls = []
    monkeypatch.setattr(loader, "create_rapidocr_ocr",
                        lambda suite="v6": calls.append(suite) or f"engine-{suite}")

    assert primary_suite() == "v4"
    assert loader.get_primary_ocr_engine() == "engine-v4"
    assert _loader_get_recheck() == "engine-v6"
    assert calls == ["v4", "v6"]


def test_primary_suite_falls_back_to_v6_on_invalid_value(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.config.env.get_mumu_config",
        lambda: {"mumu_ocr_primary_engine": "v7"},
    )
    assert primary_suite() == "v6"


def test_fingerprint_logs_info_and_drift_warning(tmp_path, monkeypatch, caplog) -> None:
    """成功日志含 det/rec 指纹与 rapidocr 版本；与钉死基线不符时另告警（漂移哨兵）。"""
    import logging

    module, _models = _make_fake_rapidocr_module(tmp_path, with_models=True)
    monkeypatch.setitem(sys.modules, "rapidocr", module)
    _pin_threads(monkeypatch)

    with caplog.at_level(logging.INFO, logger="src.ocr.engine_loader"):
        create_rapidocr_ocr("v6")  # 假模型文件必然哈希不符

    info = " ".join(r.message for r in caplog.records if r.levelno == logging.INFO)
    assert "det=" in info and "rec=" in info and "rapidocr" in info
    assert any("指纹" in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_sync_temp_models_recopies_on_source_change(tmp_path) -> None:
    """%TEMP% 同步指纹加固：源文件 mtime 变更后自动重拷，旧副本不滞留。"""
    import src.ocr.engine_loader as loader

    src_dir = tmp_path / "src_models"
    src_dir.mkdir()
    (src_dir / "PP-OCRv6_det_small.onnx").write_bytes(b"v6-det")
    (src_dir / "ch_PP-OCRv4_rec_mobile.onnx").write_bytes(b"v4-rec")
    tmp_root = tmp_path / "temp_models"

    loader._sync_temp_models(src_dir, tmp_root)
    first = (tmp_root / "PP-OCRv6_det_small.onnx").read_bytes()
    assert first == b"v6-det"

    # 源更新（内容+mtime 变化）→ 重拷
    (src_dir / "PP-OCRv6_det_small.onnx").write_bytes(b"v6-det-v2")
    loader._sync_temp_models(src_dir, tmp_root)
    assert (tmp_root / "PP-OCRv6_det_small.onnx").read_bytes() == b"v6-det-v2"
    assert (tmp_root / "ch_PP-OCRv4_rec_mobile.onnx").read_bytes() == b"v4-rec"


# ── 官方导入罕见字兜底接线 ────────────────────────────────────────────────


def _make_engines() -> engines_mod.OfficialOcrEngines:
    return engines_mod.OfficialOcrEngines()


def test_rare_char_engine_uses_recheck_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        engines_mod, "get_mumu_config", lambda: {"mumu_ocr_recheck_enabled": True},
    )
    monkeypatch.setattr(
        "src.ocr.engine_loader.get_recheck_ocr_engine", lambda: "recheck-engine",
    )

    assert _make_engines().rare_char == "recheck-engine"


def test_rare_char_engine_breaks_when_recheck_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        engines_mod, "get_mumu_config", lambda: {"mumu_ocr_recheck_enabled": True},
    )
    monkeypatch.setattr("src.ocr.engine_loader.get_recheck_ocr_engine", lambda: None)
    engines = _make_engines()

    assert engines.rare_char is None
    assert engines.rare_char is None  # 熔断后不再重试
    assert engines.rare_char_failed is True


def test_rare_char_engine_returns_none_when_recheck_disabled(monkeypatch) -> None:
    """复核关闭：不加载任何第二引擎，保留原结果待复核（cht 链已退役）。"""
    monkeypatch.setattr(engines_mod, "get_mumu_config", lambda: {})

    def _boom():
        raise AssertionError("复核关闭时不应请求复核引擎")

    monkeypatch.setattr("src.ocr.engine_loader.get_recheck_ocr_engine", _boom)
    engines = _make_engines()

    assert engines.rare_char is None
    assert engines.rare_char_failed is False  # 未熔断：开启开关后即可生效


# ── 配置解析 ─────────────────────────────────────────────────────────────


def test_env_config_maps_recheck_switch(tmp_path) -> None:
    env_file = tmp_path / "config.env"
    env_file.write_text("MUMU_OCR_RECHECK_ENABLED=true\n", encoding="utf-8")

    config = load_env_config(env_file)

    assert config["mumu_ocr_recheck_enabled"] is True


def test_env_config_maps_primary_engine(tmp_path) -> None:
    env_file = tmp_path / "config.env"
    env_file.write_text("MUMU_OCR_PRIMARY_ENGINE=v4\n", encoding="utf-8")

    config = load_env_config(env_file)

    assert config["mumu_ocr_primary_engine"] == "v4"
