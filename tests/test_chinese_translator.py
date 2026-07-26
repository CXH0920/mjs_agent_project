"""Qt 标准按钮中文化回归测试。"""

from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
)

from src.ui.chinese_translator import install_chinese_qt_translator


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
