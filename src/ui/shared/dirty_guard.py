"""草稿型对话框的未保存修改丢弃保护（值快照比对 + reject 拦截）。

Esc、取消按钮、标题栏 X 三条退出路径在 QDialog 中最终都进入 reject()，
本 mixin 在此拦截：草稿与装载基线不一致时先经 _confirm_discard() 确认，
用户拒绝则留在对话框。保存成功路径走 accept()（close_after_toast），
不经此拦截，不会误弹。dirty 用值快照而非事件计数：程序装载不误标，
「改了又改回原值」正确判净。
"""

from __future__ import annotations

import copy


class DirtyGuardDialogMixin:
    """配合 QDialog 使用：子类实现 draft_state()，装载完成后调用
    mark_draft_loaded() 记基线。

    基线经 deepcopy 隔离，draft_state() 返回可变结构（嵌套 list/dict）
    也不会与基线共享引用。基线默认值用类属性声明，mixin 不定义
    __init__，避免与 QDialog.__init__ 的参数链冲突。
    """

    _draft_baseline: object = None
    _draft_loaded: bool = False

    def mark_draft_loaded(self) -> None:
        """装载完成后记录基线快照（此后用户修改才会判脏）。"""
        self._draft_baseline = copy.deepcopy(self.draft_state())
        self._draft_loaded = True

    def draft_state(self):
        """当前草稿的可比较快照值（纯值结构，勿含 Qt 对象）；子类必须实现。"""
        raise NotImplementedError("草稿保护对话框必须实现 draft_state()")

    def is_dirty(self) -> bool:
        return self._draft_loaded and self.draft_state() != self._draft_baseline

    def _confirm_discard(self) -> bool:
        """丢弃确认（默认按钮 No：误回车不丢数据）；测试 monkeypatch 本方法。"""
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            "未保存的修改",
            "当前对话框有未保存的修改，确定丢弃并关闭吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def reject(self) -> None:
        if self.is_dirty() and not self._confirm_discard():
            return
        super().reject()
