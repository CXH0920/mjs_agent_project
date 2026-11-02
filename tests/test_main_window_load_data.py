# -*- coding: utf-8 -*-
"""MainWindow._load_data 缺失引用弹窗链锚（批次6步骤0：构造期唯一模态风险点）。

该弹窗链是"传小数据构造 MainWindow 会卡死"的根源（J3），也是组合根改造的
护栏：拆分后"失效关联 → 确认 → 修复落盘 → 完成提示"的行为必须逐项不变。
第一轮 P2-3 起，窗口未显示（启动画面仍置顶）时弹窗挂起、显示后补弹——
本文件同时锚定"已显示立即弹（F5 重载路径）"与"未显示挂起 + 补弹"两侧。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.data.facade import DataFacade
from src.data.models import Hero, HeroGuide, SynergyScore
from src.ui.app import main_window as main_window_module
from src.ui.app.main_window import MainWindow


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _corpus_with_broken_reference(tmp_path: Path) -> DataFacade:
    """一次造全三种失效：相性引用缺失、攻略归属缺失、攻略关联缺失。

    注意 guide 以 hero_id 为主键，合法条目用 hero_id=2 以免与清理条目撞键。
    """
    heroes = tmp_path / "heroes.json"
    synergies = tmp_path / "synergies.json"
    guides = tmp_path / "guides.json"
    _write(heroes, [
        Hero(id=1, name="甲").model_dump(mode="json"),
        Hero(id=2, name="乙").model_dump(mode="json"),
    ])
    _write(synergies, [
        SynergyScore(hero_a_id=1, hero_b_id=2, score=6).model_dump(mode="json"),
        SynergyScore(hero_a_id=1, hero_b_id=99, score=6).model_dump(mode="json"),
    ])
    _write(guides, [
        HeroGuide(hero_id=2, weak_against_type=["高爆发型"], synergizes_with=[1]).model_dump(mode="json"),
        HeroGuide(hero_id=888, weak_against_type=["慢速防御型"], synergizes_with=[]).model_dump(mode="json"),
        HeroGuide(hero_id=1, weak_against_type=["控制型"], synergizes_with=[2, 777]).model_dump(mode="json"),
    ])
    return DataFacade(heroes, synergies, guides)


def _window_with(facade: DataFacade, visible: bool = True) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    window._data = facade
    # _load_data 的失败弹窗/标记已外移 DialogCoordinator（首启向导 T10），注入真实例
    from src.ui.app.dialog_coordinator import DialogCoordinator

    window._dialogs = DialogCoordinator(window)
    # 骨架无 C++ 对象，isVisible 需显式注入：True = 已显示窗口（F5 重载路径，
    # 弹窗立即弹）；False = 构造期（启动画面期间，弹窗挂起等补弹）
    window.isVisible = lambda: visible
    return window


def _patch_boxes(monkeypatch, answer) -> dict[str, list[str]]:
    """记录 question/warning/information 三级弹窗（标题+正文拼接）；question 固定返回 answer。"""
    recorded: dict[str, list[str]] = {"question": [], "warning": [], "information": []}
    mb = main_window_module.QMessageBox

    def _text(args) -> str:
        return " ".join(str(a) for a in args[1:3])  # 标题 + 正文

    def fake_question(*args, **kwargs):
        recorded["question"].append(_text(args))
        return answer

    def fake_warning(*args, **kwargs):
        recorded["warning"].append(_text(args))

    def fake_information(*args, **kwargs):
        recorded["information"].append(_text(args))

    monkeypatch.setattr(mb, "question", fake_question)
    monkeypatch.setattr(mb, "warning", fake_warning)
    monkeypatch.setattr(mb, "information", fake_information)
    return recorded


def test_load_data_repairs_missing_references_on_confirm(tmp_path: Path, monkeypatch) -> None:
    """确认修复：三条失效（相性/攻略归属/攻略关联）一次修复并落盘，弹完成提示。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)

    window._load_data()

    assert any("发现数据关联问题" in text for text in recorded["question"])
    assert recorded["information"], "修复完成应弹出提示"
    info_text = recorded["information"][0]
    # 三条 repair 分支的分类计数全部命中（DataRepairResult 三个字段各有锚）
    assert "删除相性 1 条" in info_text
    assert "删除攻略 1 条" in info_text
    assert "清理攻略关联 1 项" in info_text

    data = json.loads((tmp_path / "synergies.json").read_text(encoding="utf-8"))
    assert [(item["hero_a_id"], item["hero_b_id"]) for item in data] == [(1, 2)]  # 失效相性已删
    guides = json.loads((tmp_path / "guides.json").read_text(encoding="utf-8"))
    by_hero = {item["hero_id"]: item for item in guides}
    assert 888 not in by_hero  # 归属失效的攻略整条删除
    assert by_hero[1]["synergizes_with"] == [2]  # 失效关联被清理
    assert by_hero[2]["synergizes_with"] == [1]  # 合法攻略未动

    backups = list((tmp_path / "backups").glob("*"))
    assert len(backups) == 2  # 事务备份：guides + synergies 各一份


def test_load_data_decline_keeps_files_untouched(tmp_path: Path, monkeypatch) -> None:
    """拒绝修复：仅提示"数据未修改"，不写盘、无完成提示、无备份。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.No)

    window._load_data()

    assert any("数据未修改" in text for text in recorded["warning"])
    assert recorded["information"] == []
    data = json.loads((tmp_path / "synergies.json").read_text(encoding="utf-8"))
    assert [item["hero_b_id"] for item in data] == [2, 99]  # 原样保留
    guides = json.loads((tmp_path / "guides.json").read_text(encoding="utf-8"))
    assert sorted(item["hero_id"] for item in guides) == [1, 2, 888]  # 三条原样保留
    assert not (tmp_path / "backups").exists()  # 未创建事务备份


def test_load_data_with_consistent_corpus_shows_no_dialog(tmp_path: Path, monkeypatch) -> None:
    """一致语料零弹窗——"tmp 一致小数据构造不卡死"的不变量（J3 依赖）。"""
    heroes = tmp_path / "heroes.json"
    synergies = tmp_path / "synergies.json"
    guides = tmp_path / "guides.json"
    _write(heroes, [
        Hero(id=1, name="甲").model_dump(mode="json"),
        Hero(id=2, name="乙").model_dump(mode="json"),
    ])
    _write(synergies, [
        SynergyScore(hero_a_id=1, hero_b_id=2, score=6).model_dump(mode="json"),
    ])
    _write(guides, [
        HeroGuide(hero_id=1, weak_against_type=["高爆发型"], synergizes_with=[2]).model_dump(mode="json"),
    ])
    window = _window_with(DataFacade(heroes, synergies, guides))
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.No)

    window._load_data()

    assert recorded == {"question": [], "warning": [], "information": []}


def test_load_data_failure_shows_warning(tmp_path: Path, monkeypatch) -> None:
    """加载抛异常时（窗口已显示）弹"数据加载失败"告警而非崩溃。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        facade, "load_all",
        lambda: (_ for _ in ()).throw(RuntimeError("磁盘错误")),
    )

    window._load_data()

    assert any("数据加载失败" in text and "磁盘错误" in text for text in recorded["warning"])
    assert window._dialogs._data_error == "磁盘错误"  # 失败标记留痕（首启向导段 1 读）


# ---------------------------------------------------------------------------
# 第一轮 P2-3：窗口未显示（启动画面期间）弹窗挂起，显示后补弹
# ---------------------------------------------------------------------------

def test_load_data_defers_prompts_while_not_visible(tmp_path: Path, monkeypatch) -> None:
    """构造期（未显示）发现失效关联：不弹任何模态框，挂起等窗口显示。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade, visible=False)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)

    window._load_data()

    assert recorded == {"question": [], "warning": [], "information": []}
    assert window._dialogs._pending_missing_references, "失效关联应挂起到 _dialogs 待补弹"


def test_load_data_failure_defers_warning_while_not_visible(
    tmp_path: Path, monkeypatch,
) -> None:
    """构造期加载失败：标记立即留痕（向导段 1 三态依赖），告警挂起不弹。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade, visible=False)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        facade, "load_all",
        lambda: (_ for _ in ()).throw(RuntimeError("磁盘错误")),
    )

    window._load_data()

    assert recorded["warning"] == []
    assert window._dialogs._data_error == "磁盘错误"  # 标记不受挂起影响
    assert window._dialogs._load_failed_warning_pending


def test_flush_pending_prompts_after_show(tmp_path: Path, monkeypatch) -> None:
    """窗口显示后补弹：挂起的失效关联经 flush 走完整修复链，且幂等不重弹。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade, visible=False)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)

    window._load_data()
    assert recorded["question"] == []

    window._flush_pending_data_prompts()  # showEvent → singleShot(0) 的回调体

    assert any("发现数据关联问题" in text for text in recorded["question"])
    assert recorded["information"], "确认修复后应弹完成提示"
    data = json.loads((tmp_path / "synergies.json").read_text(encoding="utf-8"))
    assert [item["hero_b_id"] for item in data] == [2]  # 失效相性已修复删除
    assert window._dialogs._pending_missing_references is None

    question_count = len(recorded["question"])
    window._flush_pending_data_prompts()  # 幂等：无挂起时无操作
    assert len(recorded["question"]) == question_count


def test_flush_pending_load_failed_warning(tmp_path: Path, monkeypatch) -> None:
    """挂起的加载失败告警经 show_pending_load_failed 补弹一次，不重弹。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade, visible=False)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        facade, "load_all",
        lambda: (_ for _ in ()).throw(RuntimeError("磁盘错误")),
    )
    window._load_data()

    window._dialogs.show_pending_load_failed()

    assert any("数据加载失败" in text and "磁盘错误" in text for text in recorded["warning"])
    assert not window._dialogs._load_failed_warning_pending

    warning_count = len(recorded["warning"])
    window._dialogs.show_pending_load_failed()  # 幂等
    assert len(recorded["warning"]) == warning_count
