"""
首启向导状态管理

记录首启引导（环境自检 / 模拟器连接 / 试识别）的完成与跳过情况，持久化到
config/.onboarding_state.json。范式同 disclaimer_state：文件缺失、损坏或
onboarding_version 不符一律按"未完成"重新弹出；跳过后不再自动打扰。

onboarding_version 升版意味着引导内容实质变化，所有老用户（含已跳过的）
都会重新看到向导——这是有意的产品决策，升版须在发版说明中写明。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)

# 引导内容实质变化时递增，驱动首启向导重新展示
ONBOARDING_VERSION = "1"

# steps 的合法键：段 1 环境自检 / 段 2 模拟器连接 / 段 3 试识别
STEP_KEYS = ("env", "device", "recognize")

_STATE_FILE = PROJECT_ROOT / "config" / ".onboarding_state.json"


def should_show_state(state_file: Path = _STATE_FILE) -> bool:
    """首启向导是否需要展示：从未完成，或记录的引导版本已过期。

    状态文件缺失、损坏或字段非法时一律按"未完成"处理（记日志后重新弹出），
    与 disclaimer_state 的容错口径一致。
    """
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        recorded = str(data["onboarding_version"])
    except FileNotFoundError:
        return True
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("首启向导状态文件读取失败，按未完成处理: %s", e)
        return True
    return recorded != ONBOARDING_VERSION


def _write(state_file: Path, payload: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _read(state_file: Path) -> dict:
    """读现有状态；不可用时返回全新状态（不覆盖，由下一次写入重建）。"""
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and str(data.get("onboarding_version")) == ONBOARDING_VERSION:
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as e:
        logger.warning("首启向导状态文件读取失败，按未完成处理: %s", e)
    return {"onboarding_version": ONBOARDING_VERSION, "steps": {}, "skipped": False}


def mark_step(step: str, state_file: Path = _STATE_FILE) -> None:
    """记录单个引导步骤的完成时间（step ∈ STEP_KEYS）。"""
    if step not in STEP_KEYS:
        raise ValueError(f"未知的引导步骤: {step}")
    data = _read(state_file)
    data.setdefault("steps", {})[step] = datetime.now().isoformat(timespec="seconds")
    data["updated_at"] = data["steps"][step]
    _write(state_file, data)
    logger.info("首启向导步骤完成: %s", step)


def mark_skipped(state_file: Path = _STATE_FILE) -> None:
    """记录跳过引导，此后不再自动弹出（直至版本升版或 reset）。"""
    data = _read(state_file)
    data["skipped"] = True
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write(state_file, data)
    logger.info("首启向导已跳过")


def reset(state_file: Path = _STATE_FILE) -> None:
    """清除状态文件，供「帮助 → 重看首启引导」与自测复现使用。"""
    try:
        state_file.unlink()
    except FileNotFoundError:
        return
    except OSError as e:
        logger.warning("首启向导状态文件删除失败: %s", e)
