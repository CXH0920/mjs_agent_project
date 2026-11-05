# -*- coding: utf-8 -*-
"""私有数据仓状态查询（data_repo_status.py）。

承载 pull_data.py 中被 doctor 体检共用的只读查询函数：仓位置解析、
manifest 读取与一致性校验、文件指纹。pull/push 写路径仍留在 CLI 脚本侧
（src/scripts/pull_data.py），读写分离；本模块被 doctor_checks（体检）
与 pull_data（同步 CLI）双向消费，是这些函数的唯一事实源。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from src.config.env import PROJECT_ROOT, load_env_config

DEFAULT_DATA_REPO = PROJECT_ROOT.parent / "mjs_data_private"


def resolve_data_repo() -> Path:
    """私有仓根目录：环境变量 MJS_DATA_REPO > config.env 的 MJS_DATA_REPO > 同级默认。"""
    override = os.environ.get("MJS_DATA_REPO")
    if override:
        return Path(override)
    configured = load_env_config().get("mjs_data_repo")
    if configured:
        return Path(str(configured))
    return DEFAULT_DATA_REPO


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(repo: Path) -> dict:
    """读私有仓 manifest.json；缺失/损坏 sys.exit（CLI 语义，调用方 doctor
    已对缺失做预检转 FAIL finding，不会让 SystemExit 穿透体检汇总）。"""
    manifest = repo / "manifest.json"
    if not manifest.exists():
        sys.exit(f"FAIL: 找不到 {manifest}——先在私有仓运行 scripts/gen_manifest.py")
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        sys.exit(
            f"FAIL: manifest 损坏不可解析: {manifest}\n  {error}\n"
            "  修复：在私有仓重跑 scripts/gen_manifest.py"
        )


def verify_entries(repo: Path) -> tuple[int, str]:
    """校验私有仓内容与 manifest 指纹一致；返回 (退出码, 消息)，0=PASS。

    库层不 print（business 层 T201 纪律）：CLI 输出由 pull_data.verify 壳
    打印，doctor 体检直接消费返回值。
    """
    entries = load_manifest(repo)["files"]
    bad = [f["path"] for f in entries
           if not (repo / f["path"]).exists()
           or file_sha256(repo / f["path"]) != f["sha256"]]
    if bad:
        return 1, "FAIL: " + ", ".join(bad)
    return 0, f"PASS: {len(entries)} 个文件与 manifest 一致"
