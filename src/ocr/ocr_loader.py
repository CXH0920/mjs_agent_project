"""OCR 模块加载器。

集中管理 OCR 相关单例的延迟加载。
消除其他模块对主窗口的导入依赖。
"""

from __future__ import annotations


_template_manager = None
_recognizer = None
_recognizer_rois = None
_recognizer_hero_names = None
_recognizer_reference_size = None


def get_template_manager():
    """获取或初始化 TemplateManager（单例，延迟加载）。"""
    global _template_manager
    if _template_manager is None:
        from src.ocr.template_manager import TemplateManager
        _template_manager = TemplateManager()
    return _template_manager


def get_recognizer(rois, hero_names: list[str] | None = None,
                   reference_size: tuple[int, int] = (2560, 1440)):
    """获取或初始化 GeneralRecognizer（单例，延迟加载）。

    若 rois 或 hero_names 与上次不同，则重建实例。
    """
    global _recognizer, _recognizer_rois, _recognizer_hero_names, _recognizer_reference_size

    needs_rebuild = _recognizer is None
    if _recognizer is not None and (
        rois != _recognizer_rois or hero_names != _recognizer_hero_names
        or reference_size != _recognizer_reference_size
    ):
        logger = __import__("logging").getLogger(__name__)
        logger.info("ROI 或武将列表已变更，重新创建 Recognizer")
        needs_rebuild = True

    if needs_rebuild:
        from src.ocr.recognizer import GeneralRecognizer
        _recognizer = GeneralRecognizer(
            rois=rois, hero_names=hero_names, reference_size=reference_size,
        )
        _recognizer_rois = rois
        _recognizer_hero_names = hero_names
        _recognizer_reference_size = reference_size

    return _recognizer
