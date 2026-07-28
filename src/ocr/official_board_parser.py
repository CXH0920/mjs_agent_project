"""官方榜单固定版式的图像解析算法。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class OfficialBoardLayout:
    """一种官方榜单图片的固定版式。"""

    key: str
    top: float
    bottom: float
    panel_ranges: tuple[tuple[float, float], ...]
    columns: tuple[tuple[str, ...], ...]
    column_breaks: tuple[tuple[float, ...], ...]
    header_lines: tuple[int, ...]
    output_names: tuple[str, ...]
    review_names: tuple[str, ...]


LAYOUTS = {
    "2v2": OfficialBoardLayout(
        key="2v2", top=0.18, bottom=0.99, panel_ranges=((0.03, 0.48), (0.52, 0.97)),
        columns=(("排名", "武将", "胜率"), ("排名", "武将")),
        column_breaks=((0.0, 0.29, 0.69, 1.0), (0.0, 0.45, 1.0)),
        header_lines=(1, 3),
        output_names=("2v2胜率排行.csv", "2v2出场排行.csv"),
        review_names=("2v2胜率排行_待复核.csv", "2v2出场排行_待复核.csv"),
    ),
    "exile": OfficialBoardLayout(
        key="exile", top=0.28, bottom=0.99, panel_ranges=((0.03, 0.48), (0.52, 0.97)),
        columns=(("排名", "武将"), ("排名", "武将")),
        column_breaks=((0.0, 0.45, 1.0), (0.0, 0.45, 1.0)),
        header_lines=(2, 4),
        output_names=("武将放逐.csv", "武将放逐.csv"),
        review_names=("武将放逐_待复核.csv", "武将放逐_待复核.csv"),
    ),
}


def read_image(path: Path) -> np.ndarray:
    """读取包含非 ASCII 路径的本地图片。"""
    try:
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except OSError as exc:
        raise ValueError(f"无法读取图片: {exc}") from exc
    if image is None:
        raise ValueError("无法解析图片")
    return image


def extract_panels(
    image: np.ndarray,
    layout: OfficialBoardLayout,
) -> list[tuple[int, int, np.ndarray]]:
    """按固定版式裁出榜单面板。"""
    height, width = image.shape[:2]
    top, bottom = round(height * layout.top), round(height * layout.bottom)
    panels = []
    for left_ratio, right_ratio in layout.panel_ranges:
        left, right = round(width * left_ratio), round(width * right_ratio)
        panels.append((left, top, image[top:bottom, left:right]))
    return panels


def find_data_boundaries(
    panel: np.ndarray,
    image_height: int,
    layout: OfficialBoardLayout,
    panel_index: int = 0,
) -> list[int]:
    """检测表格横线并返回数据行边界。"""
    gray = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY)
    lines = cv2.HoughLinesP(
        cv2.Canny(gray, 40, 120), 1, np.pi / 180, threshold=80,
        minLineLength=max(80, panel.shape[1] // 3), maxLineGap=12,
    )
    if lines is None:
        raise ValueError(f"未检测到{layout.key}榜单横线")
    ys = sorted(
        round((line[0][1] + line[0][3]) / 2)
        for line in lines if abs(line[0][1] - line[0][3]) <= 2
    )
    groups: list[list[int]] = []
    for y in ys:
        if not groups or y > groups[-1][-1] + 6:
            groups.append([y])
        else:
            groups[-1].append(y)
    centers = [round(sum(group) / len(group)) for group in groups]
    min_gap = max(8, round(image_height * 0.002))
    max_gap = max(min_gap + 1, round(image_height * 0.011))
    runs: list[list[int]] = []
    for y in centers:
        if not runs or not min_gap <= y - runs[-1][-1] <= max_gap:
            runs.append([y])
        else:
            runs[-1].append(y)
    boundaries = max(runs, key=len, default=[])[layout.header_lines[panel_index]:]
    if len(boundaries) < 2:
        raise ValueError(f"未能定位{layout.key}榜单数据行")
    return boundaries


def restore_missing_boundaries(boundaries: list[int]) -> tuple[list[int], set[int]]:
    """按常规行高补回 Hough 漏检的中间横线。"""
    gaps = np.diff(boundaries)
    if gaps.size == 0:
        return boundaries, set()
    median_gap = float(np.median(gaps))
    if median_gap <= 0:
        return boundaries, set()

    restored = [boundaries[0]]
    for top, bottom in zip(boundaries, boundaries[1:]):
        gap = bottom - top
        segments = round(gap / median_gap)
        if gap > median_gap * 1.5 and segments > 1:
            restored.extend(round(top + gap * index / segments) for index in range(1, segments))
        restored.append(bottom)

    original_boundaries = set(boundaries)
    repaired_ranks = {
        index + 1 for index, boundary in enumerate(restored)
        if boundary not in original_boundaries
    }
    return restored, repaired_ranks


def split_row_cells(
    row: np.ndarray,
    columns: tuple[str, ...],
    column_breaks: tuple[float, ...],
) -> dict[str, np.ndarray]:
    """按版式列比例切分一行中的单元格。"""
    result = {}
    width = row.shape[1]
    for index, column in enumerate(columns):
        # 胜率首位紧贴分隔线，沿用通用内缩会截断“4”的左半边。
        left_padding = -4 if column == "胜率" else 4
        left = round(width * column_breaks[index]) + left_padding
        right = round(width * column_breaks[index + 1]) - 4
        result[column] = row[:, left:right]
    return result


def build_rank_digit_templates(
    panel: np.ndarray,
    boundaries: list[int],
    columns: tuple[str, ...],
    column_breaks: tuple[float, ...],
) -> dict[str, list[np.ndarray]]:
    """用视觉行序已知的排名格建立当前榜单字体的数字模板。"""
    templates = {str(digit): [] for digit in range(10)}
    for expected_rank, (top, bottom) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        row = panel[top + 3:bottom - 3]
        rank_cell = split_row_cells(row, columns, column_breaks)["排名"]
        glyphs = segment_glyphs(rank_cell)
        rank_text = str(expected_rank)
        if len(glyphs) != len(rank_text):
            continue
        for digit, glyph in zip(rank_text, glyphs):
            templates[digit].append(normalize_glyph(glyph))
    return templates


def prepare_rate_templates(
    panel: np.ndarray,
    boundaries: list[int],
    columns: tuple[str, ...],
    column_breaks: tuple[float, ...],
    recognize_cell: Callable[[np.ndarray], tuple[str, float]],
    progress_callback: Callable[[], None] | None = None,
) -> tuple[dict[int, tuple[str, float]], dict[str, list[np.ndarray]]]:
    """用同列胜率小数位建立数字模板，并保留排名模板作为兜底。"""
    templates = build_rank_digit_templates(panel, boundaries, columns, column_breaks)
    results: dict[int, tuple[str, float]] = {}
    for expected_rank, (top, bottom) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        row = panel[top + 3:bottom - 3]
        rate_cell = split_row_cells(row, columns, column_breaks)["胜率"]
        text, confidence = recognize_cell(rate_cell)
        results[expected_rank] = (text, confidence)
        match = re.fullmatch(r"\d{2}\.(\d{2})%?", text.replace(" ", ""))
        glyphs = segment_glyphs(rate_cell)
        if match and len(glyphs) >= 5:
            # 小数位不受当前十位数字误识问题影响，能提供同字体的可靠样本。
            for digit, glyph in zip(match.group(1), glyphs[3:5]):
                templates[digit].append(normalize_glyph(glyph))
        if progress_callback:
            progress_callback()
    return results, templates


def recognize_rate_with_templates(
    rate_cell: np.ndarray,
    templates: dict[str, list[np.ndarray]],
) -> tuple[str, float]:
    """用排名格生成的字形模板识别 ``xx.xx%`` 胜率。"""
    if not all(templates.get(str(digit)) for digit in range(10)):
        return "", 0.0
    digits: list[str] = []
    scores: list[float] = []
    for glyph in segment_glyphs(rate_cell):
        digit, score = match_digit(glyph, templates)
        if score < 0.72:
            continue
        digits.append(digit)
        scores.append(score)
        if len(digits) == 4:
            return f"{digits[0]}{digits[1]}.{digits[2]}{digits[3]}%", min(scores)
    return "", 0.0


def segment_glyphs(cell: np.ndarray) -> list[np.ndarray]:
    """按亮色连通列切分数字字形。"""
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    binary = (gray > 180).astype(np.uint8)
    columns = binary.any(axis=0)
    groups: list[list[int]] = []
    for x, has_foreground in enumerate(columns):
        if has_foreground and (not groups or x > groups[-1][-1] + 1):
            groups.append([x])
        elif has_foreground:
            groups[-1].append(x)
    glyphs = []
    for group in groups:
        glyph = binary[:, group[0]:group[-1] + 1]
        rows = np.where(glyph.any(axis=1))[0]
        if rows.size:
            glyphs.append(glyph[rows[0]:rows[-1] + 1])
    return glyphs


def normalize_glyph(glyph: np.ndarray) -> np.ndarray:
    """将字形等比缩放并居中到固定画布。"""
    target_height, target_width = 40, 28
    scale = min((target_height - 4) / glyph.shape[0], (target_width - 4) / glyph.shape[1])
    resized = cv2.resize(
        glyph, (max(1, round(glyph.shape[1] * scale)), max(1, round(glyph.shape[0] * scale))),
        interpolation=cv2.INTER_NEAREST,
    )
    canvas = np.zeros((target_height, target_width), dtype=np.uint8)
    top = (target_height - resized.shape[0]) // 2
    left = (target_width - resized.shape[1]) // 2
    canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
    return canvas


def match_digit(
    glyph: np.ndarray,
    templates: dict[str, list[np.ndarray]],
) -> tuple[str, float]:
    """返回与字形最相似的模板数字及 Dice 分数。"""
    normalized = normalize_glyph(glyph)
    best_digit, best_score = "", 0.0
    for digit, samples in templates.items():
        for sample in samples:
            intersection = np.count_nonzero(normalized & sample)
            score = 2 * intersection / (np.count_nonzero(normalized) + np.count_nonzero(sample))
            if score > best_score:
                best_digit, best_score = digit, float(score)
    return best_digit, best_score
