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

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TEMPLATE_DIR = PROJECT_ROOT / "templates"
DEFAULT_TEMPLATE_FILE = DEFAULT_TEMPLATE_DIR / "wujiang_select.png"
MATCH_GUIDE_TEMPLATE_FILE = DEFAULT_TEMPLATE_DIR / "match_guide" / "template.png"
DEFAULT_REFERENCE_SIZE = (2560, 1440)


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
        self._last_match_scale = 1.0
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

    # ── 加载 ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """内部加载逻辑。"""
        if self._template_path.exists():
            self._load_internal()
        else:
            logger.info("模板文件不存在: %s", self._template_path)
            self._template = None

    def _load_internal(self) -> None:
        """从文件加载模板到内存。"""
        try:
            img = cv2.imread(str(self._template_path), cv2.IMREAD_GRAYSCALE)
            if img is not None and img.size > 0:
                self._template = img
                self._load_metadata()
                logger.debug("模板已加载: %s (%sx%s)", self._template_path.name, img.shape[1], img.shape[0])
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

    def _load_metadata(self) -> None:
        """加载模板参考尺寸；缺少元数据时兼容旧模板。"""
        self._reference_size = DEFAULT_REFERENCE_SIZE
        if not self._metadata_path.exists():
            return
        try:
            with self._metadata_path.open("r", encoding="utf-8") as file:
                metadata = json.load(file)
            width = int(metadata["reference_width"])
            height = int(metadata["reference_height"])
            if width > 0 and height > 0:
                self._reference_size = (width, height)
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
        success = cv2.imwrite(str(self._template_path), gray)
        if not success:
            raise IOError(f"模板保存失败: {self._template_path}")

        # 加载到内存
        self._template = gray
        self._reference_size = (img_w, img_h)
        try:
            with self._metadata_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump({
                    "reference_width": img_w,
                    "reference_height": img_h,
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
            reference_width, reference_height = self._reference_size
            base_scale = min(
                gray.shape[1] / reference_width,
                gray.shape[0] / reference_height,
            )
            scales = [base_scale * factor for factor in (0.85, 0.925, 1.0, 1.075, 1.15)]
            scales.append(1.0)
            best_value = -1.0
            best_scale = 1.0
            for scale in sorted({round(value, 4) for value in scales if value > 0}):
                width = max(1, round(self._template.shape[1] * scale))
                height = max(1, round(self._template.shape[0] * scale))
                if width > gray.shape[1] or height > gray.shape[0]:
                    continue
                template = cv2.resize(self._template, (width, height), interpolation=cv2.INTER_AREA)
                result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val > best_value:
                    best_value = max_val
                    best_scale = scale
            if best_value < 0:
                logger.debug("所有模板缩放比例均大于当前截图，跳过匹配")
                return False, 0.0
            self._last_match_scale = best_scale
            max_val = best_value
            matched = bool(max_val >= threshold)
            logger.debug("模板匹配: 置信度=%.4f, 缩放=%.4f, 阈值=%.2f, %s",
                         max_val, best_scale, threshold, "匹配" if matched else "不匹配")
            return matched, float(max_val)
        except Exception as e:
            logger.error("模板匹配异常: %s", e)
            logger.debug(traceback.format_exc())
            return False, 0.0

    # ── 删除 ──────────────────────────────────────────────────────────

    def delete_template(self) -> None:
        """删除模板文件。"""
        self._template = None
        self._reference_size = DEFAULT_REFERENCE_SIZE
        self._last_match_scale = 1.0
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
