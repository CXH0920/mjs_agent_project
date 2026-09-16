"""
免责声明状态管理

记录用户对免责声明文本版本的接受情况，持久化到 config/.disclaimer_state.json。
仅在免责声明文本版本变化时要求重新确认——跟随应用版本每次发版都弹，
会退化为用户盲点的无效弹窗；故文本本身未修订（TERMS.md 第 9 节版本号不变）
时不打扰用户。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)

# 免责声明文本版本：TERMS.md / LICENSE 附加条款实质修订时递增，驱动启动弹窗重新展示
DISCLAIMER_VERSION = "1.0"

_STATE_FILE = PROJECT_ROOT / "config" / ".disclaimer_state.json"


def should_show(state_file: Path = _STATE_FILE) -> bool:
    """免责声明是否需要展示：从未接受过，或接受的文本版本已过期。

    状态文件缺失、损坏或字段非法时一律按"未接受"处理（记日志后重新弹窗）。
    """
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        accepted_version = str(data["disclaimer_version"])
    except FileNotFoundError:
        return True
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("免责声明状态文件读取失败，按未接受处理: %s", e)
        return True
    return accepted_version != DISCLAIMER_VERSION


def accept(state_file: Path = _STATE_FILE, version: str = DISCLAIMER_VERSION) -> None:
    """记录用户接受免责声明的时间与文本版本。"""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "disclaimer_version": version,
        "accepted_at": datetime.now().isoformat(timespec="seconds"),
    }
    state_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("免责声明已确认: 文本版本 v%s", version)
