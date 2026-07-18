"""OCR 模板缩放和 ROI 归一化测试。"""

from __future__ import annotations

import cv2
import numpy as np

from src.ocr.recognizer import GeneralRecognizer, _DEFAULT_GENERALS_ROI
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


def test_general_recognizer_scales_rois_to_current_image(monkeypatch) -> None:
    captured_shapes: list[tuple[int, int]] = []
    recognizer = GeneralRecognizer(rois=[[100, 100, 20, 40]], hero_names=[])

    def fake_recognize_single(roi, slot):
        captured_shapes.append((roi.shape[1], roi.shape[0]))
        return "测试", 1.0

    monkeypatch.setattr(recognizer, "_recognize_single", fake_recognize_single)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)

    results = recognizer.recognize(image)

    assert results == [{"index": 1, "name": "测试", "confidence": 1.0}]
    assert captured_shapes == [(10, 20)]


def test_default_general_rois_leave_vertical_name_padding() -> None:
    """默认 2560×1440 ROI 高度应为 145，避免竖排名称底部被截断。"""
    assert len(_DEFAULT_GENERALS_ROI) == 8
    assert all(roi[2:] == [50, 145] for roi in _DEFAULT_GENERALS_ROI)
