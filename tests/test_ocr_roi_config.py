"""OCR ROI 默认配置与本地覆盖配置测试。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from src.business.emulator.capture_service import CaptureService
from src.ocr.card_grid_detector import derive_name_rois
from src.ocr.roi_config import (
    DEFAULT_ROI_CONFIG_PATH,
    OcrRoiConfig,
    OcrRoiConfigError,
    OcrRoiLayout,
    OcrRoiSlot,
)


def _copy_default_config(tmp_path: Path) -> Path:
    path = tmp_path / "ocr_rois.default.json"
    path.write_text(DEFAULT_ROI_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return path


def test_default_layouts_contain_expected_slots(tmp_path: Path) -> None:
    config = OcrRoiConfig(_copy_default_config(tmp_path), tmp_path / "ocr_rois.json")

    assert config.layout_for("hero_selection").reference_size == (2560, 1440)
    assert len(config.layout_for("hero_selection").slots) == 8
    assert len(config.layout_for("match_guide").slots) == 5
    assert all(slot.team_roi is not None for slot in config.layout_for("match_guide").slots)
    assert config.layout_for("peak_board").reference_size == (2560, 1440)
    assert len(config.layout_for("peak_board").slots) == 8


def test_save_layout_writes_local_override_with_utf8_lf(tmp_path: Path) -> None:
    default_path = _copy_default_config(tmp_path)
    user_path = tmp_path / "config" / "ocr_rois.json"
    config = OcrRoiConfig(default_path, user_path)
    original = config.layout_for("hero_selection")
    slots = list(original.slots)
    slots[0] = replace(slots[0], name_roi=(160, 370, 50, 145))

    config.save_layout("hero_selection", OcrRoiLayout(original.reference_size, tuple(slots)))

    raw = user_path.read_bytes()
    stored = json.loads(raw.decode("utf-8"))
    reloaded = OcrRoiConfig(default_path, user_path)
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert set(stored["layouts"]) == {"hero_selection"}
    assert reloaded.layout_for("hero_selection").slots[0].name_roi == (160, 370, 50, 145)
    assert reloaded.layout_for("match_guide") == config.layout_for("match_guide")


def test_invalid_local_override_falls_back_to_default(tmp_path: Path, caplog) -> None:
    default_path = _copy_default_config(tmp_path)
    user_path = tmp_path / "ocr_rois.json"
    user_path.write_text(
        json.dumps({
            "schema_version": 1,
            "layouts": {"hero_selection": {"reference_size": [2560, 1440], "slots": []}},
        }),
        encoding="utf-8",
        newline="\n",
    )

    config = OcrRoiConfig(default_path, user_path)

    assert len(config.layout_for("hero_selection").slots) == 8
    assert "已回退默认布局" in caplog.text


def test_rejects_out_of_bounds_or_incomplete_layout(tmp_path: Path) -> None:
    config = OcrRoiConfig(_copy_default_config(tmp_path), tmp_path / "ocr_rois.json")
    layout = config.layout_for("hero_selection")
    out_of_bounds = OcrRoiLayout(
        layout.reference_size,
        (OcrRoiSlot((2500, 1400, 100, 100)),) + layout.slots[1:],
    )

    with pytest.raises(OcrRoiConfigError, match="超出参考尺寸"):
        config.save_layout("hero_selection", out_of_bounds)

    with pytest.raises(OcrRoiConfigError, match="应包含 8 个"):
        config.save_layout("hero_selection", OcrRoiLayout(layout.reference_size, layout.slots[:7]))


def test_capture_task_keeps_layout_snapshot_after_config_update(tmp_path: Path, monkeypatch) -> None:
    config = OcrRoiConfig(_copy_default_config(tmp_path), tmp_path / "ocr_rois.json")
    service = CaptureService(roi_config=config)
    submitted = []

    class _Worker:
        def submit(self, task) -> None:
            submitted.append(task)

    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: _Worker())
    first_task = service.submit_ocr_task("first", template_name="hero_selection")
    layout = config.layout_for("hero_selection")
    slots = list(layout.slots)
    slots[0] = replace(slots[0], name_roi=(160, 370, 50, 145))
    config.save_layout("hero_selection", OcrRoiLayout(layout.reference_size, tuple(slots)))
    second_task = service.submit_ocr_task("second", template_name="hero_selection")

    assert submitted == [first_task, second_task]
    assert first_task.roi_layout.slots[0].name_roi == (155, 370, 50, 145)
    assert second_task.roi_layout.slots[0].name_roi == (160, 370, 50, 145)


def test_peak_board_layout_reference_matches_frame_size(tmp_path: Path, monkeypatch) -> None:
    """watcher 派生的 rois 与截帧同像素空间：布局参考尺寸取当帧尺寸，
    非 1440p 分辨率下不再被模板页参考尺寸二次缩放。"""
    config = OcrRoiConfig(_copy_default_config(tmp_path), tmp_path / "ocr_rois.json")
    service = CaptureService(roi_config=config)
    submitted = []

    class _Worker:
        def submit(self, task) -> None:
            submitted.append(task)

    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: _Worker())

    cards = [(100 + i * 200, 180, 170, 240) for i in range(9)]
    rois = [list(roi) for roi in derive_name_rois(cards)]
    task = service.submit_ocr_task(
        Image.new("RGB", (1920, 1080)),
        template_name="peak_board",
        rois=rois,
        match_template=False,
    )

    assert task.roi_layout.reference_size == (1920, 1080)
    assert [slot.name_roi for slot in task.roi_layout.slots] == [tuple(roi) for roi in rois]

    # 对照：固定布局页（rois=None）仍按配置参考尺寸缩放，行为不变
    fixed = service.submit_ocr_task(
        Image.new("RGB", (1920, 1080)), template_name="hero_selection"
    )
    assert fixed.roi_layout.reference_size == (2560, 1440)
