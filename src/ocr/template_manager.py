"""
模板管理模块

负责武将选择页面的模板截图的保存、加载、OpenCV 模板匹配。
模板是用户框选的一个小区域图片（如页面标题或按钮），
用于检测当前画面是否为武将选择页面。
对局攻略页（match_guide）因动态背景漏检，改用随包双锚点 AND + 期望位置
校验判定，锚点加载失败时回退本模块的大模板漏斗。
"""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
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

# 跨实例时序缓存：实际加载的模板路径 → (模板文件 mtime_ns, 上次匹配的最佳缩放比例)。
# ocr_worker 每个任务都新建 TemplateManager，实例字段无法跨轮保留；该缓存让
# "上一轮命中的缩放比例"在下一轮直接作为局部复验的首选，免去多尺度扫描。
# mtime 校验保证模板重制/替换后缓存自动失效。
_LAST_SCALE_CACHE: dict[str, tuple[int, float]] = {}
# 已打过"模板已加载"日志的模板路径：轮询每轮新建实例并重新 imread，同一模板
# 路径只在进程内首次加载时打一次 DEBUG。模板重制走 set_template 的 INFO 可见。
_LOGGED_LOADED_PATHS: set[str] = set()
# 全图兜底的粗扫降采样比例与甄别余量：非目标页全尺度得分实测 ~0.43（阈值 0.8），
# 1/4 降采样的得分损失远小于该差距，粗扫可可靠区分"布局偏移"与"完全非目标页"
_COARSE_SCAN_RATIO = 0.25
_COARSE_SCAN_MARGIN = 0.15

# ── 对局攻略页（match_guide）锚点判定 ─────────────────────────────
# 该页动态背景导致大模板漏检，改用双锚点 AND + 期望位置校验：
# 两个固定 UI 小图（礼物图标 / 整理按钮）各自分数达标且峰位落在期望坐标容差内才判命中。
# 锚点是随包游戏 UI 特征而非用户数据，不支持用户覆盖；清单/图片加载失败自动回退大模板漏斗。
_MATCH_GUIDE_ANCHOR_MANIFEST = BUNDLED_TEMPLATE_DIR / "match_guide" / "anchors.json"
_ANCHOR_LOCAL_PADDING_RATIO = 0.2
# 参考系位置容差（1440p 实测，见 .tmp_test/verify_dual_anchor.py）；全图兜底用 2 倍宽容差
# 吸收截图链路轻微平移与 1/4 粗扫量化误差（实测 11px 处假峰 0.837 < 分数下限，放宽安全）
_ANCHOR_TOLERANCE_REF = 6
_ANCHOR_FULL_TOLERANCE_REF = 12
# 锚点分数下限：30 张截图实测最低命中 0.908、最近假峰 0.837；外部阈值只能向上收紧
_ANCHOR_SCORE_FLOOR = 0.85
_ANCHOR_STALE_WARN_ROUNDS = 50
# 连续未命中轮数（跨实例累计：ocr_worker 每任务新建 TemplateManager，实例字段攒不到告警轮数）
_ANCHOR_MISS_ROUNDS: dict[str, int] = {}


@dataclass(frozen=True)
class _Anchor:
    """对局攻略页判定锚点：随包灰度小图与参考系（2560×1440）期望位置。"""

    name: str
    image: np.ndarray
    x: int
    y: int
    w: int
    h: int


class TemplateManager:
    """模板管理器 — 保存/加载/匹配武将选择页面模板"""

    def __init__(
        self,
        template_path: str | Path | None = None,
        *,
        template_name: str = "hero_selection",
        anchor_manifest_path: Path | None = None,
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
        self._loaded_template_path: Path | None = None
        self._last_match_scale = 1.0
        self._has_scale_history = False
        self._last_match_strategy = "unmatched"
        # 锚点仅对 match_guide 生效（测试经 anchor_manifest_path 注入合成清单）
        self._anchor_manifest_path = (
            Path(anchor_manifest_path) if anchor_manifest_path is not None
            else _MATCH_GUIDE_ANCHOR_MANIFEST
        )
        self._anchors: list[_Anchor] = []
        self._anchor_reference_size = DEFAULT_REFERENCE_SIZE
        self._anchor_history_scale: float | None = None
        self._anchor_best_observed = 0.0
        self._load()
        if template_name == "match_guide":
            self._load_anchors(self._anchor_manifest_path)

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
                self._loaded_template_path = path
                self._restore_scale_history(path)
                self._load_metadata(path.with_suffix(".json"))
                if str(path) not in _LOGGED_LOADED_PATHS:
                    _LOGGED_LOADED_PATHS.add(str(path))
                    logger.debug("模板已加载: %s (%sx%s)", path.name, img.shape[1], img.shape[0])
            else:
                logger.warning("模板文件读取失败: %s", self._template_path)
                self._template = None
        except Exception as e:
            logger.error("模板加载异常: %s", e)
            logger.debug(traceback.format_exc())
            self._template = None

    def _restore_scale_history(self, path: Path) -> None:
        """从跨实例缓存恢复上次匹配的缩放比例；文件已变更时视为失效。"""
        try:
            cached = _LAST_SCALE_CACHE.get(str(path))
            if cached and cached[0] == path.stat().st_mtime_ns:
                self._last_match_scale = cached[1]
                self._has_scale_history = True
                return
        except OSError:
            pass
        self._last_match_scale = 1.0
        self._has_scale_history = False

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

    def _load_anchors(self, manifest_path: Path) -> None:
        """加载对局攻略锚点清单；任一环节失败整体回退大模板判定（WARNING 可见）。"""
        try:
            with manifest_path.open("r", encoding="utf-8") as file:
                manifest = json.load(file)
            reference_width = int(manifest["reference_width"])
            reference_height = int(manifest["reference_height"])
            if reference_width <= 0 or reference_height <= 0:
                raise ValueError("参考尺寸非法")
            anchors: list[_Anchor] = []
            for item in manifest["anchors"]:
                anchor = _Anchor(
                    name=str(item["name"]),
                    image=self._read_anchor_image(manifest_path.parent / str(item["file"])),
                    x=int(item["x"]), y=int(item["y"]),
                    w=int(item["w"]), h=int(item["h"]),
                )
                if (anchor.w <= 0 or anchor.h <= 0 or anchor.x < 0 or anchor.y < 0
                        or anchor.x + anchor.w > reference_width
                        or anchor.y + anchor.h > reference_height):
                    raise ValueError(f"锚点坐标越界: {anchor.name}")
                if anchor.image.shape[:2] != (anchor.h, anchor.w):
                    raise ValueError(f"锚点图片尺寸与清单不符: {anchor.name}")
                anchors.append(anchor)
            if len(anchors) < 2:
                raise ValueError(f"锚点数量不足（双锚点 AND 判定至少 2 个）: {len(anchors)}")
            self._anchors = anchors
            self._anchor_reference_size = (reference_width, reference_height)
            # 锚点清单更新（发版）后 mtime 变化，历史缩放自动失效，机制与大模板一致
            try:
                mtime = manifest_path.stat().st_mtime_ns
            except OSError:
                mtime = None
            cached = _LAST_SCALE_CACHE.get(str(manifest_path))
            if mtime is not None and cached and cached[0] == mtime:
                self._anchor_history_scale = cached[1]
            if str(manifest_path) not in _LOGGED_LOADED_PATHS:
                _LOGGED_LOADED_PATHS.add(str(manifest_path))
                logger.info("对局攻略锚点已加载: %s (%d 个)", manifest_path.name, len(anchors))
        except Exception as e:
            self._anchors = []
            logger.warning("对局攻略锚点加载失败，回退大模板判定: %s", e)
            logger.debug(traceback.format_exc())

    @staticmethod
    def _read_anchor_image(path: Path) -> np.ndarray:
        with path.open("rb") as file:
            image = cv2.imdecode(np.frombuffer(file.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            raise ValueError(f"锚点图片解码失败: {path.name}")
        return image

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
        self._loaded_template_path = self._template_path
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
        # 锚点分支置于模板缺失早退之前：锚点判定不依赖大模板，大模板仅是加载失败的回退
        if self._anchors:
            return self._match_with_anchors(image, threshold)

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
            history_scale = self._last_match_scale if self._has_scale_history else None
            self._last_match_scale = 1.0
            self._last_match_strategy = "unmatched"
            reference_width, reference_height = self._reference_size
            base_scale = min(
                gray.shape[1] / reference_width,
                gray.shape[0] / reference_height,
            )
            scales = self._candidate_scales(base_scale)
            local_region = self._local_search_region(gray, base_scale)

            # 时序复验：模拟器分辨率不会逐帧变化，上一轮的最佳缩放比例大概率仍命中，
            # 在期望位置附近直接复验即可返回，免去整轮多尺度扫描
            if (
                history_scale is not None
                and self._template_roi is not None
                and history_scale != base_scale
            ):
                cached_region = self._local_search_region(gray, history_scale)
                cached_value = self._match_at_scale(gray, history_scale, cached_region)
                if cached_value is not None and cached_value >= threshold:
                    self._set_match_details(history_scale, "cached_local")
                    return True, float(cached_value)

            base_value = self._match_at_scale(gray, base_scale, local_region)
            base_strategy = "base_local" if local_region is not None else "base_full"
            if base_value is not None and base_value >= threshold:
                self._set_match_details(base_scale, base_strategy)
                return True, float(base_value)

            best_value = base_value if base_value is not None else -1.0
            best_scale = base_scale
            # 局部多尺度：各候选缩放的期望位置仍由模板 ROI 比例定位（缩放只影响窗口
            # 大小，不影响位置），先在局部完成全部候选缩放，命中即免掉全图扫描
            if local_region is not None:
                for scale in scales:
                    if scale == round(base_scale, 4):
                        continue
                    value = self._match_at_scale(gray, scale, self._local_search_region(gray, scale))
                    if value is not None and value > best_value:
                        best_value = value
                        best_scale = scale
                if best_value >= threshold:
                    self._set_match_details(best_scale, "fallback_local_multiscale")
                    return True, float(best_value)

            # 全图兜底：先对全部候选缩放做 1/4 降采样粗扫，只有粗扫得分逼近阈值的
            # 最优缩放才回原尺寸做全图精扫。非目标页（轮询常态）全尺度得分远低于
            # 阈值，粗扫即可判否，避免每拍都付整幅全图多尺度扫描的几百毫秒
            fallback_scales = scales if local_region is not None else [
                scale for scale in scales if scale != round(base_scale, 4)
            ]
            coarse_gray = cv2.resize(
                gray, None, fx=_COARSE_SCAN_RATIO, fy=_COARSE_SCAN_RATIO,
                interpolation=cv2.INTER_AREA,
            )
            coarse_threshold = max(0.0, threshold - _COARSE_SCAN_MARGIN)
            best_coarse_value = -1.0
            best_coarse_scale: float | None = None
            for scale in fallback_scales:
                value = self._match_at_coarse_scale(coarse_gray, scale * _COARSE_SCAN_RATIO)
                if value is not None and value > best_value:
                    best_value = value
                    best_scale = scale
                if value is not None and value > best_coarse_value:
                    best_coarse_value = value
                    best_coarse_scale = scale
            if best_coarse_scale is not None and best_coarse_value >= coarse_threshold:
                value = self._match_at_scale(gray, best_coarse_scale)
                if value is not None and value > best_value:
                    best_value = value
                    best_scale = best_coarse_scale
            if best_value < 0:
                logger.debug("所有模板缩放比例均大于当前截图，跳过匹配")
                return False, 0.0
            refined_coarse = best_coarse_scale is not None and best_coarse_value >= coarse_threshold
            if refined_coarse:
                fallback_strategy = (
                    "fallback_full_multiscale" if local_region is not None else "fallback_multiscale"
                )
            else:
                # 粗扫即判否（完全非目标页），未发生原尺寸全图扫描
                fallback_strategy = "coarse_reject_multiscale"
            self._set_match_details(best_scale, fallback_strategy)
            matched = bool(best_value >= threshold)
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

    def _match_at_coarse_scale(self, coarse_gray: np.ndarray, coarse_scale: float) -> float | None:
        """在降采样图上按已折算的比例做一次全图模板匹配（全图兜底的粗扫）。"""
        width = max(1, round(self._template.shape[1] * coarse_scale))
        height = max(1, round(self._template.shape[0] * coarse_scale))
        if width > coarse_gray.shape[1] or height > coarse_gray.shape[0]:
            return None
        template = cv2.resize(self._template, (width, height), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(coarse_gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, _ = cv2.minMaxLoc(result)
        return float(max_value)

    def _set_match_details(self, scale: float, strategy: str) -> None:
        self._last_match_scale = scale
        self._last_match_strategy = strategy
        self._has_scale_history = True
        path = self._loaded_template_path
        if path is not None:
            try:
                _LAST_SCALE_CACHE[str(path)] = (path.stat().st_mtime_ns, scale)
            except OSError:
                pass

    # ── 对局攻略锚点匹配 ──────────────────────────────────────────────

    def _match_with_anchors(self, image: np.ndarray | Image.Image, threshold: float) -> tuple[bool, float]:
        """对局攻略页锚点判定：双锚点 AND + 期望位置校验，按三层漏斗推进。

        外部阈值低于分数下限时按下限执行（锚点分数区间实测很窄，调低只会引入误报）。
        """
        effective_threshold = max(threshold, _ANCHOR_SCORE_FLOOR)
        if isinstance(image, Image.Image):
            image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except Exception as e:
            logger.error("图像转灰度失败: %s", e)
            return False, 0.0

        try:
            reference_width, reference_height = self._anchor_reference_size
            base_scale = min(gray.shape[1] / reference_width, gray.shape[0] / reference_height)
            self._last_match_scale = 1.0
            self._last_match_strategy = "anchors_miss"
            self._anchor_best_observed = 0.0

            # ① 时序复验：上一轮命中缩放即真实渲染比例（精度最高），先于基准缩放尝试
            history_scale = self._anchor_history_scale
            if history_scale is not None and history_scale != base_scale:
                confidence = self._anchors_local_score(gray, effective_threshold, history_scale)
                if confidence is not None:
                    return self._anchor_hit(history_scale, "anchors_cached_local", confidence)

            # ② 基准缩放 + 参考缩放局部（1.0 档兜高分屏上按参考原尺寸渲染的场景）。
            # 不做相对多尺度档：y=1230 的锚点要通过 ±6px 位置校验，候选缩放与真实
            # 缩放的偏差须 <0.5%，而常规 ±15% 档距全部超限，相对档实际无命中可能。
            for scale, strategy in ((base_scale, "anchors_local"), (1.0, "anchors_ref_local")):
                if strategy == "anchors_ref_local" and round(scale, 4) == round(base_scale, 4):
                    continue
                confidence = self._anchors_local_score(gray, effective_threshold, scale)
                if confidence is not None:
                    return self._anchor_hit(scale, strategy, confidence)

            # ③ 全图粗扫兜底：仅基准缩放一个比例，宽容差吸收轻微平移；粗扫不过即免精扫
            confidence = self._anchors_full_fallback(gray, effective_threshold, base_scale)
            if confidence is not None:
                return self._anchor_hit(base_scale, "anchors_full_fallback", confidence)

            self._note_anchor_miss(gray.shape)
            return False, float(self._anchor_best_observed)
        except Exception as e:
            logger.error("对局攻略锚点匹配异常: %s", e)
            logger.debug(traceback.format_exc())
            return False, 0.0

    def _anchors_local_score(self, gray: np.ndarray, threshold: float, scale: float) -> float | None:
        """两锚点全部在 scale 期望位置邻域命中（AND）；返回联合置信度（较小值）。"""
        scores: list[float] = []
        for anchor in self._anchors:
            center_x = round(anchor.x * scale)
            center_y = round(anchor.y * scale)
            score = self._anchor_window_score(
                gray, threshold, scale, anchor, center_x, center_y, _ANCHOR_TOLERANCE_REF)
            if score is None:
                return None  # 短路：前序锚点不过即不再试后续
            scores.append(score)
        return min(scores)

    def _anchor_window_score(
        self,
        gray: np.ndarray,
        threshold: float,
        scale: float,
        anchor: _Anchor,
        center_x: int,
        center_y: int,
        tolerance_ref: float,
    ) -> float | None:
        """单锚点在 (center_x, center_y) 邻域窗口做原尺寸匹配，分数与峰位双校验。

        峰位始终相对期望位置（参考坐标 × scale）校验；窗口中心在 ② 用期望位置、
        在 ③ 粗扫兜底用粗扫峰位，复用同一套裁剪与校验逻辑。
        """
        expected_x = round(anchor.x * scale)
        expected_y = round(anchor.y * scale)
        width = max(1, round(anchor.w * scale))
        height = max(1, round(anchor.h * scale))
        padding = max(2, round(width * _ANCHOR_LOCAL_PADDING_RATIO))
        left = max(0, center_x - padding)
        top = max(0, center_y - padding)
        right = min(gray.shape[1], center_x + width + padding)
        bottom = min(gray.shape[0], center_y + height + padding)
        region = gray[top:bottom, left:right]
        if region.shape[0] < height or region.shape[1] < width:
            return None  # 低分辨率/贴边时窗口容不下模板，matchTemplate 会抛错，直接判否
        template = cv2.resize(anchor.image, (width, height), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_loc = cv2.minMaxLoc(result)
        self._anchor_best_observed = max(self._anchor_best_observed, float(max_value))
        if max_value < threshold:
            return None
        tolerance = max(2, round(tolerance_ref * scale))
        if (abs(left + max_loc[0] - expected_x) > tolerance
                or abs(top + max_loc[1] - expected_y) > tolerance):
            return None
        return float(max_value)

    def _anchors_full_fallback(self, gray: np.ndarray, threshold: float, base_scale: float) -> float | None:
        """全图兜底：1/4 降采样粗扫，粗扫逼近阈值的锚点才在峰位邻域做原尺寸精扫。

        轮询常态是非目标页，小锚点粗扫即可判否（机制与大模板粗扫一致），位置校验
        由精扫窗口复用 _anchor_window_score 的宽容差版本完成。
        """
        coarse_gray = cv2.resize(
            gray, None, fx=_COARSE_SCAN_RATIO, fy=_COARSE_SCAN_RATIO,
            interpolation=cv2.INTER_AREA,
        )
        coarse_threshold = max(0.0, threshold - _COARSE_SCAN_MARGIN)
        scores: list[float] = []
        for anchor in self._anchors:
            width = max(1, round(anchor.w * base_scale))
            height = max(1, round(anchor.h * base_scale))
            coarse_width = max(1, round(width * _COARSE_SCAN_RATIO))
            coarse_height = max(1, round(height * _COARSE_SCAN_RATIO))
            if coarse_width > coarse_gray.shape[1] or coarse_height > coarse_gray.shape[0]:
                return None
            template = cv2.resize(anchor.image, (coarse_width, coarse_height), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(coarse_gray, template, cv2.TM_CCOEFF_NORMED)
            _, coarse_value, _, coarse_loc = cv2.minMaxLoc(result)
            if coarse_value < coarse_threshold:
                return None
            peak_x = round(coarse_loc[0] / _COARSE_SCAN_RATIO)
            peak_y = round(coarse_loc[1] / _COARSE_SCAN_RATIO)
            score = self._anchor_window_score(
                gray, threshold, base_scale, anchor, peak_x, peak_y, _ANCHOR_FULL_TOLERANCE_REF)
            if score is None:
                return None
            scores.append(score)
        return min(scores)

    def _anchor_hit(self, scale: float, strategy: str, confidence: float) -> tuple[bool, float]:
        """锚点命中收尾：记录策略、跨实例缓存缩放并清零未命中轮数。"""
        self._last_match_scale = scale
        self._last_match_strategy = strategy
        self._anchor_history_scale = scale  # 同实例下一轮 ① 直接复验
        manifest = self._anchor_manifest_path
        _ANCHOR_MISS_ROUNDS[str(manifest)] = 0
        try:
            _LAST_SCALE_CACHE[str(manifest)] = (manifest.stat().st_mtime_ns, scale)
        except OSError:
            pass
        return True, confidence

    def _note_anchor_miss(self, frame_shape: tuple[int, ...]) -> None:
        """连续未命中计数；达到阈值轮数打一条 WARNING（版本改版静默失效的唯一显式出口）。"""
        key = str(self._anchor_manifest_path)
        rounds = _ANCHOR_MISS_ROUNDS.get(key, 0) + 1
        _ANCHOR_MISS_ROUNDS[key] = rounds
        logger.debug("对局攻略锚点未命中: 观测最高分 %.4f", self._anchor_best_observed)
        if rounds == _ANCHOR_STALE_WARN_ROUNDS:
            reference_width, reference_height = self._anchor_reference_size
            logger.warning(
                "对局攻略锚点连续 %d 轮未命中，疑似游戏 UI 改版或截图分辨率异常 "
                "(当前 %dx%d，参考 %dx%d)，请核对 %s",
                rounds, frame_shape[1], frame_shape[0], reference_width, reference_height,
                self._anchor_manifest_path,
            )

    # ── 删除 ──────────────────────────────────────────────────────────

    def delete_template(self) -> None:
        """删除模板文件。"""
        self._template = None
        self._reference_size = DEFAULT_REFERENCE_SIZE
        self._template_roi = None
        self._loaded_template_path = None
        self._last_match_scale = 1.0
        self._has_scale_history = False
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
