"""Qt 内置控件的中文翻译。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTranslator
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

_STANDARD_BUTTON_TRANSLATIONS = {
    "OK": "确定",
    "Open": "打开",
    "Save": "保存",
    "Save All": "全部保存",
    "Cancel": "取消",
    "Close": "关闭",
    "Discard": "放弃",
    "Apply": "应用",
    "Reset": "重置",
    "Restore Defaults": "恢复默认",
    "Help": "帮助",
    "Yes": "是",
    "No": "否",
    "Abort": "中止",
    "Retry": "重试",
    "Ignore": "忽略",
    "Don't Save": "不保存",
    "&OK": "确定",
    "&Open": "打开",
    "&Save": "保存",
    "Save &All": "全部保存",
    "&Cancel": "取消",
    "&Close": "关闭",
    "&Discard": "放弃",
    "&Apply": "应用",
    "&Reset": "重置",
    "Restore &Defaults": "恢复默认",
    "&Help": "帮助",
    "&Yes": "是",
    "&No": "否",
    "&Abort": "中止",
    "&Retry": "重试",
    "&Ignore": "忽略",
    "&Don't Save": "不保存",
    "Show Details...": "查看详情",
    "Hide Details": "隐藏详情",
    "Add to Custom Colors": "添加到自定义颜色",
    "Pick Screen Color": "屏幕取色",
}


class ChineseQtTranslator(QTranslator):
    """为 Qt 未提供中文翻译资源的标准控件补充常用中文文案。"""

    def translate(
        self,
        context: str,
        source_text: str,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str | None:
        return _STANDARD_BUTTON_TRANSLATIONS.get(source_text)


def install_chinese_qt_translator(app: QApplication) -> ChineseQtTranslator:
    """安装应用级翻译器，使 Qt 自动生成的按钮使用中文。"""
    translator = ChineseQtTranslator(app)
    app.installTranslator(translator)
    return translator


class _DetailsButtonFilter(QObject):
    """翻译 QMessageBox 详情按钮文字。

    Qt 内部点击展开/收起时直接 setText 不走 QTranslator，需在 layout 变化时
    遍历子按钮翻译，避免初始汉化后展开变回英文 "Hide Details"。
    """

    _MAP = {
        "Show Details...": "查看详情",
        "Hide Details": "隐藏详情",
    }

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.LayoutRequest, QEvent.Type.Resize):
            if isinstance(obj, QMessageBox):
                for btn in obj.findChildren(QPushButton):
                    t = btn.text()
                    if t in self._MAP:
                        btn.setText(self._MAP[t])
        return False


def install_details_button_translator(msgbox: QMessageBox) -> _DetailsButtonFilter:
    """为 QMessageBox 安装详情按钮翻译过滤器（随对话框销毁）。"""
    filt = _DetailsButtonFilter(msgbox)
    msgbox.installEventFilter(filt)
    return filt
