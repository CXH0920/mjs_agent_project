"""Qt 内置控件的中文翻译。"""

from __future__ import annotations

from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication


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
    ) -> str:
        return _STANDARD_BUTTON_TRANSLATIONS.get(source_text, "")


def install_chinese_qt_translator(app: QApplication) -> ChineseQtTranslator:
    """安装应用级翻译器，使 Qt 自动生成的按钮使用中文。"""
    translator = ChineseQtTranslator(app)
    app.installTranslator(translator)
    return translator
