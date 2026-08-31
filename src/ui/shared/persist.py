"""模态编辑对话框的标准保存循环（收敛自各面板的复制粘贴循环，#E1/#D5）。

统一两件事：
- 重试语义：业务性失败（IO/校验）弹窗后可重试，达上限提示停止；
  非预期异常（编程错误）记录堆栈、不重试——重试无法修复代码缺陷；
- 每次失败都进日志（此前部分面板只弹窗不留痕，违反"禁止忽略异常"约定）。
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import ValidationError
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.ui.shared.widgets import show_toast

logger = logging.getLogger(__name__)


def run_edit_dialog(
    dialog: QDialog,
    persist: Callable[[], None],
    *,
    parent: QWidget,
    success_message: str | None = None,
    failure_hint: str | None = None,
    attempts: int | None = 3,
    on_retry: Callable[[], None] | None = None,
) -> bool:
    """执行"模态编辑 → 确认后保存 → 失败重试"循环，返回是否保存成功。

    Args:
        dialog: 编辑对话框（exec() 返回 Accepted 时尝试 persist）。
        persist: 保存动作（仓储/服务调用）；抛异常视为保存失败。
        parent: 弹窗父对象。
        success_message: 保存成功后的 toast 文案；None 表示由调用方自行反馈。
        failure_hint: 失败弹窗的附加提示（如"编辑内容已保留"，用于失败后
            对话框不重置、草稿可继续编辑的场景）。
        attempts: 最大尝试次数；None 表示不设上限（重试循环维持原行为）。
        on_retry: 每次保存失败后的恢复动作（如 reload_data 对齐界面与磁盘）。
    """
    attempts_made = 0
    while dialog.exec() == QDialog.DialogCode.Accepted:
        attempts_made += 1
        try:
            persist()
        except (OSError, ValueError, ValidationError) as error:
            logger.warning("保存失败（可重试）: %s", error)
            message = str(error) if not failure_hint else f"{error}\n\n{failure_hint}"
            QMessageBox.critical(parent, "保存失败", message)
            if on_retry is not None:
                on_retry()
            if attempts is not None and attempts_made >= attempts:
                QMessageBox.warning(
                    parent, "已停止重试",
                    "连续保存失败，已停止重试，请检查文件权限/磁盘后重试。")
                return False
            continue
        except Exception as error:
            # 编程错误重试无意义：留完整堆栈后直接退出循环
            logger.exception("保存失败（非预期异常）")
            QMessageBox.critical(parent, "保存失败", f"发生非预期错误：{error}")
            return False
        if success_message:
            show_toast(parent, success_message)
        return True
    return False
