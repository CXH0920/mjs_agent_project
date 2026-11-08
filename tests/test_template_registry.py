# -*- coding: utf-8 -*-
"""template_registry 同名测试：模板管理器单例注册表（D5 零覆盖冒烟补测）。"""

from __future__ import annotations

import pytest
from src.ocr import template_registry
from src.ocr.template_registry import get_template_manager


@pytest.fixture(autouse=True)
def _isolate_singleton_cache():
    """用例前后清空模块级缓存：Fake 实例不得泄入其他用例的真实消费方。"""
    template_registry._template_managers.clear()
    yield
    template_registry._template_managers.clear()


def test_get_template_manager_caches_per_name(monkeypatch) -> None:
    """同名两次返回同一实例，不同名各建一份（延迟加载只建一次）。"""
    created: list[str] = []

    class _FakeTemplateManager:
        def __init__(self, template_name: str) -> None:
            created.append(template_name)

    monkeypatch.setattr("src.ocr.template_manager.TemplateManager", _FakeTemplateManager)

    first = get_template_manager()
    again = get_template_manager()
    other = get_template_manager("match_guide")

    assert first is again
    assert other is not first
    assert created == ["hero_selection", "match_guide"]


def test_get_template_manager_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="不支持的模板名称"):
        get_template_manager("bogus")
