"""
武将名称识别模块

使用 PaddleOCR 对 8 个武将名称区域进行 OCR 识别。
识别策略：
  1. 同类名称 ROI 拼图后批量执行 PaddleOCR，异常槽位逐槽复核
  2. 按字数门禁建立候选闭包，多路证据必须在候选交集内确认
  3. 等长且仅错一字时，在合法候选内使用结构化字形评分决胜
  4. 全部证据族以极高置信度一致读出词表外原文时不做评分决胜绑定：有候选时
     保留候选待人工确认，完全无候选时判为新武将（unknown_new_hero）

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
from src.ocr.batch_canvas import build_batch_canvas, join_name_fragments, split_canvas_groups
from src.ocr.character_similarity import CharacterSimilarityService, levenshtein_distance
from src.ocr.image_preprocessor import ImagePreprocessor
from src.ocr.name_resolution import NameResolver
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout, OcrRoiSlot

logger = logging.getLogger(__name__)

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
        # 名称证据解析与页面消歧的纯决策层（审计 G3 切片 4.6a 出仓）
        self._resolver = NameResolver(self._hero_names, self._similarity_service)
        self._timing_ms: dict[str, float] = {}
        # 已打过 ROI 明细的缩放比：同实例同缩放下 ROI 坐标恒定，仅首次识别打
        self._logged_roi_scale: tuple[float, float] | None = None

    # ── OCR 引擎 ──────────────────────────────────────────────────────

    @property
    def _engine(self):
        """PaddleOCR（ch），延迟加载；加载失败后熔断，避免每次识别重复重试。"""
        if self._ocr is False:
            raise RuntimeError(
                "PaddleOCR 引擎此前加载失败，已熔断（重启应用后可重试）"
            )
        if self._ocr is None:
            logger.info("首次调用，正在加载 PaddleOCR 模型...")
            try:
                started = time.perf_counter()
                from src.ocr.paddle_loader import create_paddle_ocr
                self._ocr = create_paddle_ocr(
                    use_angle_cls=False,
                    lang="ch",
                    show_log=False,
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                self._timing_ms["model_load"] = self._timing_ms.get("model_load", 0.0) + elapsed_ms
                logger.info("PaddleOCR 模型加载完成，耗时 %.1fms", elapsed_ms)
            except Exception as e:
                logger.error("PaddleOCR 模型加载失败: %s", e)
                logger.debug(traceback.format_exc())
                self._ocr = False  # 熔断标记：后续识别快速失败，不再重复加载
                raise
        return self._ocr

    def adopt_engine(self, engine) -> None:
        """注入外部共享的 PaddleOCR 引擎，避免同进程重复加载多份模型。"""
        self._ocr = engine

    def shared_engine(self):
        """返回已加载的引擎供跨识别器共享；未加载或已熔断时返回 None。"""
        return self._ocr if self._ocr else None

    def ensure_engine(self):
        """确保 PaddleOCR 引擎已加载并返回它；首次调用触发惰性加载（不可中断）。"""
        return self._engine

    @property
    def _recheck_engine(self):
        """v6 复核引擎（B2 复核模式）；开关关闭或引擎不可用时返回 None。"""
        from src.config.env import get_mumu_config
        if not get_mumu_config().get("mumu_ocr_recheck_enabled", False):
            return None
        from src.ocr.paddle_loader import get_recheck_ocr_engine
        return get_recheck_ocr_engine()

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
        canvas, _ = build_batch_canvas({slot: prepared for slot in range(1, 9)})
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
            含候选、确认状态、长度模式和多路证据的槽位结果。
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
        log_rois = self._logged_roi_scale != (scale_x, scale_y)
        self._logged_roi_scale = (scale_x, scale_y)
        logger.debug("武将 ROI 缩放: %.4f×%.4f，当前截图=%sx%s，参考=%sx%s",
                     scale_x, scale_y, image_width, image_height,
                     reference_width, reference_height)

        raw_slots: dict[int, np.ndarray] = {}
        prepared_slots: dict[int, np.ndarray] = {}
        for i, slot in enumerate(self._layout.slots):
            x, y, w, h = slot.name_roi
            roi_x = round(x * scale_x)
            roi_y = round(y * scale_y)
            roi_w = max(1, round(w * scale_x))
            roi_h = max(1, round(h * scale_y))
            if log_rois:
                logger.debug(
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
            raw_slots[i + 1] = roi_img
            preprocess_started = time.perf_counter()
            prepared_slots[i + 1] = self._preprocessor.preprocess_roi(roi_img)
            self._add_timing("name_preprocess", preprocess_started)

        batch_evidence: dict[int, list[dict]] = {}
        recognized = self._recognize_prepared_batch(
            prepared_slots, "name", evidence_by_slot=batch_evidence,
        )
        results: list[dict] = []
        for i, _slot in enumerate(self._layout.slots, 1):
            prepared = prepared_slots.get(i)
            if prepared is None:
                results.append(self._resolver.empty_name_result(i))
                continue
            evidence = list(batch_evidence.get(i, []))
            batch_text, batch_confidence = recognized.get(i, ("", 0.0))
            initial = self._resolver.resolve_name_evidence(i, evidence)
            if self._resolver.requires_slot_recheck(initial, batch_text, batch_confidence):
                self._append_single_name_evidence(evidence, raw_slots[i], i)
            result = self._resolver.resolve_name_evidence(i, evidence)
            results.append(result)
            logger.debug(
                "武将 %d 识别: %s (状态=%s, 原文=%r)",
                i, result["name"] or "(未确认)", result["resolution"], result["raw_name"],
            )

        final = self._resolver.resolve_page_names(results)
        self._recheck_unresolved_slots(final, prepared_slots)
        return final

    def _recognize_match_guide(self, image: np.ndarray) -> list[dict]:
        """识别 2v2 对局中的角色名与楚/汉军标签。"""
        image_height, image_width = image.shape[:2]
        reference_width, reference_height = self._layout.reference_size
        scale_x = image_width / reference_width
        scale_y = image_height / reference_height
        raw_name_slots: dict[int, np.ndarray] = {}
        name_slots: dict[int, np.ndarray] = {}
        team_slots: dict[int, np.ndarray] = {}
        for seat_index, slot in enumerate(self._layout.slots, 1):
            name_img = self._crop_roi(image, list(slot.name_roi), scale_x, scale_y)
            if name_img is None:
                continue
            raw_name_slots[seat_index] = name_img
            preprocess_started = time.perf_counter()
            name_slots[seat_index] = self._preprocessor.preprocess_roi(name_img)
            self._add_timing("name_preprocess", preprocess_started)
            if slot.team_roi is not None:
                team_img = self._crop_roi(image, list(slot.team_roi), scale_x, scale_y)
                if team_img is not None:
                    preprocess_started = time.perf_counter()
                    team_slots[seat_index] = self._preprocessor.preprocess_roi(team_img)
                    self._add_timing("team_preprocess", preprocess_started)

        name_evidence: dict[int, list[dict]] = {}
        recognized_names = self._recognize_prepared_batch(
            name_slots, "name", evidence_by_slot=name_evidence,
        )
        recognized_teams = self._recognize_prepared_batch(team_slots, "team")
        results: list[dict] = []
        for seat_index, _slot in enumerate(self._layout.slots, 1):
            prepared_name = name_slots.get(seat_index)
            if prepared_name is None:
                continue
            evidence = list(name_evidence.get(seat_index, []))
            batch_text, batch_confidence = recognized_names.get(seat_index, ("", 0.0))
            initial = self._resolver.resolve_name_evidence(seat_index, evidence)
            if self._resolver.requires_slot_recheck(initial, batch_text, batch_confidence):
                self._append_single_name_evidence(
                    evidence, raw_name_slots[seat_index], seat_index,
                )
            name_result = self._resolver.resolve_name_evidence(seat_index, evidence)
            team_text, _ = recognized_teams.get(seat_index, ("", 0.0))
            prepared_team = team_slots.get(seat_index)
            if not team_text and prepared_team is not None:
                team_text, _ = self._recognize_prepared_single(
                    prepared_team, seat_index, "team",
                )
            team = self._normalize_team(team_text, seat_index)
            name_result["team"] = team
            results.append(name_result)
        final = self._resolver.resolve_page_names(results)
        self._recheck_unresolved_slots(final, name_slots)
        return final

    def _append_single_name_evidence(
        self,
        evidence: list[dict],
        raw_roi: "np.ndarray",
        slot: int,
    ) -> None:
        """仅为未确认槽位补充 gamma 提亮图和 plain 放大图两路证据。"""
        enhanced = self._preprocessor.preprocess_roi_enhanced(raw_roi)
        text, confidence = self._recognize_prepared_single(enhanced, slot, "name")
        self._resolver.append_evidence(evidence, "single_enhanced", text, confidence)
        plain = self._preprocess_plain_roi(raw_roi)
        text, confidence = self._recognize_prepared_single(plain, slot, "name")
        self._resolver.append_evidence(evidence, "single_plain", text, confidence)

    def _recheck_unresolved_slots(
        self,
        results: list[dict],
        prepared_slots: dict[int, np.ndarray],
    ) -> None:
        """B2 复核模式：对页面消解后仍未决的槽位用 v6 复核引擎补充证据。

        触发条件：resolution ∈ {unresolved, conflict} 且候选闭包非空。喂法与
        生产同构（3x 灰度条、30px 间隙、960 分组画布）。接受纪律（8b8a3d5
        误绑事故教训）：读数必须精确命中该槽候选闭包内的成员才作为
        source="recheck" 证据注入并重跑消解；不命中一律维持原状，绝不引入新名字。
        """
        pending = [
            item for item in results
            if item["resolution"] in {"unresolved", "conflict"} and item["candidates"]
        ]
        if not pending:
            return
        engine = self._recheck_engine
        if engine is None:
            return
        strips = {
            item["index"]: prepared_slots[item["index"]]
            for item in pending
            if item["index"] in prepared_slots
        }
        if not strips:
            return
        # 页面唯一性已确认的名字不可再被复核绑定，避免同页重名被复核坐实
        occupied = {item["name"] for item in results if item["name"]}
        recognized = self._recognize_prepared_batch(strips, "name", engine=engine)
        for item in pending:
            text, confidence = recognized.get(item["index"], ("", 0.0))
            if not text:
                continue
            if text not in set(item["candidates"]) - occupied:
                logger.debug(
                    "武将 %d 复核读数 %r 未命中候选闭包，维持原状", item["index"], text,
                )
                continue
            item.update(self._resolver.resolve_name_evidence(item["index"], [
                *item["evidence"],
                {
                    "source": "recheck",
                    "text": text,
                    "confidence": round(float(confidence), 4),
                },
            ]))
            logger.info(
                "武将 %d 复核确认: %r → %s (%s, %.4f)",
                item["index"], item["raw_name"], item["name"] or "(未决)",
                item["resolution"], item["confidence"],
            )
            if item["name"]:
                occupied.add(item["name"])

    @staticmethod
    def _preprocess_plain_roi(roi: np.ndarray) -> np.ndarray:
        enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)

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

    @staticmethod
    def _normalize_team(text: str, slot: int) -> str:
        normalized = text.replace(" ", "").replace("【", "").replace("】", "")
        if "楚" in normalized:
            return "楚军"
        if "汉" in normalized:
            return "汉军"
        if text:
            logger.debug("武将 %d 阵营标签未识别: %r", slot, text)
        return ""

    def _recognize_prepared_single(
        self, prepared: np.ndarray, slot: int, kind: str,
    ) -> tuple[str, float]:
        """识别已预处理的单个 ROI，供批处理异常槽位回退。"""
        ocr_started = time.perf_counter()
        text, confidence = self._extract_text(self._engine.ocr(prepared, cls=False))
        self._add_timing(f"{kind}_ocr", ocr_started)
        if text:
            logger.debug("武将 %d %s OCR 原始结果: text=%r, confidence=%.4f", slot, kind, text, confidence)
        return text, confidence

    def _recognize_prepared_batch(
        self,
        prepared_slots: dict[int, np.ndarray],
        kind: str,
        evidence_by_slot: dict[int, list[dict]] | None = None,
        engine=None,
    ) -> dict[int, tuple[str, float]]:
        """将同类 ROI 分块拼图检测（画布不超过检测器工作尺度）；异常槽位由调用方逐槽回退。

        engine 缺省用主引擎；B2 复核模式传入 v6 复核引擎对未决槽重读。
        """
        mapped: dict[int, list[tuple[str, float, float]]] = {slot: [] for slot in prepared_slots}
        for group in split_canvas_groups(prepared_slots):
            canvas, ranges = build_batch_canvas({slot: prepared_slots[slot] for slot in group})
            try:
                ocr_started = time.perf_counter()
                result = (engine or self._engine).ocr(canvas, cls=False)
                self._add_timing(f"{kind}_ocr", ocr_started)
            except Exception as exc:
                logger.warning("%s ROI 拼图 OCR 失败，将逐槽回退: %s", kind, exc)
                continue
            for line in (result[0] if result and result[0] else []):
                try:
                    box, (text, confidence) = line
                    center_x = sum(point[0] for point in box) / len(box)
                    center_y = sum(point[1] for point in box) / len(box)
                    slot = next(
                        (index for index, (left, right) in ranges.items() if left <= center_x < right),
                        None,
                    )
                    text = text.strip()
                    if slot is not None and text:
                        mapped[slot].append((text, float(confidence), center_y))
                except (IndexError, TypeError, ValueError):
                    logger.warning("%s ROI 拼图返回了无法映射的检测框", kind)

        recognized: dict[int, tuple[str, float]] = {}
        for slot, candidates in mapped.items():
            if kind == "name" and len(candidates) > 1:
                candidates = join_name_fragments(slot, candidates)
            if evidence_by_slot is not None:
                for text, confidence, _center_y in candidates:
                    self._resolver.append_evidence(
                        evidence_by_slot.setdefault(slot, []),
                        f"batch_{'plain' if kind == 'name' else kind}",
                        text,
                        confidence,
                    )
            if (
                len(candidates) == 1
                and candidates[0][1] >= _BATCH_MIN_CONFIDENCE
                and not (kind == "name" and self._requires_name_batch_fallback(candidates[0][0]))
            ):
                recognized[slot] = (candidates[0][0], candidates[0][1])
            elif candidates:
                logger.info("武将 %d %s 拼图结果不唯一或置信度过低，逐槽回退", slot, kind)
        return recognized

    def _requires_name_batch_fallback(self, text: str) -> bool:
        """避免截断文本被多候选纠错静默绑定到错误武将。"""
        if not self._hero_names or text in self._hero_names:
            return False
        candidates = [
            hero for hero in self._hero_names
            if levenshtein_distance(text, hero)
            <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
        ]
        return len(candidates) != 1

    def _add_timing(self, key: str, started: float) -> None:
        self._timing_ms[key] = self._timing_ms.get(key, 0.0) + (time.perf_counter() - started) * 1000

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
    def save_results(results: list[dict], json_path: str | Path, image_path: str | Path | None = None, page_type: str = "wujiang_select") -> None:
        """将识别结果保存为 JSON 文件。"""
        data = {
            "image": str(image_path) if image_path else "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page_type": page_type,
            "generals": results,
        }
        json_path = Path(json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(json_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception as e:
            logger.error("识别结果保存失败 %s: %s", json_path, e)
