# -*- coding: utf-8 -*-
"""RAG 语料构建公共工具（rag_common.py，#63/#64/#66）。

统一 8 个 build 脚本的：
- stdout UTF-8 包装（setup_stdout，消除三种写法）；
- 路径基准（ROOT 基于 __file__，消除"必须 cwd=项目根"的相对路径）；
- JSON 读取（load_json：UTF-8-SIG 容错 + 缺失/解析失败统一告警或退出）；
- JSON 写入（save_json：原子写，复用 src.data.json_repository.atomic_write_json）。
"""
from __future__ import annotations

import io
import json
import logging
import re
import sys
from pathlib import Path

from src.config.env import PROJECT_ROOT as ROOT
DATA = ROOT / "data"
CORPUS = ROOT / "data" / "rag_corpus"

# ---------------------------------------------------------------------------
# 脚本日志规范与助手
# ---------------------------------------------------------------------------
# stdout（print）是 QProcess 进度契约：只允许协议行（[i/N] xxx START/OK/FAIL、
# ✅/⚠️/❌ 状态行等）与面向用户的最终汇总，格式变更必须同步解析方
# （fetch 服务 / 进度窗 / tests/test_fetch_utils.py 协议锁）。
# 诊断信息（堆栈、中间值、静默恢复详情）一律走 get_script_logger 返回的
# logger：DEBUG+ 写入 logs/rag/<script>.log，WARNING+ 镜像到 stderr
# （ScriptRunner 会并入维护面板输出）——不占用 stdout 协议通道。


def get_script_logger(script_name: str) -> logging.Logger:
    """返回脚本专用 logger：文件记录 DEBUG+，stderr 镜像 WARNING+。"""
    logger = logging.getLogger(f"rag_script.{script_name}")
    if logger.handlers:
        return logger
    log_dir = ROOT / "logs" / "rag"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / f"{script_name}.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger

# 元规则文档结构解析正则：build_rule_corpus / sync_rule_stats / apply_rule_proposal
# 三个脚本共用同一份口径（此前三处逐字复制，一处改动即产生解析口径漂移）
HEADING_RE = re.compile(r'^(#{2,3})\s+(.*)$')
SEPARATOR_RE = re.compile(r'^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|$')


def setup_stdout() -> None:
    """统一 stdout UTF-8 包装（幂等）。"""
    encoding = getattr(sys.stdout, "encoding", "") or ""
    if encoding.lower() in ("utf-8", "utf8"):
        return
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def load_json(path, required: bool = True, label: str | None = None):
    """读取 JSON（UTF-8 / UTF-8-SIG 容错，防止 Excel/记事本存出的 BOM 导致解析失败）。

    - required=True（构建必需的源数据）：缺失/解析失败打印错误并退出码 1；
    - required=False（可选输入，如旧语料）：失败打印告警并返回 None。
    """
    path = Path(path)
    label = label or path.name
    if not path.exists():
        if required:
            print(f"❌ 缺少源数据 {path}（{label}）")
            sys.exit(1)
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if required:
            print(f"❌ 源数据解析失败 {path}（{label}）：{exc}")
            sys.exit(1)
        print(f"⚠️ 跳过可选输入 {path}（{label}）：{exc}")
        return None


def save_json(path, data, indent: int = 1) -> None:
    """原子写 JSON（UTF-8、LF、indent=1 与既有语料一致）。"""
    path = Path(path)
    from src.data.json_repository import atomic_write_json  # noqa: PLC0415

    atomic_write_json(path, data, indent=indent)


def project_path(*parts: str) -> Path:
    """项目根下的路径（相对项目根拼接）。"""
    return ROOT.joinpath(*parts)
