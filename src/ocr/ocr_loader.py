"""OCR 模板管理器单例。

配置页与 OcrService 通过本模块获取按页面缓存的 TemplateManager；
识别器（GeneralRecognizer / PaddleOCR）由 ``OcrWorker`` 在 worker 线程内独占创建，
不经过本模块。
"""

from __future__ import annotations


_template_managers = {}


def get_template_manager(template_name: str = "hero_selection"):
    """获取指定模板管理器（按模板名称缓存，延迟加载）。"""
    if template_name not in {"hero_selection", "match_guide"}:
        raise ValueError(f"不支持的模板名称: {template_name}")
    if template_name not in _template_managers:
        from src.ocr.template_manager import TemplateManager
        _template_managers[template_name] = TemplateManager(template_name=template_name)
    return _template_managers[template_name]
