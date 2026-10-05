# -*- coding: utf-8 -*-
"""版本单一来源回归：About 对话框与启动日志不得再各自硬编码版本号。"""

from __future__ import annotations

from src.config import version


def test_app_version_reads_version_file(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("1.2.1\n", encoding="utf-8")
    monkeypatch.setattr(version, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(version, "BUNDLE_ROOT", tmp_path)
    version.app_version.cache_clear()

    assert version.app_version() == "1.2.1"


def test_app_version_falls_back_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(version, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(version, "BUNDLE_ROOT", tmp_path)
    version.app_version.cache_clear()

    assert version.app_version() == "0.0.0-dev"


def test_about_dialog_uses_version_source():
    """About 文本是用户唯一可见版本面，必须与 VERSION 文件同源（回归：
    曾硬编码 v0.1.0 与实际 1.2.1 直接矛盾）。"""
    import inspect

    from src.ui.app import dialog_coordinator

    source = inspect.getsource(dialog_coordinator.DialogCoordinator.show_about)
    assert "app_version()" in source
    assert "0.1.0" not in source
