# -*- coding: utf-8 -*-
"""主窗口菜单栏构建（审计 G1 切片 4.2b）。

从 MainWindow 拆出的纯构建函数：集中创建全部 QAction（快捷键、objectName
与回调接线约定不变，测试经 `_actions` 键集/快捷键/菜单挂载断言），并按
「文件 / 配置 / 数据 / 帮助」四组装配菜单栏。不持有状态，返回动作字典由
窗口保存。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenuBar


def build_actions(
    owner: QMainWindow, callbacks: dict[str, Callable[[], None]]
) -> dict[str, QAction]:
    """集中创建菜单和新应用外壳复用的命令。"""
    actions = {
        "exit": QAction("退出", owner),
        "api_settings": QAction("API 配置", owner),
        "emulator_settings": QAction("模拟器配置", owner),
        "faction_colors": QAction("势力配色", owner),
        "whitelist_config": QAction("白名单配置", owner),
        "data_management": QAction("清空攻略/相性数据", owner),
        "reload": QAction("重新加载数据", owner),
        "official_import": QAction("官方数据导入", owner),
        "fetch_all": QAction("全量获取", owner),
        "fetch_incremental": QAction("增量获取", owner),
        "fetch_specific": QAction("指定获取", owner),
        "guide_all": QAction("全量获取", owner),
        "guide_incremental": QAction("增量获取", owner),
        "guide_specific": QAction("指定获取", owner),
        "synergy_single": QAction("选定武将", owner),
        "synergy_pair": QAction("指定获取", owner),
        "synergy_combos": QAction("实战配队生成", owner),
        "combos_import": QAction("实战配队导入", owner),
        "announcement_check": QAction("检查公告更新", owner),
        "announcement_log": QAction("公告记录", owner),
        "card_sync_check": QAction("检查卡牌百科更新", owner),
        "baike_ignore_manager": QAction("百科忽略名单管理", owner),
        "about": QAction("关于", owner),
    }
    actions["exit"].setShortcut("Ctrl+Q")
    actions["reload"].setShortcut("F5")
    for name, callback in callbacks.items():
        actions[name].setObjectName(f"action_{name}")
        actions[name].triggered.connect(callback)
    return actions


def build_menu_bar(bar: QMenuBar, actions: dict[str, QAction]) -> None:
    """使用共享 QAction 构建菜单栏。"""
    file_menu = bar.addMenu("文件")
    file_menu.addAction(actions["reload"])
    file_menu.addSeparator()
    file_menu.addAction(actions["exit"])

    tools_menu = bar.addMenu("配置")
    tools_menu.addAction(actions["api_settings"])
    tools_menu.addAction(actions["emulator_settings"])
    tools_menu.addAction(actions["faction_colors"])
    tools_menu.addAction(actions["whitelist_config"])

    data_menu = bar.addMenu("数据")
    data_menu.addAction(actions["announcement_check"])
    data_menu.addAction(actions["announcement_log"])
    data_menu.addAction(actions["card_sync_check"])
    data_menu.addAction(actions["baike_ignore_manager"])
    data_menu.addSeparator()
    _add_generation_submenus(data_menu, actions)
    data_menu.addSeparator()
    data_menu.addAction(actions["official_import"])
    data_menu.addAction(actions["combos_import"])
    data_menu.addAction(actions["data_management"])

    help_menu = bar.addMenu("帮助")
    help_menu.addAction(actions["about"])


def _add_generation_submenus(parent_menu, actions: dict[str, QAction]) -> None:
    """挂载武将获取/攻略生成/武将相性三个生成子菜单。"""
    fetch_menu = parent_menu.addMenu("武将获取")
    fetch_menu.addActions([
        actions["fetch_all"],
        actions["fetch_incremental"],
        actions["fetch_specific"],
    ])
    guide_menu = parent_menu.addMenu("攻略生成")
    guide_menu.addActions([
        actions["guide_all"],
        actions["guide_incremental"],
        actions["guide_specific"],
    ])
    synergy_menu = parent_menu.addMenu("武将相性")
    synergy_menu.addActions([
        actions["synergy_single"],
        actions["synergy_pair"],
        actions["synergy_combos"],
    ])
