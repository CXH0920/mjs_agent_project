# -*- coding: utf-8 -*-
"""template_manager 同名测试：模板匹配缩放策略与跨实例缩放历史（D5 锚点迁移，自 test_ocr_scaling 归位）。"""

from __future__ import annotations

import cv2
import numpy as np
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


def test_template_coarse_probe_rejects_unrelated_page_with_single_scale(tmp_path, monkeypatch) -> None:
    """完全非目标页由基准档单档粗扫直接判否，不再展开其余档位粗扫（A2）。"""
    source = np.zeros((144, 256, 3), dtype=np.uint8)
    rng = np.random.default_rng(42)
    source[40:80, 90:150] = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    manager = TemplateManager(tmp_path / "template.png")
    manager.set_template(source, (90, 40, 60, 40))

    unrelated = np.zeros_like(source)
    unrelated[10:50, 20:80] = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    coarse_calls: list[float] = []
    original = TemplateManager._match_at_coarse_scale

    def _counting_coarse(self, coarse_gray, coarse_scale):
        coarse_calls.append(coarse_scale)
        return original(self, coarse_gray, coarse_scale)

    monkeypatch.setattr(TemplateManager, "_match_at_coarse_scale", _counting_coarse)
    matched, confidence = manager.match(unrelated, threshold=0.8)

    assert not matched
    assert confidence < 0.65
    assert manager.last_match_strategy == "coarse_reject_probe"
    assert len(coarse_calls) == 1  # 仅基准档一次粗扫，其余档位全部免去
