"""2v2 卡位检测测试：合成图覆盖布局/干扰/拒绝场景，真图做本地回归。

真图样本放 tests/fixtures_local/2v2/（不入库，缺失时自动跳过）：
ban_phase_14.png（禁选期 7+7）、pool_9.png（候选期 4+5）、pool_10.png（候选期 5+5）。
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from src.ocr.card_grid_detector import derive_name_rois, detect_selection_cards

FIXTURE_DIR = Path(__file__).parent / "fixtures_local" / "2v2"
CANVAS_W, CANVAS_H = 2560, 1440
CARD_W, CARD_H = 238, 326
PITCH_X = 276

REAL_CASES = [
    ("ban_phase_14.png", 14, (7, 7)),
    ("pool_9.png", 9, (4, 5)),
    ("pool_10.png", 10, (5, 5)),
]


def _parchment_canvas(width: int = CANVAS_W, height: int = CANVAS_H) -> np.ndarray:
    """宣纸底色：低饱和高明度，与真实页面背景一致。"""
    return np.full((height, width, 3), (232, 231, 230), dtype=np.uint8)


def _draw_card(
    canvas: np.ndarray,
    x: int,
    y: int,
    w: int = CARD_W,
    h: int = CARD_H,
    seed: int = 0,
) -> None:
    """画一张深色卡面并挖淡色斑块，模拟淡色立绘降低掩码填充率的真实情况。"""
    rng = np.random.default_rng(seed)
    # 各通道 ≤85 保证 V<90 恒入掩码，避免随机出"灰而不够暗"的背景色
    color = tuple(int(value) for value in rng.integers(15, 86, size=3))
    cv2.rectangle(canvas, (x, y), (x + w - 1, y + h - 1), color, thickness=-1)
    for _ in range(3):
        px = int(rng.integers(x + 12, x + w - 42))
        py = int(rng.integers(y + 12, y + h - 42))
        cv2.rectangle(canvas, (px, py), (px + 28, py + 28), (215, 218, 222), thickness=-1)


def _draw_row(canvas: np.ndarray, x_start: int, y: int, count: int, seed: int) -> None:
    for i in range(count):
        _draw_card(canvas, x_start + i * PITCH_X, y, seed=seed + i)


def _draw_page_distractors(canvas: np.ndarray) -> None:
    """复现页面干扰元素：巨型深色边框、顶部饰件条、席位标签、进度条、卡片区外假卡。"""
    cv2.rectangle(canvas, (30, 25), (2530, 1415), (40, 40, 40), thickness=10)
    cv2.rectangle(canvas, (600, 180), (1770, 202), (70, 70, 70), thickness=-1)
    cv2.rectangle(canvas, (620, 950), (1000, 990), (60, 60, 60), thickness=-1)
    cv2.rectangle(canvas, (1600, 950), (1980, 990), (60, 60, 60), thickness=-1)
    cv2.rectangle(canvas, (700, 1330), (1900, 1345), (50, 50, 50), thickness=-1)
    _draw_card(canvas, 300, 1080, seed=999)


def test_detects_ban_phase_7x7_with_distractors():
    """禁选期 14 张（7+7）在干扰元素环绕下全部检出且行优先排序。"""
    canvas = _parchment_canvas()
    _draw_page_distractors(canvas)
    _draw_row(canvas, 340, 280, 7, seed=100)
    _draw_row(canvas, 339, 617, 7, seed=200)

    cards = detect_selection_cards(canvas)

    assert cards is not None
    assert len(cards) == 14
    row1, row2 = cards[:7], cards[7:]
    assert [card[0] for card in row1] == [340 + i * PITCH_X for i in range(7)]
    assert all(card[1] == 280 for card in row1)
    assert [card[0] for card in row2] == [339 + i * PITCH_X for i in range(7)]
    assert all(card[1] == 617 for card in row2)
    assert all(card[2] == CARD_W and card[3] == CARD_H for card in cards)


def test_detects_pool_9_with_touching_rows():
    """候选期 9 张（4+5）：上下行间隙仅 6px（真实页面近乎贴行）不得粘连。"""
    canvas = _parchment_canvas()
    _draw_row(canvas, 752, 247, 4, seed=300)
    _draw_row(canvas, 609, 579, 5, seed=400)

    cards = detect_selection_cards(canvas)

    assert cards is not None
    assert len(cards) == 9
    assert _row_sizes(cards) == (4, 5)


def test_detects_pool_10():
    """候选期 10 张（5+5）全部检出。"""
    canvas = _parchment_canvas()
    _draw_row(canvas, 614, 247, 5, seed=500)
    _draw_row(canvas, 614, 579, 5, seed=600)

    cards = detect_selection_cards(canvas)

    assert cards is not None
    assert len(cards) == 10
    assert _row_sizes(cards) == (5, 5)


def test_rejects_standard_single_row_page():
    """标准选将页（单行 8 张、卡高 367）不满足 2v2 卡高窗，整体拒绝。"""
    canvas = _parchment_canvas()
    for i in range(8):
        _draw_card(canvas, 141 + i * 282, 303, w=268, h=367, seed=700 + i)

    assert detect_selection_cards(canvas) is None


def test_rejects_when_card_count_below_range():
    """卡数不足 8 张时返回 None，不得输出残缺牌面。"""
    canvas = _parchment_canvas()
    _draw_row(canvas, 614, 247, 5, seed=800)

    assert detect_selection_cards(canvas) is None


def test_rejects_plain_background():
    """纯宣纸背景（无任何卡牌）返回 None。"""
    assert detect_selection_cards(_parchment_canvas()) is None


def test_lower_resolution_adapts():
    """半分辨率（1280×720）下同一 5+5 布局仍可检出，参数按比例自适应。"""
    canvas = _parchment_canvas(width=1280, height=720)
    for i in range(5):
        _draw_card(canvas, 307 + i * 138, 124, w=119, h=163, seed=900 + i)
        _draw_card(canvas, 307 + i * 138, 290, w=119, h=163, seed=950 + i)

    cards = detect_selection_cards(canvas)

    assert cards is not None
    assert len(cards) == 10
    assert _row_sizes(cards) == (5, 5)
    assert cards[0] == (307, 124, 119, 163)


def test_derive_name_rois_matches_card_proportions():
    """名条 ROI 按卡内相对比例生成，且顺序与输入一致。"""
    cards = [(340, 280, CARD_W, CARD_H), (616, 280, CARD_W, CARD_H)]

    rois = derive_name_rois(cards)

    assert rois[0] == (354, 329, 71, 124)
    for card, roi in zip(cards, rois, strict=False):
        x, y, w, h = card
        assert x <= roi[0] and roi[0] + roi[2] <= x + w
        assert y <= roi[1] and roi[1] + roi[3] <= y + h


def _row_sizes(cards: list) -> tuple:
    """按行顶 y 聚类统计各行卡数（同行 y 差 ≤ 数像素，跨行差 >300px）。"""
    rows: list[list[int]] = []
    for card in sorted(cards, key=lambda item: item[1]):
        for row in rows:
            if abs(card[1] - row[0]) < 50:
                row.append(card[1])
                break
        else:
            rows.append([card[1]])
    return tuple(len(row) for row in rows)


@pytest.mark.parametrize("filename,expected_count,expected_rows", REAL_CASES)
def test_real_screenshot_detection(filename, expected_count, expected_rows):
    """真图回归：本地样本缺失时跳过；命中时断言卡数、行结构并打印实测耗时。"""
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"缺少本地真图样本: {path}")
    image = cv2.imread(str(path))
    assert image is not None, f"真图读取失败: {path}"

    started = time.perf_counter()
    cards = detect_selection_cards(image)
    elapsed_ms = (time.perf_counter() - started) * 1000

    print(f"{filename} 检出 {len(cards) if cards else 0} 张，耗时 {elapsed_ms:.1f}ms")
    assert cards is not None
    assert len(cards) == expected_count
    assert _row_sizes(cards) == expected_rows
