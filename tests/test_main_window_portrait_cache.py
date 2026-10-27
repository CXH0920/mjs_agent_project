# -*- coding: utf-8 -*-
"""采集完成信号与头像缓存失效的联动锚（批次11 审查 P1 修复）。

曾发生的缺陷：shared.portrait.load_portrait 的 LRU 缓存把「查无头像」的
None 结果与旧 QPixmap 一并缓存，应用内「全量/增量/指定获取」采集下载
头像后，match 面板命中旧缓存继续显示占位/旧头像，须重启应用才恢复。
修复：MainWindow 在采集完成信号上挂 load_portrait.cache_clear()。
"""

from __future__ import annotations

import base64
from pathlib import Path

from src.business.fetching.hero_fetch_service import HeroFetchService
from src.ui.app.main_window import MainWindow
from src.ui.app.progress_reporter import ProgressReporter
from src.ui.shared import portrait

# 1x1 透明 PNG：头像加载行为测试的真实可解码图片（同 test_runtime_root_config_and_portrait）
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _patch_portrait_roots(tmp_path: Path, monkeypatch) -> Path:
    """头像两级根重定向到 tmp，返回可写根（模拟 crawler 下载落点）。"""
    runtime = tmp_path / "runtime_images"
    runtime.mkdir()
    monkeypatch.setattr(portrait, "IMAGES_DIR", tmp_path / "bundle_images", raising=False)
    monkeypatch.setattr(portrait, "IMAGES_OUTPUT_DIR", runtime, raising=False)
    portrait.load_portrait.cache_clear()
    return runtime


def _window_with_fetch_service() -> MainWindow:
    """仅装配 _connect_fetch_signals 依赖的两个属性（不构造完整主窗口）。"""
    window = MainWindow.__new__(MainWindow)
    window._fetch_service = HeroFetchService()
    window._reporter = ProgressReporter()
    window._connect_fetch_signals()
    return window


def test_fetch_completed_clears_portrait_cache(tmp_path: Path, monkeypatch, qapp) -> None:
    """采集完成信号必须清空头像缓存：缓存过 None 的新头像要能立即加载。"""
    runtime = _patch_portrait_roots(tmp_path, monkeypatch)

    # 复现：查询时头像文件尚未落盘 → None 被缓存
    assert portrait.load_portrait("新武将", 96, 129) is None
    # 采集子进程下载头像落盘（直接写文件模拟）
    (runtime / "新武将.png").write_bytes(_ONE_PIXEL_PNG)

    window = _window_with_fetch_service()
    window._fetch_service.fetch_completed.emit(True)

    # 修复后：缓存已被完成信号清空，新头像本次进程内立即可加载
    assert portrait.load_portrait("新武将", 96, 129) is not None

    portrait.load_portrait.cache_clear()


def test_fetch_failed_also_clears_portrait_cache(tmp_path: Path, monkeypatch, qapp) -> None:
    """失败批次同样清缓存：部分下载成功的头像不应被失败前查询的 None 压住。"""
    runtime = _patch_portrait_roots(tmp_path, monkeypatch)

    assert portrait.load_portrait("新武将", 96, 129) is None
    (runtime / "新武将.png").write_bytes(_ONE_PIXEL_PNG)

    window = _window_with_fetch_service()
    window._fetch_service.fetch_completed.emit(False)

    assert portrait.load_portrait("新武将", 96, 129) is not None

    portrait.load_portrait.cache_clear()
