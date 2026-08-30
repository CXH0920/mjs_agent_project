"""
模板管理模块

负责武将选择页面的模板截图的保存、加载、OpenCV 模板匹配。
模板是用户框选的一个小区域图片（如页面标题或按钮），
用于检测当前画面是否为武将选择页面。
"""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from src.config.env import BUNDLE_ROOT, PROJECT_ROOT

logger = logging.getLogger(__name__)

# 随包默认模板在打包资源根（frozen 下只读 _internal/templates，见 mjs_agent.spec）；
# 用户框选/重制模板写入可写运行时根。开发态两者同为项目根，行为不变。
BUNDLED_TEMPLATE_DIR = BUNDLE_ROOT / "templates"
DEFAULT_TEMPLATE_DIR = PROJECT_ROOT / "templates"
DEFAULT_TEMPLATE_FILE = DEFAULT_TEMPLATE_DIR / "wujiang_select.png"
MATCH_GUIDE_TEMPLATE_FILE = DEFAULT_TEMPLATE_DIR / "match_guide" / "template.png"
DEFAULT_REFERENCE_SIZE = (2560, 1440)
_LOCAL_SEARCH_PADDING_RATIO = 0.2


class TemplateManager:
    """模板管理器 — 保存/加载/匹配武将选择页面模板"""

    def __init__(
        self,
        template_path: str | Path | None = None,
        *,
        template_name: str = "hero_selection",
    ) -> None:
        if template_path is not None:
            self._template_path = Path(template_path)
        elif template_name == "match_guide":
            self._template_path = MATCH_GUIDE_TEMPLATE_FILE
        else:
            self._template_path = DEFAULT_TEMPLATE_FILE
        self.template_name = template_name
        self._template: np.ndarray | None = None  # 灰度模板图像
        self._reference_size = DEFAULT_REFERENCE_SIZE
        self._template_roi: tuple[int, int, int, int] | None = None
        self._last_match_scale = 1.0
        self._last_match_confidence = 0.0
        self._last_match_strategy = "unmatched"
        logger.debug("TemplateManager 初始化, 模板路径: %s", self._template_path)
        self._load()

    # ── 属性 ──────────────────────────────────────────────────────────

    @property
    def template_path(self) -> Path:
        return self._template_path

    @property
    def is_loaded(self) -> bool:
        return self._template is not None

    @property
    def reference_size(self) -> tuple[int, int]:
        """返回制作模板时的截图尺寸。旧模板使用默认参考尺寸。"""
        return self._reference_size

    @property
    def last_match_scale(self) -> float:
        return self._last_match_scale

    @property
    def last_match_confidence(self) -> float:
        return self._last_match_confidence

    @property
    def last_match_strategy(self) -> str:
        return self._last_match_strategy

    # ── 加载 ──────────────────────────────────────────────────────────

    def _bundled_template_path(self) -> Path:
        """返回随包默认模板路径。"""
        if self.template_name == "match_guide":
            return BUNDLED_TEMPLATE_DIR / "match_guide" / "template.png"
        return BUNDLED_TEMPLATE_DIR / "wujiang_select.png"

    def _load(self) -> None:
        """内部加载逻辑：用户模板缺失时回退随包默认模板（打包态只读资源）。"""
        if self._template_path.exists():
            self._load_internal(self._template_path)
            return
        bundled = self._bundled_template_path()
        if bundled.exists():
            logger.info("用户模板不存在，使用随包默认模板: %s", bundled)
            self._load_internal(bundled)
        else:
            logger.info("模板文件不存在: %s", self._template_path)
            self._template = None

    def _load_internal(self, path: Path) -> None:
        """从文件加载模板到内存。"""
        try:
            with path.open("rb") as _f:
                img = cv2.imdecode(np.frombuffer(_f.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
            if img is not None and img.size > 0:
                self._template = img
                self._load_metadata(path.with_suffix(".json"))
                logger.debug("模板已加载: %s (%sx%s)", path.name, img.shape[1], img.shape[0])
            else:
                logger.warning("模板文件读取失败: %s", self._template_path)
                self._template = None
        except Exception as e:
            logger.error("模板加载异常: %s", e)
            logger.debug(traceback.format_exc())
            self._template = None

    @property
    def _metadata_path(self) -> Path:
        return self._template_path.with_suffix(".json")

    def _load_metadata(self, metadata_path: Path) -> None:
        """加载模板参考尺寸；缺少元数据时兼容旧模板。"""
        self._reference_size = DEFAULT_REFERENCE_SIZE
        self._template_roi = None
        if not metadata_path.exists():
            return
        try:
            with metadata_path.open("r", encoding="utf-8") as file:
                metadata = json.load(file)
            width = int(metadata["reference_width"])
            height = int(metadata["reference_height"])
            if width > 0 and height > 0:
                self._reference_size = (width, height)
            if all(key in metadata for key in ("x", "y", "w", "h")):
                roi_values = tuple(int(metadata[key]) for key in ("x", "y", "w", "h"))
                x, y, roi_width, roi_height = roi_values
                if x >= 0 and y >= 0 and roi_width > 0 and roi_height > 0 and x + roi_width <= width and y + roi_height <= height:
                    self._template_roi = roi_values
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning("模板元数据读取失败，使用默认参考尺寸: %s", exc)

    def reload(self) -> None:
        """从磁盘重新加载模板。"""
        self._load()

    # ── 设置模板 ──────────────────────────────────────────────────────

    def set_template(self, image: np.ndarray | Image.Image, roi: tuple[int, int, int, int]) -> None:
        """从全图中截取 ROI 区域设为模板并保存到文件。

        Args:
            image: 全屏截图（BGR 或 RGB）。
            roi: (x, y, w, h) 框选区域。

        Raises:
            ValueError: ROI 尺寸过小或超出边界。
        """
        x, y, w, h = roi

        if w < 10 or h < 10:
            raise ValueError(f"ROI 尺寸过小 ({w}x{h})，请框选更大的区域")

        if isinstance(image, Image.Image):
            image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

        img_h, img_w = image.shape[:2]
        if x + w > img_w or y + h > img_h:
            raise ValueError(f"ROI 超出画面边界 ({img_w}x{img_h})")

        # 裁剪 ROI
        roi_crop = image[y:y + h, x:x + w]
        # 转灰度
        gray = cv2.cvtColor(roi_crop, cv2.COLOR_BGR2GRAY)

        # 保存到文件
        self._template_path.parent.mkdir(parents=True, exist_ok=True)
        # cv2.imwrite 用 ANSI fopen 不支持中文路径，改 imencode + open(wb) 规避
        _ext = self._template_path.suffix or ".png"
        _ok, buf = cv2.imencode(_ext, gray)
        if not _ok:
            raise IOError(f"模板保存失败: {self._template_path}")
        with self._template_path.open("wb") as _f:
            _f.write(buf.tobytes())

        # 加载到内存
        self._template = gray
        self._reference_size = (img_w, img_h)
        self._template_roi = (x, y, w, h)
        try:
            with self._metadata_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump({
                    "reference_width": img_w,
                    "reference_height": img_h,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                }, file, ensure_ascii=False, indent=2)
                file.write("\n")
        except OSError as exc:
            logger.warning("模板元数据保存失败: %s", exc)
        logger.info("模板已保存: %s (%sx%s)", self._template_path.name, w, h)

    # ── 匹配 ──────────────────────────────────────────────────────────

    def match(self, image: np.ndarray | Image.Image, threshold: float = 0.8) -> tuple[bool, float]:
        """检测当前画面是否为武将选择页面。

        Args:
            image: 截图图像。
            threshold: 匹配阈值（0~1），默认 0.8。

        Returns:
            (是否匹配, 置信度)
        """
        if self._template is None:
            logger.debug("模板未加载，跳过匹配")
            return False, 0.0

        if isinstance(image, Image.Image):
            image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except Exception as e:
            logger.error("图像转灰度失败: %s", e)
            return False, 0.0

        try:
            self._last_match_scale = 1.0
            self._last_match_confidence = 0.0
            self._last_match_strategy = "unmatched"
            reference_width, reference_height = self._reference_size
            base_scale = min(
                gray.shape[1] / reference_width,
                gray.shape[0] / reference_height,
            )
            scales = self._candidate_scales(base_scale)
            local_region = self._local_search_region(gray, base_scale)
            base_value = self._match_at_scale(gray, base_scale, local_region)
            base_strategy = "base_local" if local_region is not None else "base_full"
            if base_value is not None and base_value >= threshold:
                self._set_match_details(base_value, base_scale, base_strategy)
                logger.debug(
                    "模板匹配: 置信度=%.4f, 缩放=%.4f, 阈值=%.2f, 策略=%s, 匹配",
                    base_value, base_scale, threshold, base_strategy,
                )
                return True, float(base_value)

            best_value = base_value if base_value is not None else -1.0
            best_scale = base_scale
            fallback_scales = scales if local_region is not None else [
                scale for scale in scales if scale != round(base_scale, 4)
            ]
            for scale in fallback_scales:
                value = self._match_at_scale(gray, scale)
                if value is not None and value > best_value:
                    best_value = value
                    best_scale = scale
            if best_value < 0:
                logger.debug("所有模板缩放比例均大于当前截图，跳过匹配")
                return False, 0.0
            fallback_strategy = "fallback_full_multiscale" if local_region is not None else "fallback_multiscale"
            self._set_match_details(best_value, best_scale, fallback_strategy)
            matched = bool(best_value >= threshold)
            logger.debug(
                "模板匹配: 置信度=%.4f, 缩放=%.4f, 阈值=%.2f, 策略=%s, %s",
                best_value, best_scale, threshold, fallback_strategy, "匹配" if matched else "不匹配",
            )
            return matched, float(best_value)
        except Exception as e:
            logger.error("模板匹配异常: %s", e)
            logger.debug(traceback.format_exc())
            return False, 0.0

    @staticmethod
    def _candidate_scales(base_scale: float) -> list[float]:
        values = [base_scale * factor for factor in (0.85, 0.925, 1.0, 1.075, 1.15)]
        values.append(1.0)
        return sorted({round(value, 4) for value in values if value > 0})

    def _local_search_region(self, gray: np.ndarray, base_scale: float) -> np.ndarray | None:
        if self._template_roi is None:
            return None
        x, y, width, height = self._template_roi
        expected_x = round(x * base_scale)
        expected_y = round(y * base_scale)
        expected_width = max(1, round(width * base_scale))
        expected_height = max(1, round(height * base_scale))
        padding_x = max(1, round(expected_width * _LOCAL_SEARCH_PADDING_RATIO))
        padding_y = max(1, round(expected_height * _LOCAL_SEARCH_PADDING_RATIO))
        left = max(0, expected_x - padding_x)
        top = max(0, expected_y - padding_y)
        right = min(gray.shape[1], expected_x + expected_width + padding_x)
        bottom = min(gray.shape[0], expected_y + expected_height + padding_y)
        region = gray[top:bottom, left:right]
        return region if region.size else None

    def _match_at_scale(
        self,
        gray: np.ndarray,
        scale: float,
        region: np.ndarray | None = None,
    ) -> float | None:
        width = max(1, round(self._template.shape[1] * scale))
        height = max(1, round(self._template.shape[0] * scale))
        search_image = region if region is not None else gray
        if width > search_image.shape[1] or height > search_image.shape[0]:
            return None
        template = cv2.resize(self._template, (width, height), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(search_image, template, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, _ = cv2.minMaxLoc(result)
        return float(max_value)

    def _set_match_details(self, confidence: float, scale: float, strategy: str) -> None:
        self._last_match_confidence = confidence
        self._last_match_scale = scale
        self._last_match_strategy = strategy

    # ── 删除 ──────────────────────────────────────────────────────────

    def delete_template(self) -> None:
        """删除模板文件。"""
        self._template = None
        self._reference_size = DEFAULT_REFERENCE_SIZE
        self._template_roi = None
        self._last_match_scale = 1.0
        self._last_match_confidence = 0.0
        self._last_match_strategy = "unmatched"
        if self._template_path.exists():
            try:
                self._template_path.unlink()
                logger.info("模板已删除: %s", self._template_path)
            except OSError as e:
                logger.error("模板删除失败: %s", e)
        if self._metadata_path.exists():
            try:
                self._metadata_path.unlink()
            except OSError as e:
                logger.error("模板元数据删除失败: %s", e)
