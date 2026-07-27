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
        """提前加载 OCR 模型及汉字特征缓存，避免首次识别时的延迟。"""
        _ = self._engine
        self._similarity_service.warmup()

    # ── 识别 ──────────────────────────────────────────────────────────

    def recognize(self, image: np.ndarray | Image.Image) -> list[dict]:
        """识别当前页面的武将名称，返回含置信度和阵营标签的结果。

        Args:
            image: 截图图像。

        Returns:
            [{index: int, name: str, confidence: float}, ...]
        """
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

        results: list[dict] = []
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
                results.append({"index": i + 1, "name": "", "confidence": 0.0})
                continue
            name, confidence = self._recognize_single(roi_img, i + 1)
            results.append({"index": i + 1, "name": name, "confidence": round(confidence, 4)})
            logger.debug("武将 %d 识别: %s (置信度=%.4f)", i + 1, name or "(空)", confidence)

        return results

    def _recognize_match_guide(self, image: np.ndarray) -> list[dict]:
        """识别 2v2 对局中的角色名与楚/汉军标签。"""
        image_height, image_width = image.shape[:2]
        reference_width, reference_height = self._layout.reference_size
        scale_x = image_width / reference_width
        scale_y = image_height / reference_height
        results: list[dict] = []
        for seat_index, slot in enumerate(self._layout.slots, 1):
            name_img = self._crop_roi(image, list(slot.name_roi), scale_x, scale_y)
            if name_img is None:
                continue
            name, confidence = self._recognize_single(name_img, seat_index)
            if not name:
                continue
            team_img = (
                self._crop_roi(image, list(slot.team_roi), scale_x, scale_y)
                if slot.team_roi is not None else None
            )
            team = self._recognize_team(team_img, seat_index) if team_img is not None else ""
            results.append({
                "index": len(results) + 1,
                "name": name,
                "confidence": round(confidence, 4),
                "team": team,
            })
            if len(results) == 4:
                break
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
            prepared = self._preprocessor.preprocess_roi(roi)
            result = self._engine.ocr(prepared, cls=False)
            text, conf = self._extract_text(result)
            logger.info(
                "武将 %d OCR 原始结果: text=%r, confidence=%.4f",
                slot, text, conf,
            )

            if not text:
                return "", 0.0

            # 第二段矫正：存在词表候选时优先采用，避免高置信度形近字漏纠正。
            if self._hero_names:
                corrected = self._similarity_service.correct_hero_name(text, self._hero_names)
                if corrected != text:
                    logger.debug("武将 %d: 矫正 %s → %s", slot, text, corrected)
                    return corrected, conf
                if conf >= _HIGH_CONFIDENCE and text not in self._hero_names:
                    logger.debug("武将 %d: 高置信度未知新名 '%s'，无纠错候选", slot, text)

            return text, conf

        except Exception as e:
            logger.warning("武将 %d 识别异常: %s", slot, e)
            logger.debug(traceback.format_exc())

        return "", 0.0

    def _recognize_team(self, roi: np.ndarray, slot: int) -> str:
        """识别角色右上角的【楚军】或【汉军】标记。"""
        try:
            text, _ = self._extract_text(self._engine.ocr(self._preprocessor.preprocess_roi(roi), cls=False))
        except Exception as exc:
            logger.warning("武将 %d 阵营标签识别异常: %s", slot, exc)
            return ""
        normalized = text.replace(" ", "").replace("【", "").replace("】", "")
        if "楚" in normalized:
            return "楚军"
        if "汉" in normalized:
            return "汉军"
        logger.info("武将 %d 阵营标签未识别: %r", slot, text)
        return ""

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
