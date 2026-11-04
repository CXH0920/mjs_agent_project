"""官方榜单单格识别策略域（自 OfficialDataImportService 拆出）。

单元格图像 → OCR 候选 → 采信判定的全部策略：预处理候选管线、快通道、
罕见字兜底与逐字识别。变更原因是「OCR 识别质量调优」（阈值/预处理/
兜底链），与导入编排（版式/流程）互不相关，故独立成模块。依赖
OfficialOcrEngines 与 HeroNameResolver，零编排知识。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import cv2
import numpy as np
from src.business.recognition.name_resolution import HeroNameResolver
from src.business.recognition.official_ocr_engines import OfficialOcrEngines
from src.ocr import official_board_parser
from src.ocr.character_similarity import CharacterSimilarityService, levenshtein_distance

logger = logging.getLogger(__name__)

# 单格识别快通道：2× 放大单候选对干净格与 4× 双候选结果完全一致（2026-10
# 七种版式语料 ×120 格逐格比对零不一致），单次引擎调用耗时约 1/4（det 输入
# 面积主导）。数字格需匹配"纯排名/胜率"格式、名称格需词表精确命中且置信度
# 达标才采信，未过门的困难格原样回退 4× 双候选路径，精度下界不变。
FAST_PATH_MIN_CONFIDENCE = 0.90
FAST_PATH_NUMERIC_PATTERN = re.compile(r"\d{1,3}(?:\.\d{2})?%?")


class OfficialCellRecognizer:
    """官方榜单单格 OCR 识别策略：行分派、候选管线、快通道与罕见字兜底。"""

    def __init__(self, engines: OfficialOcrEngines, names: HeroNameResolver) -> None:
        self._engines = engines
        self._names = names

    @property
    def _rare_char_engine(self):
        # 委托到 OfficialOcrEngines.rare_char；保留 property 使既有调用点与
        # 测试的类级 patch（monkeypatch.setattr(类, "_rare_char_engine", ...）继续生效
        return self._engines.rare_char

    @property
    def _rare_char_engine_failed(self) -> bool:
        return self._engines.rare_char_failed

    def recognize_row(
        self,
        row: np.ndarray,
        columns: tuple[str, ...],
        column_breaks: tuple[float, ...],
        precomputed: dict[str, tuple[str, float]] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, tuple[str, float]]:
        return {
            column: precomputed[column] if precomputed and column in precomputed else (
                self.recognize_name_cell(cell, status_callback) if column == "武将" else self.recognize_cell(cell)
            )
            for column, cell in official_board_parser.split_row_cells(
                row, columns, column_breaks,
            ).items()
        }

    def recognize_cell_candidates(self, cell: np.ndarray, engine=None) -> list[tuple[str, float]]:
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
            # 恢复完整检测+识别流程：det 网络先框出文字区域，避免整格输入产生的边缘幻觉
            ocr_result = (engine if engine is not None else self._engines.main).ocr(
                candidate, cls=False,
            )
            results.extend(self._parse_ocr_lines(ocr_result))
        return results

    @staticmethod
    def _parse_ocr_lines(ocr_result) -> list[tuple[str, float]]:
        """把引擎返回压平为 (文本, 置信度) 列表。"""
        lines: list[tuple[str, float]] = []
        for line in ocr_result[0] if ocr_result and ocr_result[0] else []:
            # 完整流程 line 为 [box, (text, confidence)]
            if isinstance(line[0], (list, tuple)):
                text, confidence = line[1]
            else:
                text, confidence = line
            if text:
                lines.append((text.replace(" ", ""), float(confidence)))
        return lines

    def _fast_path_cell(self, cell: np.ndarray) -> list[tuple[str, float]]:
        """2× 放大单候选快速识别；引擎未注入（单测/懒加载场景）时不启用。"""
        engine = self._engines.ocr
        if engine is None or cell.size == 0:
            return []
        enlarged = cv2.resize(cell, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        return self._parse_ocr_lines(engine.ocr(enlarged, cls=False))

    def _fast_numeric_cell(self, cell: np.ndarray) -> tuple[str, float] | None:
        """排名/胜率格快通道：单行纯数字高置信即采信，否则回退原路径。"""
        lines = self._fast_path_cell(cell)
        if len(lines) != 1:
            return None
        text, confidence = lines[0]
        if confidence < FAST_PATH_MIN_CONFIDENCE or not FAST_PATH_NUMERIC_PATTERN.fullmatch(text):
            return None
        return text, confidence

    def _fast_name_cell(self, cell: np.ndarray) -> tuple[str, float] | None:
        """武将格快通道：单行词表精确命中且高置信即采信，与精确命中分支同构。"""
        lines = self._fast_path_cell(cell)
        if len(lines) != 1:
            return None
        text, confidence = lines[0]
        name = self._names.chinese_text(text)
        if confidence < FAST_PATH_MIN_CONFIDENCE or name not in self._names.hero_names:
            return None
        return name, confidence

    def recognize_cell(self, cell: np.ndarray, engine=None) -> tuple[str, float]:
        if engine is None:
            fast = self._fast_numeric_cell(cell)
            if fast is not None:
                return fast
        candidates = self.recognize_cell_candidates(cell, engine)
        return max(candidates, key=lambda item: item[1], default=("", 0.0))

    def _recognize_name_with_engine(
        self,
        cell: np.ndarray,
        engine,
        allowed_names: tuple[str, ...] | list[str],
    ) -> tuple[str, float]:
        """使用指定引擎识别名称，仅接受词表中可确认的完整结果。"""
        allowed_names = tuple(dict.fromkeys(allowed_names))
        allowed_set = set(allowed_names)
        candidates = self.recognize_cell_candidates(cell, engine)
        exact_matches = [
            match for match in self._names.exact_hero_matches(candidates)
            if match[0] in allowed_set
        ]
        exact_match, exact_conflicts = self._names.select_unique_name_match(exact_matches)
        if exact_match:
            return exact_match
        if exact_conflicts:
            logger.warning("官方榜单武将精确候选冲突: %s", exact_conflicts)
            return self._names.common_prefix(exact_conflicts), max(confidence for _text, confidence in candidates)
        corrected_matches = []
        for text, confidence in candidates:
            candidate_name = self._names.chinese_text(text)
            if len(candidate_name) < 2:
                continue
            nearby_names = [
                hero for hero in allowed_names
                if levenshtein_distance(candidate_name, hero)
                <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
            ]
            corrected = nearby_names[0] if len(nearby_names) == 1 else candidate_name
            if corrected in allowed_set:
                corrected_matches.append((corrected, confidence))
        corrected_match, corrected_conflicts = self._names.select_unique_name_match(corrected_matches)
        if corrected_match:
            return corrected_match
        if corrected_conflicts:
            logger.warning("官方榜单武将校正候选冲突: %s", corrected_conflicts)
            return self._names.common_prefix(corrected_conflicts), max(confidence for _text, confidence in candidates)
        glyph_name, glyph_confidence = self.recognize_name_glyphs(cell, engine)
        if glyph_name:
            nearby_names = [
                hero for hero in allowed_names
                if levenshtein_distance(glyph_name, hero)
                <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
            ]
            corrected = nearby_names[0] if len(nearby_names) == 1 else glyph_name
            if corrected in allowed_set:
                return corrected, glyph_confidence
        return "", 0.0

    def recognize_name_cell(
        self,
        cell: np.ndarray,
        status_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, float]:
        """优先选用词表中的完整 OCR 候选，单字结果再逐字补识别。"""
        fast = self._fast_name_cell(cell)
        if fast is not None:
            return fast
        candidates = self.recognize_cell_candidates(cell)
        if not candidates:
            return "", 0.0
        exact_match, exact_conflicts = self._names.select_unique_name_match(self._names.exact_hero_matches(candidates))
        if exact_match:
            return exact_match
        if exact_conflicts:
            logger.warning("官方榜单武将精确候选冲突: %s", exact_conflicts)
            text = self._names.common_prefix(exact_conflicts)
            confidence = max(confidence for _text, confidence in candidates)
        else:
            text, confidence = max(candidates, key=lambda item: item[1])
        name = self._names.chinese_text(text)
        if name in self._names.hero_names:
            return text, confidence
        ambiguous_candidates = self._names.ambiguous_name_candidates(name)

        glyph_name, glyph_confidence = self.recognize_name_glyphs(cell)
        if glyph_name:
            corrected = self._names.correct_official_name(glyph_name)
            if corrected in self._names.hero_names:
                return corrected, glyph_confidence
        prefix_matches = [hero for hero in self._names.hero_names if hero.startswith(name)]
        if len(prefix_matches) == 1:
            return prefix_matches[0], confidence
        allowed_names = tuple(ambiguous_candidates or prefix_matches)
        if not allowed_names:
            return text, confidence
        rare_char_engine = self._rare_char_engine
        if rare_char_engine is not None and status_callback:
            status_callback("正在执行罕见字兜底识别")
        if rare_char_engine is not None:
            try:
                rare_name, rare_confidence = self._recognize_name_with_engine(
                    cell, rare_char_engine, allowed_names,
                )
                self._engines.note_rare_char_success()
                if rare_name:
                    return rare_name, rare_confidence
            except Exception as exc:
                # 计数熔断：单次瞬时异常只损失本格兜底，不连坐整批的罕见字识别
                logger.warning("罕见字 OCR 识别失败，将保留原结果待复核: %s", exc)
                self._engines.note_rare_char_failure()
        elif self._engines.rare_char_failed and status_callback:
            status_callback("罕见字兜底已降级，名称保留原结果待复核")
        return text, confidence

    def recognize_name_glyphs(self, cell: np.ndarray, engine=None) -> tuple[str, float]:
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
                self.recognize_cell(glyph, engine)
                if engine is not None else self.recognize_cell(glyph)
            )
            character = self._names.chinese_text(text)
            if len(character) != 1:
                return "", 0.0
            characters.append(character)
            confidences.append(confidence)
        return "".join(characters), min(confidences)
