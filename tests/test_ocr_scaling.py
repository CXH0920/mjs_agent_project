"""GeneralRecognizer 缩放与 ROI 归一化测试（模板匹配策略已归位 test_template_manager）。"""

from __future__ import annotations

import numpy as np
from src.ocr.recognizer import GeneralRecognizer
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout, OcrRoiSlot


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
