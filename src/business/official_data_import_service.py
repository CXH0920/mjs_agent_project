"""官方榜单图片导入服务。"""

from __future__ import annotations

import csv
import logging
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import QThread, Signal
from src.data.recommendation_index_repository import mark_recommendation_index_stale
from src.ocr import official_board_parser
from src.ocr.character_similarity import CharacterSimilarityService
from src.ocr.official_board_parser import LAYOUTS

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REVIEW_DIR = PROJECT_ROOT / "screenshot_data" / "official_import"


class OfficialDataImportService:
    """按表格线切分官方榜单，并将识别结果写入 CSV。"""

    def __init__(self, hero_names: list[str] | None = None) -> None:
        self._hero_names = hero_names or self._load_hero_names()
        self._name_corrector = CharacterSimilarityService()
        self._ocr = None
        self._rare_char_ocr = None
        self._rare_char_engine_failed = False

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

    @property
    def _rare_char_engine(self):
        """按需加载繁体模型，以覆盖简体模型字典外的罕见字。"""
        if self._rare_char_engine_failed:
            return None
        if self._rare_char_ocr is None:
            try:
                from paddleocr import PaddleOCR
                logger.info("正在加载官方榜单罕见字 OCR 模型")
                self._rare_char_ocr = PaddleOCR(
                    use_angle_cls=False, lang="chinese_cht", show_log=False,
                )
            except Exception as exc:
                logger.warning("罕见字 OCR 模型不可用，将保留原结果待复核: %s", exc)
                self._rare_char_engine_failed = True
                return None
        return self._rare_char_ocr

    def import_selected(self, paths: dict[str, str]) -> list[dict]:
        """执行所有已选择的导入流程。"""
        return [self.import_file(key, Path(path)) for key, path in paths.items() if path]

    def import_file(
        self,
        key: str,
        image_path: Path,
        progress_callback: Callable[[int, int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict:
        """从一张官方榜单图片识别并覆盖对应数据文件。"""
        layout = LAYOUTS[key]
        image = official_board_parser.read_image(image_path)
        outputs: dict[str, dict] = {}
        panel_tasks = []
        total_steps = 0

        for panel_index, panel_data in enumerate(official_board_parser.extract_panels(image, layout)):
            panel_x, panel_y, panel = panel_data
            columns = layout.columns[panel_index]
            boundaries = official_board_parser.find_data_boundaries(
                panel, image.shape[0], layout, panel_index,
            )
            boundaries, repaired_ranks = official_board_parser.restore_missing_boundaries(boundaries)
            panel_tasks.append((panel_index, panel_x, panel_y, panel, columns, boundaries, repaired_ranks))
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

        for panel_index, panel_x, panel_y, panel, columns, boundaries, repaired_ranks in panel_tasks:
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
                official_board_parser.prepare_rate_templates(
                    panel, boundaries, columns, column_breaks, self._recognize_cell, advance_progress,
                )
                if "胜率" in columns else ({}, None)
            )
            for top, bottom in zip(boundaries, boundaries[1:]):
                row = panel[top + 3:bottom - 3]
                if row.size == 0:
                    advance_progress()
                    continue
                expected_rank = len(batch["records"]) + 1
                cells = official_board_parser.split_row_cells(row, columns, column_breaks)
                fields = self._recognize_row(
                    row, columns, column_breaks,
                    {"胜率": rate_ocr_results[expected_rank]} if "胜率" in columns else None,
                    status_callback,
                )
                name, confidence = self._normalize_name(fields["武将"])
                record = {"排名": expected_rank, "武将": name}
                template_rate, template_score = "", 0.0
                if "胜率" in columns:
                    template_rate, template_score = official_board_parser.recognize_rate_with_templates(
                        cells["胜率"], digit_templates or {},
                    )
                    record["胜率"] = template_rate or self._normalize_rate(fields["胜率"][0])
                batch["records"].append(record)

                reasons = self._review_reasons(expected_rank, fields, name, record)
                unresolved_name_reason = self._unresolved_name_reason(name)
                if unresolved_name_reason:
                    reasons.append(unresolved_name_reason)
                if expected_rank in repaired_ranks:
                    reasons.append("检测到缺失表格横线，已按行高补全")
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
        mark_recommendation_index_stale(True)
        if "2v2胜率排行.csv" in outputs:
            from src.data.win_rate_repository import clear_win_rate_cache
            clear_win_rate_cache()
        record_count = sum(len(batch["records"]) for batch in outputs.values())
        review_count = sum(len(batch["reviews"]) for batch in outputs.values())
        logger.info("官方%s榜单导入完成: %d 条，待复核 %d 条", layout.key, record_count, review_count)
        return {"name": layout.key, "records": record_count, "reviews": review_count, "outputs": [DATA_DIR / name for name in outputs]}

    def _recognize_row(
        self,
        row: np.ndarray,
        columns: tuple[str, ...],
        column_breaks: tuple[float, ...],
        precomputed: dict[str, tuple[str, float]] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, tuple[str, float]]:
        return {
            column: precomputed[column] if precomputed and column in precomputed else (
                self._recognize_name_cell(cell, status_callback) if column == "武将" else self._recognize_cell(cell)
            )
            for column, cell in official_board_parser.split_row_cells(
                row, columns, column_breaks,
            ).items()
        }

    def _recognize_cell_candidates(self, cell: np.ndarray, engine=None) -> list[tuple[str, float]]:
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
            ocr_result = (engine if engine is not None else self._engine).ocr(candidate, cls=False)
            for line in ocr_result[0] if ocr_result and ocr_result[0] else []:
                text, confidence = line[1]
                if text:
                    results.append((text.replace(" ", ""), float(confidence)))
        return results

    def _recognize_cell(self, cell: np.ndarray, engine=None) -> tuple[str, float]:
        candidates = (
            self._recognize_cell_candidates(cell, engine)
            if engine is not None else self._recognize_cell_candidates(cell)
        )
        return max(candidates, key=lambda item: item[1], default=("", 0.0))

    @staticmethod
    def _chinese_text(text: str) -> str:
        return "".join(re.findall(r"[\u4e00-\u9fff]", text))

    def _exact_hero_matches(
        self, candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        return [
            (self._chinese_text(text), confidence)
            for text, confidence in candidates
            if self._chinese_text(text) in self._hero_names
        ]

    @staticmethod
    def _select_unique_name_match(
        matches: list[tuple[str, float]],
    ) -> tuple[tuple[str, float] | None, tuple[str, ...]]:
        names = tuple(dict.fromkeys(name for name, _confidence in matches))
        if len(names) == 1:
            return max(matches, key=lambda item: item[1]), ()
        return None, names

    @staticmethod
    def _common_prefix(names: tuple[str, ...] | list[str]) -> str:
        if not names:
            return ""
        prefix = names[0]
        for name in names[1:]:
            while prefix and not name.startswith(prefix):
                prefix = prefix[:-1]
        return prefix

    def _strict_prefix_matches(self, name: str) -> list[str]:
        return [hero for hero in self._hero_names if len(hero) > len(name) and hero.startswith(name)]

    def _nearby_hero_names(self, name: str) -> list[str]:
        return [
            hero for hero in self._hero_names
            if CharacterSimilarityService._levenshtein_distance(name, hero)
            <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
        ]

    def _ambiguous_name_candidates(self, name: str) -> list[str]:
        prefix_matches = self._strict_prefix_matches(name)
        if len(prefix_matches) > 1:
            return prefix_matches
        nearby_names = self._nearby_hero_names(name)
        if len(nearby_names) > 1 and len(self._common_prefix(nearby_names)) >= 2:
            return nearby_names
        return []

    def _correct_official_name(self, name: str) -> str:
        """保留复姓公共前缀歧义，避免词表扩充后静默改绑。"""
        if not name or name in self._hero_names or self._ambiguous_name_candidates(name):
            return name
        return self._name_corrector.correct_hero_name(name, self._hero_names)

    def _unresolved_name_reason(self, name: str) -> str:
        if not name or name in self._hero_names:
            return ""
        ambiguous_candidates = self._ambiguous_name_candidates(name)
        if ambiguous_candidates:
            return f"武将名称候选不唯一：{'/'.join(ambiguous_candidates)}"
        return "武将名称未命中词表"

    def _recognize_name_with_engine(self, cell: np.ndarray, engine) -> tuple[str, float]:
        """使用指定引擎识别名称，仅接受词表中可确认的完整结果。"""
        candidates = self._recognize_cell_candidates(cell, engine)
        exact_match, exact_conflicts = self._select_unique_name_match(self._exact_hero_matches(candidates))
        if exact_match:
            return exact_match
        if exact_conflicts:
            logger.warning("官方榜单武将精确候选冲突: %s", exact_conflicts)
            return self._common_prefix(exact_conflicts), max(confidence for _text, confidence in candidates)
        corrected_matches = []
        for text, confidence in candidates:
            candidate_name = self._chinese_text(text)
            if len(candidate_name) < 2:
                continue
            corrected = self._correct_official_name(candidate_name)
            if corrected in self._hero_names:
                corrected_matches.append((corrected, confidence))
        corrected_match, corrected_conflicts = self._select_unique_name_match(corrected_matches)
        if corrected_match:
            return corrected_match
        if corrected_conflicts:
            logger.warning("官方榜单武将校正候选冲突: %s", corrected_conflicts)
            return self._common_prefix(corrected_conflicts), max(confidence for _text, confidence in candidates)
        glyph_name, glyph_confidence = self._recognize_name_glyphs(cell, engine)
        if glyph_name:
            corrected = self._correct_official_name(glyph_name)
            if corrected in self._hero_names:
                return corrected, glyph_confidence
        return "", 0.0

    def _recognize_name_cell(
        self,
        cell: np.ndarray,
        status_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, float]:
        """优先选用词表中的完整 OCR 候选，单字结果再逐字补识别。"""
        candidates = self._recognize_cell_candidates(cell)
        if not candidates:
            return "", 0.0
        exact_match, exact_conflicts = self._select_unique_name_match(self._exact_hero_matches(candidates))
        if exact_match:
            return exact_match
        if exact_conflicts:
            logger.warning("官方榜单武将精确候选冲突: %s", exact_conflicts)
            text = self._common_prefix(exact_conflicts)
            confidence = max(confidence for _text, confidence in candidates)
        else:
            text, confidence = max(candidates, key=lambda item: item[1])
        name = self._chinese_text(text)
        ambiguous_candidates = self._ambiguous_name_candidates(name)
        if len(name) != 1 and not ambiguous_candidates:
            return text, confidence

        glyph_name, glyph_confidence = self._recognize_name_glyphs(cell)
        if glyph_name:
            corrected = self._correct_official_name(glyph_name)
            if corrected in self._hero_names:
                return corrected, glyph_confidence
        prefix_matches = [hero for hero in self._hero_names if hero.startswith(name)]
        if len(prefix_matches) == 1:
            return prefix_matches[0], confidence
        if status_callback:
            status_callback("正在执行罕见字兜底识别")
        rare_char_engine = self._rare_char_engine
        if rare_char_engine is not None:
            try:
                rare_name, rare_confidence = self._recognize_name_with_engine(cell, rare_char_engine)
                if rare_name:
                    return rare_name, rare_confidence
            except Exception as exc:
                logger.warning("罕见字 OCR 识别失败，将保留原结果待复核: %s", exc)
                self._rare_char_engine_failed = True
        return text, confidence

    def _recognize_name_glyphs(self, cell: np.ndarray, engine=None) -> tuple[str, float]:
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
            text, confidence = (
                self._recognize_cell(glyph, engine)
                if engine is not None else self._recognize_cell(glyph)
            )
            character = self._chinese_text(text)
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
        return self._correct_official_name(name), confidence

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
        ocr_name = "".join(re.findall(r"[\u4e00-\u9fff]", fields["武将"][0]))
        if ocr_name and ocr_name != name:
            reasons.append("武将名称已由词表校正")
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
                    lambda text, prefix=status: self.progress_changed.emit(f"{prefix}：{text}", -1, -1),
                ))
            self.completed.emit(summaries)
        except Exception as exc:
            logger.exception("官方榜单导入失败")
            self.failed.emit(str(exc))
