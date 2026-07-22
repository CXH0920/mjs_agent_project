"""官方榜单图片导入服务。"""

from __future__ import annotations

import csv
import logging
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import QThread, Signal
from src.ocr.recognizer import _HIGH_CONFIDENCE, _correct_with_hero_list

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REVIEW_DIR = PROJECT_ROOT / "screenshot_data" / "official_import"


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


class OfficialDataImportService:
    """按表格线切分官方榜单，并将识别结果写入 CSV。"""

    def __init__(self, hero_names: list[str] | None = None) -> None:
        self._hero_names = hero_names or self._load_hero_names()
        self._ocr = None

    @staticmethod
    def _load_hero_names() -> list[str]:
        heroes_path = DATA_DIR / "heroes.json"
        try:
            import json
            with heroes_path.open("r", encoding="utf-8") as file:
                return [item["name"] for item in json.load(file) if item.get("name")]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("无法加载武将词表: %s", exc)
            return []

    @property
    def _engine(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR
            logger.info("正在加载官方榜单 OCR 模型")
            self._ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
        return self._ocr

    def import_selected(self, paths: dict[str, str]) -> list[dict]:
        """执行所有已选择的导入流程。"""
        return [self.import_file(key, Path(path)) for key, path in paths.items() if path]

    def import_file(
        self,
        key: str,
        image_path: Path,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict:
        """从一张官方榜单图片识别并覆盖对应数据文件。"""
        layout = LAYOUTS[key]
        image = self._read_image(image_path)
        outputs: dict[str, dict] = {}
        panel_tasks = []
        total_steps = 0

        for panel_index, panel_data in enumerate(self._extract_panels(image, layout)):
            panel_x, panel_y, panel = panel_data
            columns = layout.columns[panel_index]
            boundaries = self._find_data_boundaries(panel, image.shape[0], layout, panel_index)
            panel_tasks.append((panel_index, panel_x, panel_y, panel, columns, boundaries))
            row_count = len(boundaries) - 1
            total_steps += row_count * (2 if "胜率" in columns else 1)

        completed_steps = 0

        def advance_progress() -> None:
            nonlocal completed_steps
            completed_steps += 1
            if progress_callback:
                progress_callback(completed_steps, total_steps)

        if progress_callback:
            progress_callback(0, total_steps)

        for panel_index, panel_x, panel_y, panel, columns, boundaries in panel_tasks:
            output_name = layout.output_names[panel_index]
            review_name = layout.review_names[panel_index]
            column_breaks = layout.column_breaks[panel_index]
            batch = outputs.setdefault(output_name, {
                "columns": columns,
                "review_name": review_name,
                "records": [],
                "reviews": [],
                "seen_names": set(),
            })
            rate_ocr_results, digit_templates = (
                self._prepare_rate_templates(
                    panel, boundaries, columns, column_breaks, advance_progress,
                )
                if "胜率" in columns else ({}, None)
            )
            for top, bottom in zip(boundaries, boundaries[1:]):
                row = panel[top + 3:bottom - 3]
                if row.size == 0:
                    advance_progress()
                    continue
                expected_rank = len(batch["records"]) + 1
                cells = self._split_row_cells(row, columns, column_breaks)
                fields = self._recognize_row(
                    row, columns, column_breaks,
                    {"胜率": rate_ocr_results[expected_rank]} if "胜率" in columns else None,
                )
                name, confidence = self._normalize_name(fields["武将"])
                record = {"排名": expected_rank, "武将": name}
                template_rate, template_score = "", 0.0
                if "胜率" in columns:
                    template_rate, template_score = self._recognize_rate_with_templates(
                        cells["胜率"], digit_templates or {},
                    )
                    record["胜率"] = template_rate or self._normalize_rate(fields["胜率"][0])
                batch["records"].append(record)

                reasons = self._review_reasons(expected_rank, fields, name, record)
                ocr_rate = self._normalize_rate(fields.get("胜率", ("", 0.0))[0])
                if template_rate and ocr_rate and template_rate != ocr_rate and template_score < 0.90:
                    reasons.append("胜率OCR与数字模板不一致")
                elif "胜率" in columns and not template_rate:
                    reasons.append("胜率数字模板识别失败")
                if name and name in batch["seen_names"]:
                    reasons.append("武将名称重复")
                batch["seen_names"].add(name)
                if reasons:
                    crop_path = self._save_review_crop(Path(output_name).stem, expected_rank, row)
                    batch["reviews"].append({
                        "期望排名": expected_rank,
                        "OCR排名": fields["排名"][0],
                        "OCR名称": fields["武将"][0],
                        "OCR胜率": fields.get("胜率", ("", 0.0))[0],
                        "数字模板胜率": template_rate,
                        "数字模板置信度": f"{template_score:.4f}",
                        "置信度": f"{confidence:.4f}",
                        "异常原因": "；".join(reasons),
                        "原图坐标": f"{panel_x},{panel_y + top},{panel_x + panel.shape[1]},{panel_y + bottom}",
                        "行截图路径": str(crop_path),
                    })
                advance_progress()

        if not outputs:
            raise ValueError("未检测到任何数据行")
        for output_name, batch in outputs.items():
            records = batch["records"]
            self._write_csv(DATA_DIR / output_name, list(records[0]), records)
            self._write_csv(
                DATA_DIR / batch["review_name"],
                ["期望排名", "OCR排名", "OCR名称", "OCR胜率", "数字模板胜率", "数字模板置信度", "置信度", "异常原因", "原图坐标", "行截图路径"],
                batch["reviews"],
            )
        if "2v2胜率排行.csv" in outputs:
            from src.data.win_rate_repository import clear_win_rate_cache
            clear_win_rate_cache()
        record_count = sum(len(batch["records"]) for batch in outputs.values())
        review_count = sum(len(batch["reviews"]) for batch in outputs.values())
        logger.info("官方%s榜单导入完成: %d 条，待复核 %d 条", layout.key, record_count, review_count)
        return {"name": layout.key, "records": record_count, "reviews": review_count, "outputs": [DATA_DIR / name for name in outputs]}

    @staticmethod
    def _read_image(path: Path) -> np.ndarray:
        try:
            image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except OSError as exc:
            raise ValueError(f"无法读取图片: {exc}") from exc
        if image is None:
            raise ValueError("无法解析图片")
        return image

    @staticmethod
    def _extract_panels(image: np.ndarray, layout: OfficialBoardLayout) -> list[tuple[int, int, np.ndarray]]:
        height, width = image.shape[:2]
        top, bottom = round(height * layout.top), round(height * layout.bottom)
        panels = []
        for left_ratio, right_ratio in layout.panel_ranges:
            left, right = round(width * left_ratio), round(width * right_ratio)
            panels.append((left, top, image[top:bottom, left:right]))
        return panels

    @staticmethod
    def _find_data_boundaries(
        panel: np.ndarray,
        image_height: int,
        layout: OfficialBoardLayout,
        panel_index: int = 0,
    ) -> list[int]:
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

    def _recognize_row(
        self,
        row: np.ndarray,
        columns: tuple[str, ...],
        column_breaks: tuple[float, ...],
        precomputed: dict[str, tuple[str, float]] | None = None,
    ) -> dict[str, tuple[str, float]]:
        return {
            column: precomputed[column] if precomputed and column in precomputed else (
                self._recognize_name_cell(cell) if column == "武将" else self._recognize_cell(cell)
            )
            for column, cell in self._split_row_cells(row, columns, column_breaks).items()
        }

    @staticmethod
    def _split_row_cells(
        row: np.ndarray,
        columns: tuple[str, ...],
        column_breaks: tuple[float, ...],
    ) -> dict[str, np.ndarray]:
        result = {}
        width = row.shape[1]
        for index, column in enumerate(columns):
            # 胜率首位紧贴分隔线，沿用通用内缩会截断“4”的左半边。
            left_padding = -4 if column == "胜率" else 4
            left = round(width * column_breaks[index]) + left_padding
            right = round(width * column_breaks[index + 1]) - 4
            result[column] = row[:, left:right]
        return result

    def _build_rank_digit_templates(
        self,
        panel: np.ndarray,
        boundaries: list[int],
        columns: tuple[str, ...],
        column_breaks: tuple[float, ...],
    ) -> dict[str, list[np.ndarray]]:
        """用视觉行序已知的排名格建立当前榜单字体的数字模板。"""
        templates = {str(digit): [] for digit in range(10)}
        for expected_rank, (top, bottom) in enumerate(zip(boundaries, boundaries[1:]), start=1):
            row = panel[top + 3:bottom - 3]
            rank_cell = self._split_row_cells(row, columns, column_breaks)["排名"]
            glyphs = self._segment_glyphs(rank_cell)
            rank_text = str(expected_rank)
            if len(glyphs) != len(rank_text):
                continue
            for digit, glyph in zip(rank_text, glyphs):
                templates[digit].append(self._normalize_glyph(glyph))
        return templates

    def _prepare_rate_templates(
        self,
        panel: np.ndarray,
        boundaries: list[int],
        columns: tuple[str, ...],
        column_breaks: tuple[float, ...],
        progress_callback: Callable[[], None] | None = None,
    ) -> tuple[dict[int, tuple[str, float]], dict[str, list[np.ndarray]]]:
        """用同列胜率小数位建立数字模板，并保留排名模板作为兜底。"""
        templates = self._build_rank_digit_templates(panel, boundaries, columns, column_breaks)
        results: dict[int, tuple[str, float]] = {}
        for expected_rank, (top, bottom) in enumerate(zip(boundaries, boundaries[1:]), start=1):
            row = panel[top + 3:bottom - 3]
            rate_cell = self._split_row_cells(row, columns, column_breaks)["胜率"]
            text, confidence = self._recognize_cell(rate_cell)
            results[expected_rank] = (text, confidence)
            match = re.fullmatch(r"\d{2}\.(\d{2})%?", text.replace(" ", ""))
            glyphs = self._segment_glyphs(rate_cell)
            if match and len(glyphs) >= 5:
                # 小数位不受当前十位数字误识问题影响，能提供同字体的可靠样本。
                for digit, glyph in zip(match.group(1), glyphs[3:5]):
                    templates[digit].append(self._normalize_glyph(glyph))
            if progress_callback:
                progress_callback()
        return results, templates

    def _recognize_rate_with_templates(
        self,
        rate_cell: np.ndarray,
        templates: dict[str, list[np.ndarray]],
    ) -> tuple[str, float]:
        """用排名格生成的字形模板识别 ``xx.xx%`` 胜率。"""
        if not all(templates.get(str(digit)) for digit in range(10)):
            return "", 0.0
        digits: list[str] = []
        scores: list[float] = []
        for glyph in self._segment_glyphs(rate_cell):
            digit, score = self._match_digit(glyph, templates)
            if score < 0.72:
                continue
            digits.append(digit)
            scores.append(score)
            if len(digits) == 4:
                return f"{digits[0]}{digits[1]}.{digits[2]}{digits[3]}%", min(scores)
        return "", 0.0

    @staticmethod
    def _segment_glyphs(cell: np.ndarray) -> list[np.ndarray]:
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

    @staticmethod
    def _normalize_glyph(glyph: np.ndarray) -> np.ndarray:
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

    def _match_digit(self, glyph: np.ndarray, templates: dict[str, list[np.ndarray]]) -> tuple[str, float]:
        normalized = self._normalize_glyph(glyph)
        best_digit, best_score = "", 0.0
        for digit, samples in templates.items():
            for sample in samples:
                intersection = np.count_nonzero(normalized & sample)
                score = 2 * intersection / (np.count_nonzero(normalized) + np.count_nonzero(sample))
                if score > best_score:
                    best_digit, best_score = digit, float(score)
        return best_digit, best_score

    def _recognize_cell_candidates(self, cell: np.ndarray) -> list[tuple[str, float]]:
        if cell.size == 0:
            return []
        enlarged = cv2.resize(cell, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        candidates = [enlarged]
        lab = cv2.cvtColor(enlarged, cv2.COLOR_BGR2LAB)
        lightness, a_channel, b_channel = cv2.split(lab)
        lightness = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
        enhanced = cv2.cvtColor(cv2.merge([lightness, a_channel, b_channel]), cv2.COLOR_LAB2BGR)
        candidates.append(cv2.filter2D(enhanced, -1, np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])))
        results: list[tuple[str, float]] = []
        for candidate in candidates:
            ocr_result = self._engine.ocr(candidate, cls=False)
            for line in ocr_result[0] if ocr_result and ocr_result[0] else []:
                text, confidence = line[1]
                if text:
                    results.append((text.replace(" ", ""), float(confidence)))
        return results

    def _recognize_cell(self, cell: np.ndarray) -> tuple[str, float]:
        candidates = self._recognize_cell_candidates(cell)
        return max(candidates, key=lambda item: item[1], default=("", 0.0))

    def _recognize_name_cell(self, cell: np.ndarray) -> tuple[str, float]:
        """优先选用词表中的完整 OCR 候选，单字结果再逐字补识别。"""
        candidates = self._recognize_cell_candidates(cell)
        if not candidates:
            return "", 0.0
        exact_matches = [
            ("".join(re.findall(r"[\u4e00-\u9fff]", text)), confidence)
            for text, confidence in candidates
            if "".join(re.findall(r"[\u4e00-\u9fff]", text)) in self._hero_names
        ]
        if exact_matches:
            return max(exact_matches, key=lambda item: item[1])

        text, confidence = max(candidates, key=lambda item: item[1])
        name = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        if len(name) != 1:
            return text, confidence

        glyph_name, glyph_confidence = self._recognize_name_glyphs(cell)
        if glyph_name:
            corrected = _correct_with_hero_list(glyph_name, self._hero_names)
            if corrected in self._hero_names:
                return corrected, glyph_confidence
        prefix_matches = [hero for hero in self._hero_names if hero.startswith(name)]
        if len(prefix_matches) == 1:
            return prefix_matches[0], confidence
        return text, confidence

    def _recognize_name_glyphs(self, cell: np.ndarray) -> tuple[str, float]:
        """按亮色字形切分名称格，并在保留背景留白后逐字识别。"""
        gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        columns = (gray > 180).any(axis=0)
        groups: list[tuple[int, int]] = []
        start = None
        for x, has_foreground in enumerate(columns):
            if has_foreground and start is None:
                start = x
            elif not has_foreground and start is not None:
                groups.append((start, x - 1))
                start = None
        if start is not None:
            groups.append((start, len(columns) - 1))
        if not 2 <= len(groups) <= 4:
            return "", 0.0

        background = tuple(int(value) for value in np.median(cell.reshape(-1, 3), axis=0))
        characters: list[str] = []
        confidences: list[float] = []
        for left, right in groups:
            glyph = cell[:, max(0, left - 5):min(cell.shape[1], right + 6)]
            glyph = cv2.copyMakeBorder(glyph, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=background)
            text, confidence = self._recognize_cell(glyph)
            character = "".join(re.findall(r"[\u4e00-\u9fff]", text))
            if len(character) != 1:
                return "", 0.0
            characters.append(character)
            confidences.append(confidence)
        return "".join(characters), min(confidences)

    def _normalize_name(self, value: tuple[str, float]) -> tuple[str, float]:
        text, confidence = value
        name = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        if not name or not self._hero_names:
            return name, confidence
        if len(name) == 1:
            return name, confidence
        if confidence >= _HIGH_CONFIDENCE and name not in self._hero_names:
            return name, confidence
        return _correct_with_hero_list(name, self._hero_names), confidence

    @staticmethod
    def _normalize_rate(text: str) -> str:
        match = re.search(r"\d{1,3}(?:\.\d+)?", text)
        return f"{match.group(0)}%" if match else ""

    @staticmethod
    def _review_reasons(expected_rank: int, fields: dict[str, tuple[str, float]], name: str, record: dict) -> list[str]:
        reasons = []
        rank_match = re.search(r"\d+", fields["排名"][0])
        if rank_match and int(rank_match.group()) != expected_rank:
            reasons.append("排名OCR与行序不一致")
        if len(name) < 2:
            reasons.append("武将名称疑似缺字")
        elif not re.fullmatch(r"[\u4e00-\u9fff]{1,8}", name):
            reasons.append("武将名称为空或包含异常字符")
        elif fields["武将"][1] < 0.75:
            reasons.append("武将名称置信度低")
        if "胜率" in record and not record["胜率"]:
            reasons.append("胜率识别失败")
        return reasons

    @staticmethod
    def _save_review_crop(kind: str, rank: int, row: np.ndarray) -> Path:
        directory = REVIEW_DIR / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{rank:03d}.png"
        Image.fromarray(cv2.cvtColor(row, cv2.COLOR_BGR2RGB)).save(path)
        return path

    @staticmethod
    def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temp_path = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        temp_path.replace(path)


class OfficialDataImportWorker(QThread):
    """在后台线程执行一个或两个官方榜单导入任务。"""

    completed = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(str, int, int)

    def __init__(self, paths: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._paths = paths

    def run(self) -> None:
        try:
            service = OfficialDataImportService()
            selected_paths = [(key, path) for key, path in self._paths.items() if path]
            summaries = []
            for index, (key, path) in enumerate(selected_paths, start=1):
                name = "2v2数据" if key == "2v2" else "武将放逐数据"
                status = f"正在导入{name}（{index}/{len(selected_paths)}）"
                self.progress_changed.emit(status, 0, 0)
                summaries.append(service.import_file(
                    key,
                    Path(path),
                    lambda current, total, text=status: self.progress_changed.emit(text, current, total),
                ))
            self.completed.emit(summaries)
        except Exception as exc:
            logger.exception("官方榜单导入失败")
            self.failed.emit(str(exc))
