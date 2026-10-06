"""官方榜单导入的双 OCR 引擎持有者：懒加载、罕见字复核策略与跨任务移交。

从 OfficialDataImportService 拆出的引擎生命周期域：导入服务只认 main/rare_char
两个识别入口；ocr_worker 经 ocr/rare_char_ocr 原始值属性在任务前后移交引擎，
避免跨任务重复加载模型。B1 起主引擎与识别管线同源（RapidOCR/PP-OCRv6），
罕见字复核引擎与其互为异构（v4），cht 兜底链已随 paddle 退役。
"""

from __future__ import annotations

import logging

from src.config.env import get_mumu_config

logger = logging.getLogger(__name__)


class OfficialOcrEngines:
    """按需加载主引擎与罕见字复核引擎，记录复核引擎的失败状态。"""

    def __init__(self, ocr=None, rare_char_ocr=None) -> None:
        self._ocr = ocr
        self._rare_char_ocr = rare_char_ocr
        self.rare_char_failed = False

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
            return None
        if self._rare_char_ocr is None:
            if not get_mumu_config().get("mumu_ocr_recheck_enabled", False):
                return None
            from src.ocr.engine_loader import get_recheck_ocr_engine

            engine = get_recheck_ocr_engine()  # 内部已熔断，失败返回 None 不抛异常
            if engine is None:
                self.rare_char_failed = True
                logger.info("复核引擎不可用，罕见字保留原结果待复核")
                return None
            logger.info("官方榜单罕见字兜底使用复核引擎（与主引擎异构）")
            self._rare_char_ocr = engine
        return self._rare_char_ocr
