"""OCR 模板缩放和 ROI 归一化测试。"""

from __future__ import annotations

import cv2
import numpy as np
from src.ocr.recognizer import GeneralRecognizer
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout, OcrRoiSlot
from src.ocr.template_manager import TemplateManager


def test_template_matches_scaled_screenshot(tmp_path) -> None:
    source = np.zeros((144, 256, 3), dtype=np.uint8)
    rng = np.random.default_rng(42)
    source[40:80, 90:150] = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    manager = TemplateManager(tmp_path / "template.png")
    manager.set_template(source, (90, 40, 60, 40))

    scaled = cv2.resize(source, (128, 72), interpolation=cv2.INTER_AREA)
    matched, confidence = manager.match(scaled, threshold=0.8)

    assert matched
    assert confidence >= 0.8
    assert manager.last_match_strategy == "base_local"
    assert manager.last_match_scale == 0.5


def test_template_falls_back_to_full_search_when_local_region_misses(tmp_path) -> None:
    source = np.zeros((144, 256, 3), dtype=np.uint8)
    rng = np.random.default_rng(7)
    source[40:80, 90:150] = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    manager = TemplateManager(tmp_path / "template.png")
    manager.set_template(source, (90, 40, 60, 40))

    moved = np.zeros_like(source)
    moved[80:120, 180:240] = source[40:80, 90:150]
    matched, confidence = manager.match(moved, threshold=0.8)

    assert matched
    assert confidence >= 0.8
    assert manager.last_match_strategy == "fallback_full_multiscale"


def test_template_reuses_cached_scale_when_base_scale_drifts(tmp_path) -> None:
    """上一轮命中的缩放在本轮 base_scale 变化时优先局部复验命中。"""
    source = np.zeros((144, 256, 3), dtype=np.uint8)
    rng = np.random.default_rng(11)
    source[40:80, 90:150] = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    manager = TemplateManager(tmp_path / "template.png")
    manager.set_template(source, (90, 40, 60, 40))

    half = cv2.resize(source, (128, 72), interpolation=cv2.INTER_AREA)
    matched, _ = manager.match(half, threshold=0.8)
    assert matched
    assert manager.last_match_scale == 0.5

    # base_scale 变为 0.375，但画面内容仍按 0.5 比例渲染（模拟分辨率切换后布局未重排）
    drifted = np.zeros((54, 96, 3), dtype=np.uint8)
    drifted[20:50, 45:75] = half[20:50, 45:75]
    matched, confidence = manager.match(drifted, threshold=0.8)

    assert matched
    assert manager.last_match_strategy == "cached_local"
    assert manager.last_match_scale == 0.5


def test_template_scale_history_shared_across_instances(tmp_path) -> None:
    """ocr_worker 每任务新建实例，缩放历史经类级缓存跨实例生效。"""
    source = np.zeros((144, 256, 3), dtype=np.uint8)
    rng = np.random.default_rng(13)
    source[40:80, 90:150] = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    first = TemplateManager(tmp_path / "template.png")
    first.set_template(source, (90, 40, 60, 40))
    half = cv2.resize(source, (128, 72), interpolation=cv2.INTER_AREA)
    assert first.match(half, threshold=0.8)[0]

    second = TemplateManager(tmp_path / "template.png")
    drifted = np.zeros((54, 96, 3), dtype=np.uint8)
    drifted[20:50, 45:75] = half[20:50, 45:75]
    matched, _ = second.match(drifted, threshold=0.8)

    assert matched
    assert second.last_match_strategy == "cached_local"


def test_general_recognizer_scales_rois_to_current_image(monkeypatch) -> None:
    captured_shapes: list[tuple[int, int]] = []
    recognizer = GeneralRecognizer(rois=[[100, 100, 20, 40]], hero_names=["测试"])

    def fake_batch(prepared_slots, _kind, evidence_by_slot=None):
        captured_shapes.extend((image.shape[1], image.shape[0]) for image in prepared_slots.values())
        evidence_by_slot[1] = [
            {"source": "batch_enhanced", "text": "测试", "confidence": 1.0},
        ]
        return {1: ("测试", 1.0)}

    monkeypatch.setattr(recognizer, "_recognize_prepared_batch", fake_batch)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)

    results = recognizer.recognize(image)

    assert results[0]["name"] == "测试"
    assert results[0]["resolution"] == "exact"
    assert results[0]["raw_name"] == "测试"
    assert captured_shapes == [(30, 60)]


def test_frame_space_reference_keeps_rois_unscaled(monkeypatch) -> None:
    """参考尺寸与截图同尺寸（巅峰 watcher 派生 ROI 场景）时缩放比恒为 1，
    ROI 按原始坐标原样使用，任意分辨率下不发生二次缩放。"""
    slots = (OcrRoiSlot(name_roi=(100, 100, 20, 40)),)
    recognizer = GeneralRecognizer(layout=OcrRoiLayout((1920, 1080), slots), hero_names=["测试"])
    captured_shapes: list[tuple[int, int]] = []

    def fake_batch(prepared_slots, _kind, evidence_by_slot=None):
        captured_shapes.extend((image.shape[1], image.shape[0]) for image in prepared_slots.values())
        evidence_by_slot[1] = [
            {"source": "batch_enhanced", "text": "测试", "confidence": 1.0},
        ]
        return {1: ("测试", 1.0)}

    monkeypatch.setattr(recognizer, "_recognize_prepared_batch", fake_batch)
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)

    results = recognizer.recognize(image)

    assert results[0]["name"] == "测试"
    # ROI (20,40) 零缩放裁剪后，识别器内部对名称条做 3× 放大供 OCR
    # （与既有用例 (10,20)→(30,60) 同一倍率）；若仍按 2560×1440 参考
    # 二次缩放（×0.75），此处会是 (45, 90)
    assert captured_shapes == [(60, 120)]


def test_default_general_rois_leave_vertical_name_padding() -> None:
    """默认 2560×1440 ROI 高度应为 145，避免竖排名称底部被截断。"""
    layout = OcrRoiConfig().layout_for("hero_selection")

    assert len(layout.slots) == 8
    assert all(slot.name_roi[2:] == (50, 145) for slot in layout.slots)


def test_general_recognizer_skips_empty_roi_without_calling_opencv(monkeypatch) -> None:
    recognizer = GeneralRecognizer(rois=[[3000, 100, 50, 145]], hero_names=[])
    monkeypatch.setattr(
        recognizer,
        "_recognize_prepared_single",
        lambda _prepared, _slot, _kind: (_ for _ in ()).throw(AssertionError("不应处理空 ROI")),
    )

    results = recognizer.recognize(np.zeros((1440, 2560, 3), dtype=np.uint8))

    assert results == [{
        "index": 1,
        "raw_name": "",
        "name": "",
        "candidates": [],
        "resolution": "unknown",
        "length_mode": "unknown",
        "confidence": 0.0,
        "evidence": [],
    }]


def test_match_guide_recognizer_returns_structured_2v2_roles(monkeypatch) -> None:
    names = {1: "徐晃", 2: "许褚", 4: "韩娥", 5: "孙策"}
    teams = {1: "汉军", 2: "汉军", 3: "楚军", 4: "楚军", 5: "楚军"}
    recognizer = GeneralRecognizer(hero_names=list(names.values()), page_type="match_guide")

    def fake_batch(slots, kind, evidence_by_slot=None):
        source = names if kind == "name" else teams
        result = {slot: (source[slot], 0.9) for slot in slots if slot in source}
        if evidence_by_slot is not None:
            for slot, (text, confidence) in result.items():
                evidence_by_slot[slot] = [{
                    "source": "batch_enhanced", "text": text, "confidence": confidence,
                }]
        return result

    monkeypatch.setattr(recognizer, "_recognize_prepared_batch", fake_batch)
    monkeypatch.setattr(
        recognizer,
        "_recognize_prepared_single",
        lambda _roi, slot, kind: ("", 0.0) if kind == "name" else (teams[slot], 1.0),
    )

    results = recognizer.recognize(np.zeros((1440, 2560, 3), dtype=np.uint8))

    assert [(item["index"], item["name"], item["team"]) for item in results] == [
        (1, "徐晃", "汉军"),
        (2, "许褚", "汉军"),
        (3, "", "楚军"),
        (4, "韩娥", "楚军"),
        (5, "孙策", "楚军"),
    ]
    assert results[2]["resolution"] == "unknown"
