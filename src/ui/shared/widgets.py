"""跨页面复用的基础 Qt 控件。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.shared.style import (
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    set_tone,
    set_ui_role,
)


def clear_layout(layout: QLayout) -> None:
    """递归清空布局：销毁直接控件与子布局中的控件（含 CheckableComboBox 弹层）。

    原多个面板各自维护一份 _clear_layout 副本（#45），统一收敛到此处。
    """
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            inner = widget.layout()
            if inner is not None:
                clear_layout(inner)
            close_popup = getattr(widget, "closePopup", None)
            if callable(close_popup):
                close_popup()
            widget.setParent(None)
            widget.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            clear_layout(sub)


class ScriptRunner(QObject):
    """QProcess 异步执行 Python 脚本的公共封装（#43）。

    - 同一时刻只允许一个任务（is_running 检查，避免并发 QProcess）；
    - stdout/stderr 通过 output 信号逐段发出（bytes，调用方自行解码）；
    - 进程结束后发出 finished(code)。
    """

    output = Signal(bytes)
    finished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def run(self, python: str, script: Path, args: list[str], working_dir: Path) -> bool:
        """启动脚本；已有任务运行时返回 False。"""
        if self.is_running():
            return False
        proc = QProcess(self)
        proc.setWorkingDirectory(str(working_dir))
        proc.readyReadStandardOutput.connect(lambda: self.output.emit(proc.readAllStandardOutput()))
        proc.readyReadStandardError.connect(lambda: self.output.emit(proc.readAllStandardError()))
        proc.finished.connect(lambda code, _status: self._on_finished(code))
        proc.start(python, ([str(script)] if script else []) + args)
        self._proc = proc
        return True

    def _on_finished(self, code: int) -> None:
        self._proc = None
        self.finished.emit(code)


class DoubleClickLabel(QLabel):
    """支持发出左键双击信号的标签。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class FlowLayout(QLayout):
    """按控件实际宽度自动换行的轻量布局。"""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        content = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = content.x()
        y = content.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            size = item.sizeHint()
            next_x = x + size.width()
            if line_height and next_x > content.right() + 1:
                x = content.x()
                y += line_height + spacing
                next_x = x + size.width()
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x = next_x + spacing
            line_height = max(line_height, size.height())

        return y + line_height - rect.y() + margins.bottom()


class PageHeader(QWidget):
    """统一承载页面标题、状态说明和页面级操作。"""

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_MD)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageHeaderTitle")
        text_layout.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("pageHeaderSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        text_layout.addWidget(self.subtitle_label)
        layout.addLayout(text_layout, 1)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(SPACE_SM)
        layout.addLayout(self.actions_layout)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))

    def add_action(self, button: QAbstractButton, role: str = ROLE_SECONDARY) -> None:
        set_ui_role(button, role)
        self.actions_layout.addWidget(button)


class PageActionBar(QWidget):
    """页面内容区的状态与操作行，不重复展示外壳标题。"""

    def __init__(self, status: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pageActionBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_MD)

        self.status_label = QLabel(status)
        self.status_label.setObjectName("pageActionStatus")
        self.status_label.setWordWrap(True)
        set_tone(self.status_label, TONE_NEUTRAL)
        layout.addWidget(self.status_label, 1)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(SPACE_SM)
        layout.addLayout(self.actions_layout)

    def set_status(self, status: str, tone: str = TONE_NEUTRAL) -> None:
        self.status_label.setText(status)
        set_tone(self.status_label, tone)

    def add_action(self, button: QAbstractButton, role: str = ROLE_SECONDARY) -> None:
        set_ui_role(button, role)
        self.actions_layout.addWidget(button)


class EmptyState(QWidget):
    """统一的无数据、未执行和无搜索结果状态。"""

    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_SM)
        # 不设布局级对齐：对齐布局按窄 sizeHint 放置子控件，wordWrap 描述的
        # heightForWidth 不参与分配会导致换行文字被裁剪；居中由标签自身对齐承担

        self.title_label = QLabel(title)
        self.title_label.setObjectName("emptyStateTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.description_label = QLabel(description)
        self.description_label.setObjectName("emptyStateDescription")
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description_label.setWordWrap(True)
        self.description_label.setVisible(bool(description))
        layout.addWidget(self.description_label)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, SPACE_SM, 0, 0)
        self.actions_layout.setSpacing(SPACE_SM)
        self.actions_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(self.actions_layout)

    def set_description(self, description: str) -> None:
        self.description_label.setText(description)
        self.description_label.setVisible(bool(description))

    def add_action(self, button: QAbstractButton, role: str = ROLE_SECONDARY) -> None:
        set_ui_role(button, role)
        self.actions_layout.addWidget(button)


class StatusBadge(QLabel):
    """只用于展示短状态的语义标签。"""

    def __init__(self, text: str = "", tone: str = TONE_NEUTRAL, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        set_tone(self, tone)


class NoticeBanner(QFrame):
    """页内通知条；用于可恢复或需要用户注意的状态。"""

    def __init__(
        self,
        title: str,
        message: str = "",
        tone: str = TONE_INFO,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("noticeBanner")
        set_tone(self, tone)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_MD, SPACE_SM)
        layout.setSpacing(SPACE_MD)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("noticeBannerTitle")
        text_layout.addWidget(self.title_label)
        self.message_label = QLabel(message)
        self.message_label.setObjectName("noticeBannerMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(bool(message))
        text_layout.addWidget(self.message_label)
        layout.addLayout(text_layout, 1)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(SPACE_SM)
        layout.addLayout(self.actions_layout)

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)
        self.message_label.setVisible(bool(message))

    def set_tone(self, tone: str) -> None:
        set_tone(self, tone)

    def add_action(self, button: QAbstractButton, role: str = ROLE_SECONDARY) -> None:
        set_ui_role(button, role)
        self.actions_layout.addWidget(button)


class DialogFooter(QFrame):
    """固定顺序的标准弹窗底栏。"""

    accepted = Signal()
    rejected = Signal()

    def __init__(
        self,
        accept_text: str = "保存",
        cancel_text: str = "取消",
        accept_role: str = ROLE_PRIMARY,
        show_cancel: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("dialogFooter")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        layout.setSpacing(SPACE_SM)
        layout.addStretch()

        self.cancel_button = QPushButton(cancel_text)
        set_ui_role(self.cancel_button, ROLE_SECONDARY)
        self.cancel_button.setVisible(show_cancel)
        self.cancel_button.clicked.connect(self.rejected.emit)
        layout.addWidget(self.cancel_button)

        self.accept_button = QPushButton(accept_text)
        set_ui_role(self.accept_button, accept_role)
        self.accept_button.clicked.connect(self.accepted.emit)
        layout.addWidget(self.accept_button)

        self._accept_text = accept_text
        self._busy = False
        self._accept_was_enabled = True
        self._cancel_was_enabled = True

    def set_busy(self, busy: bool, text: str = "处理中...") -> None:
        """切换提交状态，并禁用底栏中的重复确认和取消操作。"""
        if self._busy == busy:
            return
        self._busy = busy
        if busy:
            self._accept_was_enabled = self.accept_button.isEnabled()
            self._cancel_was_enabled = self.cancel_button.isEnabled()
            self.accept_button.setText(text)
            self.accept_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            return
        self.accept_button.setText(self._accept_text)
        self.accept_button.setEnabled(self._accept_was_enabled)
        self.cancel_button.setEnabled(self._cancel_was_enabled)


class ToastOverlay(QLabel):
    """附着在父窗口上的非阻断短消息。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toastOverlay")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    def show_message(
        self,
        message: str,
        tone: str = TONE_SUCCESS,
        duration: int = 1800,
    ) -> None:
        self.setText(message)
        set_tone(self, tone)
        parent = self.parentWidget()
        if parent is None:
            return
        self.setMaximumWidth(max(220, parent.width() - SPACE_XL * 2))
        self.adjustSize()
        self.move(
            max(SPACE_LG, (parent.width() - self.width()) // 2),
            max(SPACE_LG, parent.height() - self.height() - SPACE_XL),
        )
        self.raise_()
        self.show()
        self._hide_timer.start(duration)


def show_toast(
    parent: QWidget,
    message: str,
    tone: str = TONE_SUCCESS,
    duration: int = 1800,
) -> ToastOverlay:
    """复用父窗口上的 Toast，避免重复创建临时反馈控件。"""
    toast = getattr(parent, "_shared_toast_overlay", None)
    if toast is None:
        toast = ToastOverlay(parent)
        setattr(parent, "_shared_toast_overlay", toast)
    toast.show_message(message, tone, duration)
    return toast
