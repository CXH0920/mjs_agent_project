"""原子写盘工具（mkstemp + fsync + replace）。

放 config 层（项目最底层）：config 自身的 env/profiles/状态文件写入与
data 层 json_repository 都要消费，落在任何消费方一侧都会造成反向依赖
（定位同 version.py）。除 stdlib 外禁止 import 任何项目模块——
test_architecture 的 config 底层断言守护本约束。
函数体自 src/data/json_repository.py 原样搬移，行为不变。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path | str, data: Any, indent: int = 2,
                      sort_keys: bool = False) -> None:
    """以 UTF-8、LF、同目录临时文件原子保存 JSON。

    - mkstemp 保证临时文件唯一且不覆盖既有文件（并发生成与人工编辑
      写同一路径时不再互相踩同名 .tmp）；
    - 写入后 flush + fsync 再 replace，避免断电/崩溃留下空或半截文件；
    - 任一异常清理临时文件后重新抛出（原文件保持不变）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
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


def atomic_write_text(path: Path | str, content: str) -> None:
    """以 UTF-8、LF、同目录唯一临时文件原子保存文本（与 atomic_write_json 同骨架）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except Exception:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise
