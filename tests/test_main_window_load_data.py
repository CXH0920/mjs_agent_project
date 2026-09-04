# -*- coding: utf-8 -*-
"""MainWindow._load_data 缺失引用弹窗链锚（批次6步骤0：构造期唯一模态风险点）。

该弹窗链是"传小数据构造 MainWindow 会卡死"的根源（J3），也是组合根改造的
护栏：拆分后"失效关联 → 确认 → 修复落盘 → 完成提示"的行为必须逐项不变。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.data.manager import DataFacade
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


def _window_with(facade: DataFacade) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    window._data = facade
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
    """加载抛异常时弹"数据加载失败"告警而非崩溃。"""
    facade = _corpus_with_broken_reference(tmp_path)
    window = _window_with(facade)
    recorded = _patch_boxes(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        facade, "load_all",
        lambda: (_ for _ in ()).throw(RuntimeError("磁盘错误")),
    )

    window._load_data()

    assert any("数据加载失败" in text and "磁盘错误" in text for text in recorded["warning"])
