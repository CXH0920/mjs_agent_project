"""OCR 模块加载器。

集中管理配置页所需模板管理器，以及兼容旧调用的识别器缓存。

活动截图、文件导入和轮询路径统一通过 ``OcrWorker`` 执行识别，
不应直接调用本模块的 ``get_recognizer``。
消除其他模块对主窗口的导入依赖。
"""

from __future__ import annotations


_template_managers = {}
_recognizer = None
_recognizer_rois = None
_recognizer_hero_names = None
_recognizer_reference_size = None


def get_template_manager(template_name: str = "hero_selection"):
    """获取指定模板管理器（按模板名称缓存，延迟加载）。"""
    if template_name not in {"hero_selection", "match_guide"}:
        raise ValueError(f"不支持的模板名称: {template_name}")
    if template_name not in _template_managers:
        from src.ocr.template_manager import TemplateManager
        _template_managers[template_name] = TemplateManager(template_name=template_name)
    return _template_managers[template_name]


def get_recognizer(rois, hero_names: list[str] | None = None,
                   reference_size: tuple[int, int] = (2560, 1440)):
    """兼容旧调用：获取或初始化 GeneralRecognizer（单例，延迟加载）。

    活动识别路径应改用 ``src.business.ocr_worker.OcrWorker``；
    此函数仅保留给尚未迁移的外部调用。
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
