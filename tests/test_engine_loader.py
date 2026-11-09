# -*- coding: utf-8 -*-
"""engine_loader 同名测试：主引擎惰性加载与熔断（D5 锚点迁移，自 test_ocr_components 归位）。"""

from __future__ import annotations

import pytest
from src.ocr.recognizer import GeneralRecognizer


def test_recognizer_engine_loads_via_primary_getter(monkeypatch) -> None:
    """主引擎经 engine_loader 的共享 getter 惰性加载，实例内缓存。"""
    sentinel = object()
    monkeypatch.setattr("src.ocr.engine_loader.get_primary_ocr_engine", lambda: sentinel)
    recognizer = GeneralRecognizer(hero_names=["王濬"], page_type="hero_selection")

    assert recognizer.ensure_engine() is sentinel
    assert recognizer.shared_engine() is sentinel


def test_recognizer_engine_fuses_when_primary_unavailable(monkeypatch) -> None:
    """主引擎不可用：报错并熔断，二次访问快速失败不再重试加载。"""
    monkeypatch.setattr("src.ocr.engine_loader.get_primary_ocr_engine", lambda: None)
    recognizer = GeneralRecognizer(hero_names=["王濬"], page_type="hero_selection")

    with pytest.raises(RuntimeError, match="不可用"):
        recognizer.ensure_engine()
    assert recognizer._ocr is False
    with pytest.raises(RuntimeError, match="熔断"):
        recognizer.ensure_engine()
