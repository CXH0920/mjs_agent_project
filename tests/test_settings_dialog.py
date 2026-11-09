# -*- coding: utf-8 -*-
"""settings_dialog 同名测试：页签结构、价格表校验、档案 CRUD 与草稿丢弃确认（D5 锚点迁移，自 test_ui 归位）。"""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTabWidget
from src.ui.configuration.settings_dialog import SettingsDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _settings_paths(tmp_path):
    env_path = tmp_path / "config.env"
    pricing_path = tmp_path / "model_pricing.json"
    profiles_path = tmp_path / "api_profiles.json"
    env_path.write_text("", encoding="utf-8")
    pricing_path.write_text(
        json.dumps(
            {"currency": "CNY", "unit": "per_million_tokens", "updated_at": "", "models": {}}
        ),
        encoding="utf-8",
    )
    profiles_path.write_text("{}", encoding="utf-8")
    return env_path, pricing_path, profiles_path


def test_settings_dialog_reject_with_changes_requires_confirmation(tmp_path, monkeypatch):
    """改过草稿后 reject（取消/Esc/X 共同终点）须先确认，拒绝则留在对话框。"""
    _app()
    env_path, pricing_path, profiles_path = _settings_paths(tmp_path)
    dialog = SettingsDialog(env_path=env_path, pricing_path=pricing_path, profiles_path=profiles_path)
    calls: list[int] = []

    def _decline() -> bool:
        calls.append(1)
        return False

    monkeypatch.setattr(dialog, "_confirm_discard", _decline)

    dialog._spin_widgets["REQUESTS_PER_MINUTE"].setValue(60)
    dialog.reject()
    assert calls == [1]
    assert dialog.result() == 0  # 用户拒绝丢弃：未关闭

    dialog._spin_widgets["REQUESTS_PER_MINUTE"].setValue(30)  # 改回装载值判净
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert len(calls) == 1  # 第二次未再弹确认


def test_settings_pricing_rejects_non_finite_cached_price(tmp_path):
    _app()
    env_path, pricing_path, profiles_path = _settings_paths(tmp_path)
    dialog = SettingsDialog(env_path=env_path, pricing_path=pricing_path, profiles_path=profiles_path)
    dialog._add_pricing_row("test-model")
    dialog._pricing_table.cellWidget(0, 3).setText("nan")

    with pytest.raises(ValueError, match="有限数字"):
        dialog._collect_pricing_config()


def test_settings_dialog_has_parameter_and_pricing_tabs(tmp_path, monkeypatch):
    _app()
    env_path = tmp_path / "config.env"
    pricing_path = tmp_path / "model_pricing.json"
    profiles_path = tmp_path / "api_profiles.json"
    env_path.write_text("DEEPSEEK_MODEL=test-model\n", encoding="utf-8")
    pricing_path.write_text(
        json.dumps(
            {
                "currency": "CNY",
                "unit": "per_million_tokens",
                "updated_at": "2026-07-22",
                "models": {
                    "test-model": {
                        "input_per_million": 1.5,
                        "output_per_million": 2.5,
                        "cached_input_per_million": 0.5,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    dialog = SettingsDialog(env_path=env_path, pricing_path=pricing_path, profiles_path=profiles_path)
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == ["参数配置", "价格配置"]
    assert dialog.minimumWidth() == 640
    assert dialog.minimumHeight() == 480
    assert dialog._unit_widget.text() == "百万tokens"
    assert dialog._pricing_table.rowCount() == 1
    assert [
        dialog._pricing_table.horizontalHeaderItem(index).text()
        for index in range(dialog._pricing_table.columnCount())
    ] == ["模型名称", "输入", "输出", "缓存命中"]
    assert dialog._pricing_table.item(0, 0).text() == "test-model"
    assert dialog._pricing_table.cellWidget(0, 1).decimals() == 2
    assert dialog._pricing_table.cellWidget(0, 2).decimals() == 2
    assert dialog._pricing_table.cellWidget(0, 3).text() == "0.5"

    dialog._pricing_table.item(0, 0).setText("updated-model")
    dialog._pricing_table.cellWidget(0, 1).setValue(3.0)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    dialog._on_save()

    saved = json.loads(pricing_path.read_text(encoding="utf-8"))
    assert saved["models"]["updated-model"]["input_per_million"] == 3.0
    assert "test-model" not in saved["models"]
    raw = pricing_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw


def test_settings_dialog_add_profile_switches_panel_and_saves(tmp_path, monkeypatch):
    """新增档案后面板应切到新行；填写后保存，新档案落盘、旧档案不污染。

    走真实 selectRow → currentCellChanged → _on_profile_selected 信号链，
    覆盖 _commit_panel 旧实现「刷新表格+selectRow 重入导致选中行弹回旧行」的回归。
    """
    _app()
    env_path = tmp_path / "config.env"
    pricing_path = tmp_path / "model_pricing.json"
    profiles_path = tmp_path / "api_profiles.json"
    env_path.write_text("DEEPSEEK_MODEL=test-model\n", encoding="utf-8")
    pricing_path.write_text(
        json.dumps(
            {
                "currency": "CNY",
                "unit": "per_million_tokens",
                "updated_at": "2026-08-26",
                "models": {
                    "test-model": {
                        "input_per_million": 1.5,
                        "output_per_million": 2.5,
                        "cached_input_per_million": 0.5,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    profiles_path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "name": "existing",
                        "provider": "deepseek",
                        "api_key": "sk-old",
                        "api_url": "https://api.deepseek.com/v1/chat/completions",
                        "model": "deepseek-v4-pro",
                        "enabled": True,
                        "is_default": True,
                        "note": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dialog = SettingsDialog(
        env_path=env_path, pricing_path=pricing_path, profiles_path=profiles_path
    )
    # 起始：面板回填第 0 行（existing）
    assert dialog._edit_index == 0
    assert dialog._name_widget.text() == "existing"

    # 新增档案 —— selectRow(new_row) 会触发 _on_profile_selected 重入路径
    dialog._on_add_profile()
    # 修复后：面板切到新行、名称框清空；旧实现会把选中行弹回 0、回填 existing
    assert dialog._edit_index == 1, "新增后应选中并编辑新档案（row 1）"
    assert dialog._name_widget.text() == "", "新增后面板名称框应为空（新档案草稿）"

    # 填写新档案字段（URL/模型由 deepseek 预设已预填）
    dialog._name_widget.setText("relay-2")
    dialog._api_key_widget.setText("sk-new")

    # 保存校验 —— 旧实现此时会报「第 2 个档案名称不能为空」（新档案 name 始终为空）
    data = dialog._collect_profiles()
    names = [p["name"] for p in data["profiles"]]
    assert "relay-2" in names
    assert "existing" in names
    relay = next(p for p in data["profiles"] if p["name"] == "relay-2")
    assert relay["api_key"] == "sk-new"
    # 旧档案未被污染（旧实现会把新名写到 existing 上、existing 的 key 不变）
    existing = next(p for p in data["profiles"] if p["name"] == "existing")
    assert existing["api_key"] == "sk-old"


def test_settings_dialog_remove_last_row_refreshes_panel(tmp_path, monkeypatch):
    """BUG-3：删除末行档案后面板应刷新到新选中行，不留残留、edit_index 非 None。"""
    _app()
    env_path = tmp_path / "config.env"
    pricing_path = tmp_path / "model_pricing.json"
    profiles_path = tmp_path / "api_profiles.json"
    env_path.write_text("DEEPSEEK_MODEL=test-model\n", encoding="utf-8")
    pricing_path.write_text(
        json.dumps(
            {
                "currency": "CNY", "unit": "per_million_tokens", "updated_at": "2026-08-26",
                "models": {"test-model": {"input_per_million": 1.5, "output_per_million": 2.5, "cached_input_per_million": 0.5}},
            }
        ), encoding="utf-8",
    )
    profiles_path.write_text(
        json.dumps(
            {"version": 1, "profiles": [
                {"name": "a", "provider": "deepseek", "api_key": "sk-a", "api_url": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-v4-pro", "enabled": True, "note": ""},
                {"name": "b", "provider": "openai-compatible", "api_key": "sk-b", "api_url": "https://x.example.com/v1/chat/completions", "model": "gpt-4o-mini", "enabled": False, "note": ""},
            ]}
        ), encoding="utf-8",
    )
    dialog = SettingsDialog(env_path=env_path, pricing_path=pricing_path, profiles_path=profiles_path)
    # 选中末行 b（行 1）
    dialog._profile_table.selectRow(1)
    assert dialog._edit_index == 1
    # 确认删除 b
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._on_remove_profile()
    # 面板应刷新到 a，edit_index=0（修复前：残留 b、edit_index=None）
    assert dialog._edit_index == 0, "删除末行后应选中并回填新行（a）"
    assert dialog._name_widget.text() == "a"
