"""
武将名称识别模块

使用 PaddleOCR 对 8 个武将名称区域进行 OCR 识别。
识别策略：
  1. 全量字典（ch）PaddleOCR 识别
  2. 用 165 名武将名称库做编辑距离矫正，解决形近字误识别问题
     （不过滤置信度，始终执行矫正——OCR 有时高置信度也出错）

预处理操作在图像层面：放大、自适应对比度增强、锐化。
PaddleOCR 延迟加载，首次调用时初始化。
多维汉字相似度所使用的特征数据存储在 char_info_cache.json 中。
如遇缓存未收录的汉字，会在运行时通过原始库动态补齐。
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from src.ocr.character_similarity import CharacterSimilarityService
from src.ocr.image_preprocessor import ImagePreprocessor
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout, OcrRoiSlot

logger = logging.getLogger(__name__)

_HIGH_CONFIDENCE = 0.995       # 极高置信度且无纠错候选时，保护新武将
_BATCH_SLOT_GAP = 30
_BATCH_MIN_CONFIDENCE = 0.5


class GeneralRecognizer:
    """武将名称识别器，按页面类型使用独立的 ROI 布局。"""

    def __init__(self, rois: list[list[int]] | None = None,
                 hero_names: list[str] | None = None,
                 reference_size: tuple[int, int] | None = None,
                 page_type: str = "hero_selection",
                 preprocessor: ImagePreprocessor | None = None,
                 similarity_service: CharacterSimilarityService | None = None,
                 layout: OcrRoiLayout | None = None) -> None:
        base_layout = layout or OcrRoiConfig().layout_for(page_type)
        if rois is not None:
            base_layout = OcrRoiLayout(
                reference_size or base_layout.reference_size,
                tuple(OcrRoiSlot(name_roi=tuple(roi)) for roi in rois),
            )
        elif reference_size is not None and reference_size != base_layout.reference_size:
            base_layout = OcrRoiLayout(reference_size, base_layout.slots)
        self._layout = base_layout
        self._hero_names = hero_names or []
        self._page_type = page_type
        self._ocr = None  # PaddleOCR 引擎（延迟加载）
        self._preprocessor = preprocessor or ImagePreprocessor()
        self._similarity_service = similarity_service or CharacterSimilarityService()
        self._timing_ms: dict[str, float] = {}

    # ── OCR 引擎 ──────────────────────────────────────────────────────

    @property
    def _engine(self):
        """PaddleOCR（ch），延迟加载。"""
        if self._ocr is None:
            logger.info("首次调用，正在加载 PaddleOCR 模型...")
            try:
                started = time.perf_counter()
                from paddleocr import PaddleOCR
                self._ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
                elapsed_ms = (time.perf_counter() - started) * 1000
                self._timing_ms["model_load"] = self._timing_ms.get("model_load", 0.0) + elapsed_ms
                logger.info("PaddleOCR 模型加载完成，耗时 %.1fms", elapsed_ms)
            except Exception as e:
                logger.error("PaddleOCR 模型加载失败: %s", e)
                logger.debug(traceback.format_exc())
                raise
        return self._ocr

    # ── 提前初始化 ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """提前加载 OCR 模型及汉字特征缓存，避免首次识别时的延迟。"""
        _ = self._engine
        self._similarity_service.warmup()
        self._similarity_service.warmup_hero_names(self._hero_names)

    def warmup_inference(self) -> None:
        """执行一次与名称拼图一致的检测和识别，完成运行时算子初始化。"""
        roi = np.zeros((145, 50, 3), dtype=np.uint8)
        prepared = self._preprocessor.preprocess_roi(roi)
        canvas, _ = self._build_batch_canvas({slot: prepared for slot in range(1, 9)})
        self._engine.ocr(canvas, cls=False)
        horizontal = cv2.cvtColor(
            cv2.rotate(prepared, cv2.ROTATE_90_COUNTERCLOCKWISE),
            cv2.COLOR_GRAY2BGR,
        )
        self._engine.ocr([horizontal], det=False, rec=True, cls=False)

    @property
    def timing_ms(self) -> dict[str, float]:
        """返回最近一次识别各阶段的累计耗时（毫秒）。"""
        return dict(self._timing_ms)

    # ── 识别 ──────────────────────────────────────────────────────────

    def recognize(self, image: np.ndarray | Image.Image) -> list[dict]:
        """识别当前页面的武将名称，返回含置信度和阵营标签的结果。

        Args:
            image: 截图图像。

        Returns:
            [{index: int, name: str, confidence: float}, ...]
        """
        self._timing_ms = {}
        if isinstance(image, Image.Image):
            image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

        if self._page_type == "match_guide":
            return self._recognize_match_guide(image)

        image_height, image_width = image.shape[:2]
        reference_width, reference_height = self._layout.reference_size
        scale_x = image_width / reference_width
        scale_y = image_height / reference_height
        logger.debug("武将 ROI 缩放: %.4f×%.4f，当前截图=%sx%s，参考=%sx%s",
                     scale_x, scale_y, image_width, image_height,
                     reference_width, reference_height)

        prepared_slots: dict[int, np.ndarray] = {}
        for i, slot in enumerate(self._layout.slots):
            x, y, w, h = slot.name_roi
            roi_x = round(x * scale_x)
            roi_y = round(y * scale_y)
            roi_w = max(1, round(w * scale_x))
            roi_h = max(1, round(h * scale_y))
            logger.info(
                "武将 %d OCR ROI: x=%d, y=%d, w=%d, h=%d (参考 ROI=%s)",
                i + 1, roi_x, roi_y, roi_w, roi_h, [x, y, w, h],
            )
            roi_img = image[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
            if roi_img.size == 0:
                logger.warning(
                    "武将 %d OCR ROI 超出截图边界，跳过识别: x=%d, y=%d, w=%d, h=%d, 截图=%dx%d",
                    i + 1, roi_x, roi_y, roi_w, roi_h, image_width, image_height,
                )
                continue
            preprocess_started = time.perf_counter()
            prepared_slots[i + 1] = self._preprocessor.preprocess_roi(roi_img)
            self._add_timing("name_preprocess", preprocess_started)

        recognized = self._recognize_prepared_batch(prepared_slots, "name")
        results: list[dict] = []
        for i, _slot in enumerate(self._layout.slots, 1):
            prepared = prepared_slots.get(i)
            if prepared is None:
                results.append({"index": i, "name": "", "confidence": 0.0})
                continue
            text, confidence = recognized.get(i, ("", 0.0))
            if not text:
                text, confidence = self._recognize_prepared_single(prepared, i, "name")
            name, confidence = self._correct_name(text, confidence, i)
            results.append({"index": i, "name": name, "confidence": round(confidence, 4)})
            logger.debug("武将 %d 识别: %s (置信度=%.4f)", i, name or "(空)", confidence)

        return results

    def _recognize_match_guide(self, image: np.ndarray) -> list[dict]:
        """识别 2v2 对局中的角色名与楚/汉军标签。"""
        image_height, image_width = image.shape[:2]
        reference_width, reference_height = self._layout.reference_size
        scale_x = image_width / reference_width
        scale_y = image_height / reference_height
        name_slots: dict[int, np.ndarray] = {}
        team_slots: dict[int, np.ndarray] = {}
        for seat_index, slot in enumerate(self._layout.slots, 1):
            name_img = self._crop_roi(image, list(slot.name_roi), scale_x, scale_y)
            if name_img is None:
                continue
            preprocess_started = time.perf_counter()
            name_slots[seat_index] = self._preprocessor.preprocess_roi(name_img)
            self._add_timing("name_preprocess", preprocess_started)
            if slot.team_roi is not None:
                team_img = self._crop_roi(image, list(slot.team_roi), scale_x, scale_y)
                if team_img is not None:
                    preprocess_started = time.perf_counter()
                    team_slots[seat_index] = self._preprocessor.preprocess_roi(team_img)
                    self._add_timing("team_preprocess", preprocess_started)

        recognized_names = self._recognize_prepared_batch(name_slots, "name")
        recognized_teams = self._recognize_prepared_batch(team_slots, "team")
        results: list[dict] = []
        for seat_index, _slot in enumerate(self._layout.slots, 1):
            prepared_name = name_slots.get(seat_index)
            if prepared_name is None:
                continue
            text, confidence = recognized_names.get(seat_index, ("", 0.0))
            if not text:
                text, confidence = self._recognize_prepared_single(prepared_name, seat_index, "name")
            name, confidence = self._correct_name(text, confidence, seat_index)
            if not name:
                continue
            team_text, team_confidence = recognized_teams.get(seat_index, ("", 0.0))
            prepared_team = team_slots.get(seat_index)
            if not team_text and prepared_team is not None:
                team_text, team_confidence = self._recognize_prepared_single(
                    prepared_team, seat_index, "team",
                )
            team = self._normalize_team(team_text, seat_index)
            results.append({
                "index": seat_index,
                "name": name,
                "confidence": round(confidence, 4),
                "team": team,
            })
        return results

    @staticmethod
    def _crop_roi(
        image: np.ndarray, roi: list[int], scale_x: float, scale_y: float,
    ) -> np.ndarray | None:
        """裁剪并校验按参考尺寸缩放后的 ROI。"""
        x, y, width, height = roi
        roi_x = round(x * scale_x)
        roi_y = round(y * scale_y)
        roi_w = max(1, round(width * scale_x))
        roi_h = max(1, round(height * scale_y))
        cropped = image[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
        return cropped if cropped.size else None

    def _recognize_single(self, roi: np.ndarray, slot: int) -> tuple[str, float]:
        """识别单个武将名称区域。"""
        try:
            preprocess_started = time.perf_counter()
            prepared = self._preprocessor.preprocess_roi(roi)
            self._add_timing("name_preprocess", preprocess_started)
            text, conf = self._recognize_prepared_single(prepared, slot, "name")
            return self._correct_name(text, conf, slot)
        except Exception as e:
            logger.warning("武将 %d 识别异常: %s", slot, e)
            logger.debug(traceback.format_exc())

        return "", 0.0

    def _recognize_team(self, roi: np.ndarray, slot: int) -> str:
        """识别角色右上角的【楚军】或【汉军】标记。"""
        try:
            preprocess_started = time.perf_counter()
            prepared = self._preprocessor.preprocess_roi(roi)
            self._add_timing("team_preprocess", preprocess_started)
            text, _ = self._recognize_prepared_single(prepared, slot, "team")
        except Exception as exc:
            logger.warning("武将 %d 阵营标签识别异常: %s", slot, exc)
            return ""
        return self._normalize_team(text, slot)

    def _correct_name(self, text: str, confidence: float, slot: int) -> tuple[str, float]:
        if not text:
            return "", 0.0
        if self._hero_names:
            correction_started = time.perf_counter()
            corrected = self._similarity_service.correct_hero_name(text, self._hero_names)
            self._add_timing("name_correction", correction_started)
            if corrected != text:
                logger.debug("武将 %d: 矫正 %s → %s", slot, text, corrected)
                return corrected, confidence
            if confidence >= _HIGH_CONFIDENCE and text not in self._hero_names:
                logger.debug("武将 %d: 高置信度未知新名 '%s'，无纠错候选", slot, text)
        return text, confidence

    @staticmethod
    def _normalize_team(text: str, slot: int) -> str:
        normalized = text.replace(" ", "").replace("【", "").replace("】", "")
        if "楚" in normalized:
            return "楚军"
        if "汉" in normalized:
            return "汉军"
        logger.info("武将 %d 阵营标签未识别: %r", slot, text)
        return ""

    def _recognize_prepared_single(
        self, prepared: np.ndarray, slot: int, kind: str,
    ) -> tuple[str, float]:
        """识别已预处理的单个 ROI，供批处理异常槽位回退。"""
        ocr_started = time.perf_counter()
        text, confidence = self._extract_text(self._engine.ocr(prepared, cls=False))
        self._add_timing(f"{kind}_ocr", ocr_started)
        logger.info("武将 %d %s OCR 原始结果: text=%r, confidence=%.4f", slot, kind, text, confidence)
        return text, confidence

    def _recognize_prepared_batch(
        self, prepared_slots: dict[int, np.ndarray], kind: str,
    ) -> dict[int, tuple[str, float]]:
        """将同类 ROI 拼图为一次检测；异常槽位由调用方逐槽回退。"""
        if not prepared_slots:
            return {}
        canvas, ranges = self._build_batch_canvas(prepared_slots)
        try:
            ocr_started = time.perf_counter()
            result = self._engine.ocr(canvas, cls=False)
            self._add_timing(f"{kind}_ocr", ocr_started)
        except Exception as exc:
            logger.warning("%s ROI 拼图 OCR 失败，将逐槽回退: %s", kind, exc)
            return {}

        mapped: dict[int, list[tuple[str, float]]] = {slot: [] for slot in prepared_slots}
        for line in (result[0] if result and result[0] else []):
            try:
                box, (text, confidence) = line
                center_x = sum(point[0] for point in box) / len(box)
                slot = next(
                    (index for index, (left, right) in ranges.items() if left <= center_x < right),
                    None,
                )
                text = text.strip()
                if slot is not None and text:
                    mapped[slot].append((text, float(confidence)))
            except (IndexError, TypeError, ValueError):
                logger.warning("%s ROI 拼图返回了无法映射的检测框", kind)

        recognized: dict[int, tuple[str, float]] = {}
        for slot, candidates in mapped.items():
            if (
                len(candidates) == 1
                and candidates[0][1] >= _BATCH_MIN_CONFIDENCE
                and not (kind == "name" and self._requires_name_batch_fallback(candidates[0][0]))
            ):
                recognized[slot] = candidates[0]
            elif candidates:
                logger.info("武将 %d %s 拼图结果不唯一或置信度过低，逐槽回退", slot, kind)
        return recognized

    def _requires_name_batch_fallback(self, text: str) -> bool:
        """避免截断文本被多候选纠错静默绑定到错误武将。"""
        if not self._hero_names or text in self._hero_names:
            return False
        candidates = [
            hero for hero in self._hero_names
            if self._similarity_service._levenshtein_distance(text, hero)
            <= self._similarity_service.EDIT_DISTANCE_THRESHOLD
        ]
        return len(candidates) != 1

    @staticmethod
    def _build_batch_canvas(
        prepared_slots: dict[int, np.ndarray],
    ) -> tuple[np.ndarray, dict[int, tuple[int, int]]]:
        height = max(image.shape[0] for image in prepared_slots.values())
        width = sum(image.shape[1] for image in prepared_slots.values())
        width += _BATCH_SLOT_GAP * (len(prepared_slots) - 1)
        canvas = np.zeros((height, width), dtype=np.uint8)
        ranges: dict[int, tuple[int, int]] = {}
        left = 0
        for slot, image in prepared_slots.items():
            image_height, image_width = image.shape[:2]
            canvas[:image_height, left:left + image_width] = image
            ranges[slot] = (left, left + image_width)
            left += image_width + _BATCH_SLOT_GAP
        return canvas, ranges

    def _add_timing(self, key: str, started: float) -> None:
        self._timing_ms[key] = self._timing_ms.get(key, 0.0) + (time.perf_counter() - started) * 1000

    # ── 图像预处理 ────────────────────────────────────────────────────

    @staticmethod
    def _preprocess_roi(roi: np.ndarray) -> np.ndarray:
        """兼容旧调用：委托独立的图像预处理组件。"""
        return ImagePreprocessor.preprocess_roi(roi)

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
            with open(json_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            logger.info("识别结果已保存: %s", json_path)
        except Exception as e:
            logger.error("识别结果保存失败 %s: %s", json_path, e)
