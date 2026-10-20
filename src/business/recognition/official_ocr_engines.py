"""官方榜单导入的双 OCR 引擎持有者：懒加载、罕见字复核策略与跨任务移交。

从 OfficialDataImportService 拆出的引擎生命周期域：导入服务只认 main/rare_char
两个识别入口；ocr_worker 经 ocr/rare_char_ocr 原始值属性在任务前后移交引擎，
避免跨任务重复加载模型。B1 起主引擎与识别管线同源（RapidOCR/PP-OCRv6），
罕见字复核引擎与其互为异构（v4），cht 兜底链已随 paddle 退役。
"""

from __future__ import annotations

import logging
import time

from src.config.env import get_mumu_config

logger = logging.getLogger(__name__)

# 罕见字兜底连续失败达阈值才熔断（TD-A5-001）：单次瞬时异常（显存不足、
# 单字不支持）只损失当格兜底，不再连坐整批导入的罕见字识别导致全量待复核。
# 熔断带冷却窗口，期满自动恢复重试；作用域为单个 OfficialOcrEngines 实例
# （每次导入任务新建），不跨任务。
_RARE_CHAR_FAIL_LIMIT = 3
_RARE_CHAR_COOLDOWN_SECONDS = 60.0


class OfficialOcrEngines:
    """按需加载主引擎与罕见字复核引擎，记录复核引擎的失败状态。"""

    def __init__(self, ocr=None, rare_char_ocr=None) -> None:
        self._ocr = ocr
        self._rare_char_ocr = rare_char_ocr
        self.rare_char_failed = False
        self._rare_char_fail_streak = 0
        self._rare_char_retry_at = 0.0

    def note_rare_char_failure(self) -> None:
        """记录一次罕见字兜底失败；连续达阈值才进入冷却熔断。"""
        self._rare_char_fail_streak += 1
        if self._rare_char_fail_streak >= _RARE_CHAR_FAIL_LIMIT:
            self._trip_rare_char(
                f"连续失败 {self._rare_char_fail_streak} 次，"
                f"{int(_RARE_CHAR_COOLDOWN_SECONDS)} 秒内降级保留原结果待复核"
            )

    def note_rare_char_success(self) -> None:
        """记录一次兜底成功（含读数未被采纳），清零连续失败计数。"""
        self._rare_char_fail_streak = 0

    def _trip_rare_char(self, reason: str) -> None:
        """进入熔断并登记冷却截止时间；引擎加载失败也走此即时路径。"""
        self.rare_char_failed = True
        self._rare_char_retry_at = time.monotonic() + _RARE_CHAR_COOLDOWN_SECONDS
        logger.info("罕见字兜底熔断：%s", reason)

    @property
    def ocr(self):
        """已注入的主引擎原始值（不触发懒加载），供 ocr_worker 回收复用。"""
        return self._ocr

    @property
    def rare_char_ocr(self):
        """已注入的罕见字复核引擎原始值（不触发懒加载），供 ocr_worker 回收复用。"""
        return self._rare_char_ocr

    @property
    def main(self):
        """主引擎（与识别管线同源，RapidOCR/PP-OCRv6）：首次访问时加载。"""
        if self._ocr is None:
            logger.info("正在加载官方榜单 OCR 主引擎")
            from src.ocr.engine_loader import get_primary_ocr_engine

            engine = get_primary_ocr_engine()
            if engine is None:
                raise RuntimeError(
                    "OCR 主引擎不可用（rapidocr/onnxruntime 或模型缺失，详见日志）"
                )
            self._ocr = engine
        return self._ocr

    @property
    def rare_char(self):
        """按需加载罕见字复核引擎（与主引擎互为异构：主 v6 时为 v4）。

        复核引擎（RapidOCR/ONNX）与生产管线共用 engine.ocr 接口，读数仅在
        allowed_names 候选闭包内被采纳（_recognize_name_with_engine 既有纪律）。
        复核开关关闭或引擎不可用时返回 None，保留原结果走待复核，不再回退
        第二识别引擎（cht 链已退役，B1 主引擎直读生僻字）。
        """
        if self.rare_char_failed:
            if time.monotonic() < self._rare_char_retry_at:
                return None
            # 冷却期满：解除熔断并允许重试（失败计数同步清零，避免一次失败再熔断）
            self.rare_char_failed = False
            self._rare_char_fail_streak = 0
            logger.info("罕见字兜底冷却结束，恢复重试")
        if self._rare_char_ocr is None:
            if not get_mumu_config().get("mumu_ocr_recheck_enabled", False):
                return None
            from src.ocr.engine_loader import get_recheck_ocr_engine

            engine = get_recheck_ocr_engine()  # 内部已熔断，失败返回 None 不抛异常
            if engine is None:
                self._trip_rare_char("复核引擎不可用，罕见字保留原结果待复核")
                return None
            logger.info("官方榜单罕见字兜底使用复核引擎（与主引擎异构）")
            self._rare_char_ocr = engine
        return self._rare_char_ocr
