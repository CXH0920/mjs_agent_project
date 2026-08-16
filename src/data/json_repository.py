# -*- coding: utf-8 -*-
"""知识库维护 JSON 仓库公共基类与原子写工具。

统一四个维护仓库（专属牌/武将分类/装备属性/卡牌点数）的：
- 原子写盘（mkstemp + fsync + replace，失败清理临时文件，写读互斥锁）；
- load 读取骨架（状态重置、异常分级、根结构校验由子类负责）；
- 写盘失败内存回滚（_snapshot/_restore/_save_or_rollback）。

此前各仓库各持一份 _atomic_json_write / _issue 副本（全库最多 7 份），
后续写盘改进只需改本文件。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from src.data.manager import DataIssue

logger = logging.getLogger(__name__)


def atomic_write_json(path: Path | str, data: Any, indent: int = 2) -> None:
    """以 UTF-8、LF、同目录临时文件原子保存 JSON。

    - mkstemp 保证临时文件唯一且不覆盖既有文件；
    - 写入后 flush + fsync 再 replace，避免断电/崩溃留下空或半截文件；
    - 任一异常清理临时文件后重新抛出（原文件保持不变）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=indent)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except Exception:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise


class JsonRepository:
    """JSON 文件可写仓库基类。

    子类职责：
    - __init__ 中先 super().__init__(file_path)，再初始化自身数据字段；
    - load() 用 _read_root() 取 (root, ok)，随后做根结构校验与逐条解析；
    - save() 构造 payload 后调 save_payload(payload)；
    - 需要"先改内存后写盘、失败回滚"的 CRUD 用 _snapshot/_restore/_save_or_rollback。
    """

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)
        self.load_issues: list[DataIssue] = []
        self.available = False
        self._lock = threading.RLock()

    # ---------------------------------------------------------------
    # 问题收集与日志
    # ---------------------------------------------------------------
    def _issue(self, severity: str, kind: str, message: str, index: int | None = None,
               key: object | None = None) -> None:
        self.load_issues.append(DataIssue(severity, kind, self.file_path, message, index, key))
        issue_logger = logging.getLogger(self.__class__.__module__)
        (issue_logger.warning if severity == "warning" else issue_logger.error)(
            "%s 数据问题 [%s] %s", self.__class__.__name__, kind, message)

    # ---------------------------------------------------------------
    # 读盘骨架（加锁，防止与写盘并发）
    # ---------------------------------------------------------------
    def _read_root(self) -> tuple[Any | None, bool]:
        """重置加载状态并读取 JSON；返回 (root, 是否成功)。

        文件缺失 → warning（返回 False）；读取/解析失败 → error（返回 False）。
        """
        with self._lock:
            self.load_issues = []
            self.available = False
            try:
                with self.file_path.open("r", encoding="utf-8") as stream:
                    return json.load(stream), True
            except FileNotFoundError:
                self._issue("warning", "file_missing", "文件不存在")
                return None, False
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self._issue("error", "file_read_error", str(error))
                return None, False

    # ---------------------------------------------------------------
    # 写盘（加锁 + 原子写）
    # ---------------------------------------------------------------
    def save_payload(self, payload: Any, indent: int = 2) -> None:
        with self._lock:
            atomic_write_json(self.file_path, payload, indent=indent)

    # ---------------------------------------------------------------
    # 写盘失败回滚（子类实现快照语义）
    # ---------------------------------------------------------------
    def _snapshot(self) -> Any:
        """写前内存快照；子类按自身数据字段实现。"""
        raise NotImplementedError

    def _restore(self, snapshot: Any) -> None:
        """写盘失败时恢复内存到快照；子类按自身数据字段实现。"""
        raise NotImplementedError

    def _save_or_rollback(self, snapshot: Any) -> None:
        """保存；失败时恢复内存快照并重新抛出（避免"看似失败、实际已变"的脏状态）。"""
        try:
            self.save()
        except Exception:
            self._restore(snapshot)
            raise
