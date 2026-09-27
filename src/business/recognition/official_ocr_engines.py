"""官方榜单导入的双 OCR 引擎持有者：懒加载、罕见字兜底策略与跨任务移交。

从 OfficialDataImportService 拆出的引擎生命周期域：导入服务只认 main/rare_char
两个识别入口；ocr_worker 经 ocr/rare_char_ocr 原始值属性在任务前后移交引擎，
避免跨任务重复加载模型。
"""

from __future__ import annotations

import logging

from src.config.env import get_mumu_config
from src.ocr.paddle_loader import create_paddle_ocr

logger = logging.getLogger(__name__)


class OfficialOcrEngines:
    """按需加载简体主引擎与罕见字兜底引擎，记录兜底引擎的失败状态。"""

    def __init__(self, ocr=None, rare_char_ocr=None) -> None:
        self._ocr = ocr
        self._rare_char_ocr = rare_char_ocr
        self.rare_char_failed = False

    @property
    def ocr(self):
        """已注入的简体引擎原始值（不触发懒加载），供 ocr_worker 回收复用。"""
        return self._ocr

    @property
    def rare_char_ocr(self):
        """已注入的罕见字引擎原始值（不触发懒加载），供 ocr_worker 回收复用。"""
        return self._rare_char_ocr

    @property
    def main(self):
        """简体主引擎：首次访问时加载。"""
        if self._ocr is None:
            logger.info("正在加载官方榜单 OCR 模型")
            self._ocr = create_paddle_ocr(
                use_angle_cls=False,
                lang="ch",
                show_log=False,
            )
        return self._ocr

    @property
    def rare_char(self):
        """按需加载罕见字兜底引擎：复核开关开启时优先 v6，不可用回退 cht。

        v6 复核引擎（RapidOCR/ONNX）与生产管线共用 engine.ocr 接口，读数仅在
        allowed_names 候选闭包内被采纳（_recognize_name_with_engine 既有纪律）。
        """
        if self.rare_char_failed:
            return None
        if self._rare_char_ocr is None:
            try:
                if get_mumu_config().get("mumu_ocr_recheck_enabled", False):
                    from src.ocr.paddle_loader import get_recheck_ocr_engine

                    v6_engine = get_recheck_ocr_engine()
                    if v6_engine is not None:
                        logger.info("官方榜单罕见字兜底使用 v6 复核引擎")
                        self._rare_char_ocr = v6_engine
                        return self._rare_char_ocr
                logger.info("正在加载官方榜单罕见字 OCR 模型")
                self._rare_char_ocr = create_paddle_ocr(
                    use_angle_cls=False, lang="chinese_cht", show_log=False,
                )
            except Exception as exc:
                logger.warning("罕见字 OCR 模型不可用，将保留原结果待复核: %s", exc)
                self.rare_char_failed = True
                return None
        return self._rare_char_ocr
