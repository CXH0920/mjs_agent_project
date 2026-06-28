"""
武将名称识别模块

使用 PaddleOCR 对 8 个武将名称区域进行 OCR 识别。
识别策略：
  1. 全量字典（ch）PaddleOCR 识别
  2. 若置信度低于阈值，用 155 名武将名称库做编辑距离矫正
     解决形近字误识别问题。

所有预处理操作都在图像层面：放大、自适应对比度增强、锐化。
PaddleOCR 延迟加载，首次调用时初始化。
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 8 个武将名称的默认 ROI 坐标（基于 2560×1440 分辨率）
_DEFAULT_GENERALS_ROI = [
    [160, 370, 40, 140],
    [445, 370, 40, 140],
    [725, 370, 40, 140],
    [1010, 370, 40, 140],
    [1335, 370, 40, 140],
    [1620, 370, 40, 140],
    [1900, 370, 40, 140],
    [2180, 370, 40, 140],
]

# 两段式识别阈值
_CONFIDENCE_THRESHOLD = 0.985
_EDIT_DISTANCE_THRESHOLD = 1
_CJK_START = 0x4E00
_CJK_END = 0x9FFF
_CJK_VISUAL_MAX_DIST = 500


def _character_visual_similarity(c1: str, c2: str) -> float:
    """基于 Unicode 码位差的视觉相似度（0~1）。"""
    if c1 == c2:
        return 1.0
    cp1, cp2 = ord(c1), ord(c2)
    if _CJK_START <= cp1 <= _CJK_END and _CJK_START <= cp2 <= _CJK_END:
        dist = abs(cp1 - cp2)
        return max(0.0, 1.0 - dist / _CJK_VISUAL_MAX_DIST)
    return 0.0


def _pick_visually_similar(text: str, candidates: list[str]) -> str:
    """从编辑距离相同的候选中选出视觉最相似的一个。"""
    best_score = -1.0
    best_candidate = candidates[0]
    for candidate in candidates:
        score = 0.0
        for tc, cc in zip(text, candidate):
            if tc != cc:
                score += _character_visual_similarity(tc, cc)
        length_penalty = -0.3 * abs(len(text) - len(candidate))
        score += length_penalty
        if score > best_score:
            best_score = score
            best_candidate = candidate
    return best_candidate


def _levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离。"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(
                curr_row[j] + 1,
                prev_row[j + 1] + 1,
                prev_row[j] + cost,
            ))
        prev_row = curr_row
    return prev_row[-1]


def _correct_with_hero_list(text: str, hero_names: list[str]) -> tuple[str, float]:
    """用武将名称库矫正识别结果。

    Args:
        text: OCR 识别出的文本。
        hero_names: 155 名武将名称列表。

    Returns:
        (纠正后的名称, 纠正置信度)
    """
    if not text:
        return text, 0.0

    text = text.strip()
    best_dist = len(text)
    candidates: list[str] = []

    for hero in hero_names:
        dist = _levenshtein_distance(text, hero)
        if dist < best_dist:
            best_dist = dist
            candidates = [hero]
        elif dist == best_dist:
            candidates.append(hero)
        if dist == 0:
            return hero, 1.0

    if best_dist <= _EDIT_DISTANCE_THRESHOLD:
        if len(candidates) > 1:
            best_match = _pick_visually_similar(text, candidates)
        else:
            best_match = candidates[0]
        confidence = max(0.5, 1.0 - best_dist / max(len(text), len(best_match), 1))
        if best_match != text:
            logger.debug("矫正: %s → %s (距离=%d, 候选=%s)", text, best_match, best_dist, candidates)
        return best_match, round(confidence, 4)

    return text, 0.0


class GeneralRecognizer:
    """武将名称识别器，支持全量字典 + 武将名库矫正。"""

    def __init__(self, rois: list[list[int]] | None = None, hero_names: list[str] | None = None) -> None:
        self._rois = rois or _DEFAULT_GENERALS_ROI
        self._hero_names = hero_names or []
        self._ocr = None  # PaddleOCR 引擎（延迟加载）

    # ── OCR 引擎 ──────────────────────────────────────────────────────

    @property
    def _engine(self):
        """PaddleOCR（ch），延迟加载。"""
        if self._ocr is None:
            logger.info("首次调用，正在加载 PaddleOCR 模型...")
            try:
                from paddleocr import PaddleOCR
                self._ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
                logger.info("PaddleOCR 模型加载完成")
            except Exception as e:
                logger.error("PaddleOCR 模型加载失败: %s", e)
                logger.debug(traceback.format_exc())
                raise
        return self._ocr

    # ── 提前初始化 ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """提前加载 PaddleOCR 模型，避免首次识别时的延迟。"""
        _ = self._engine

    # ── 识别 ──────────────────────────────────────────────────────────

    def recognize(self, image: np.ndarray | Image.Image) -> list[dict]:
        """对 8 个武将区域逐一识别，返回含置信度的结果。

        Args:
            image: 截图图像。

        Returns:
            [{index: int, name: str, confidence: float}, ...]
        """
        if isinstance(image, Image.Image):
            image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

        results: list[dict] = []
        for i, (x, y, w, h) in enumerate(self._rois):
            roi_img = image[y:y + h, x:x + w]
            name, confidence = self._recognize_single(roi_img, i + 1)
            results.append({"index": i + 1, "name": name, "confidence": round(confidence, 4)})
            logger.debug("武将 %d 识别: %s (置信度=%.4f)", i + 1, name or "(空)", confidence)

        return results

    def _recognize_single(self, roi: np.ndarray, slot: int) -> tuple[str, float]:
        """识别单个武将名称区域。"""
        try:
            prepared = self._preprocess_roi(roi)
            result = self._engine.ocr(prepared, cls=False)
            text, conf = self._extract_text(result)

            if text and conf < _CONFIDENCE_THRESHOLD and self._hero_names:
                corrected, _ = _correct_with_hero_list(text, self._hero_names)
                if corrected != text:
                    logger.debug("武将 %d: 矫正 %s → %s", slot, text, corrected)
                return corrected, conf

            if text:
                return text, conf

        except Exception as e:
            logger.warning("武将 %d 识别异常: %s", slot, e)
            logger.debug(traceback.format_exc())

        return "", 0.0

    # ── 图像预处理 ────────────────────────────────────────────────────

    @staticmethod
    def _preprocess_roi(roi: np.ndarray) -> np.ndarray:
        """预处理 ROI 区域：放大 3× → CLAHE → 锐化 → 灰度。"""
        enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)

        lab = cv2.cvtColor(enlarged, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

        kernel = np.array([[-1, -1, -1],
                           [-1,  9, -1],
                           [-1, -1, -1]], dtype=np.float32)
        sharpened = cv2.filter2D(enhanced, -1, kernel)

        return cv2.cvtColor(sharpened, cv2.COLOR_BGR2GRAY)

    # ── 辅助 ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(ocr_result: list | None) -> tuple[str, float]:
        """从 PaddleOCR 返回结果中提取文字和置信度。"""
        if not ocr_result or not ocr_result[0]:
            return "", 0.0
        for line in ocr_result[0]:
            text = line[1][0].strip()
            confidence = line[1][1]
            if text:
                return text, confidence
        return "", 0.0

    # ── 保存结果 ──────────────────────────────────────────────────────

    @staticmethod
    def save_results(results: list[dict], json_path: str | Path, image_path: str | Path | None = None) -> None:
        """将识别结果保存为 JSON 文件。"""
        data = {
            "image": str(image_path) if image_path else "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page_type": "wujiang_select",
            "generals": results,
        }
        json_path = Path(json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("识别结果已保存: %s", json_path)
        except Exception as e:
            logger.error("识别结果保存失败 %s: %s", json_path, e)
