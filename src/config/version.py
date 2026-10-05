# -*- coding: utf-8 -*-
"""应用版本单一来源。

VERSION 文件（项目根，frozen 态为 _internal）是版本号的唯一权威：release.py
打包与 zip 命名、启动日志、关于对话框均从此读取。放 config 层是因为 ui/main
都要消费，落在任何消费方一侧都会造成反向依赖或循环导入。
"""
from __future__ import annotations

from functools import lru_cache

from src.config.env import BUNDLE_ROOT, PROJECT_ROOT


@lru_cache(maxsize=1)
def app_version() -> str:
    """解析 VERSION 文件；缺失/不可读时回退开发版本号。"""
    for base in (PROJECT_ROOT, BUNDLE_ROOT):
        candidate = base / "VERSION"
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            continue
    return "0.0.0-dev"
