"""Qt 标准按钮中文化回归测试。"""

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
)

from src.ui.app.chinese_translator import install_chinese_qt_translator


def test_standard_dialog_buttons_use_chinese() -> None:
    app = QApplication.instance() or QApplication([])
    translator = install_chinese_qt_translator(app)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )

    assert buttons.button(QDialogButtonBox.StandardButton.Save).text() == "保存"
    assert buttons.button(QDialogButtonBox.StandardButton.Cancel).text() == "取消"
    message_box = QMessageBox()
    message_box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )

    assert message_box.button(QMessageBox.StandardButton.Yes).text() == "是"
    assert message_box.button(QMessageBox.StandardButton.No).text() == "否"
    file_dialog = QFileDialog()
    file_dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    file_dialog.show()
    app.processEvents()

    assert {"打开", "取消"}.issubset(
        {button.text() for button in file_dialog.findChildren(QPushButton)}
    )
    file_dialog.close()
    assert translator.translate("QDialogButtonBox", "Close") == "关闭"


def test_data_menu_shortcut_does_not_emit_qstring_arg_warning() -> None:
    app = QApplication.instance() or QApplication([])
    translator = install_chinese_qt_translator(app)
    messages = []
    previous_handler = qInstallMessageHandler(
        lambda mode, context, message: messages.append(message)
    )
    window = QMainWindow()
    try:
        data_menu = window.menuBar().addMenu("数据")
        reload_action = QAction("重新加载数据", window)
        reload_action.setShortcut("F5")
        data_menu.addAction(reload_action)
        window.show()
        app.processEvents()
        data_menu.show()
        app.processEvents()
    finally:
        window.close()
        qInstallMessageHandler(previous_handler)

    assert translator.translate("QPlatformTheme", "未匹配文本") is None
    assert not any("QString::arg: Argument missing" in message for message in messages)
