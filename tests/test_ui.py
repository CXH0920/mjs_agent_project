import tempfile
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QTabWidget
from src.config.env import parse_env_file, save_env_file
from src.ui.generation.backend_choose_dialog import BackendChooseDialog
from src.ui.configuration.settings_dialog import SettingsDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class TestEnvFileParsing:
    def test_parse_basic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text("KEY=val\nFOO=bar\n", encoding="utf-8")
            result = parse_env_file(env_path)
            assert result["KEY"] == "val"

    def test_parse_nonexistent(self):
        result = parse_env_file(Path("/x/config.env"))
        assert result == {}

    def test_parse_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "x.env"
            env_path.write_text("", encoding="utf-8")
            result = parse_env_file(env_path)
            assert result == {}

    def test_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "c.env"
            save_env_file(env_path, {"K": "v"})
            assert env_path.exists()
            assert not env_path.with_suffix(".env.tmp").exists()


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


def test_backend_dialog_displays_synergy_cost_estimate():
    _app()
    dialog = BackendChooseDialog(
        estimation={
            "mode": "synergy",
            "model": "test-model",
            "items": 3,
            "estimated_input_tokens": 2400,
            "estimated_output_tokens": 600,
            "estimated_tokens": 3000,
            "estimated_cost_cny": 0.0123,
        }
    )

    label_texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert "模式: 相性生成" in label_texts
    assert "需要生成的项数: 3" in label_texts
    assert "预估费用: CNY 0.0123" in label_texts


def test_backend_dialog_rag_selection_and_cost_recompute():
    _app()
    dialog = BackendChooseDialog(
        estimation={
            "mode": "synergy",
            "estimate_kind": "synergy",
            "model": "test-model",
            "items": 3,
            "estimated_input_tokens": 10500,
            "estimated_output_tokens": 600,
            "estimated_tokens": 11100,
            "estimated_cost_cny": 0.05,
        }
    )

    assert dialog.get_selected_rag() is True
    label_texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("预估输入 Token: 10,500" in text for text in label_texts)

    dialog._rag_classic_radio.setChecked(True)
    assert dialog.get_selected_rag() is False
    label_texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("预估输入 Token: 2,400" in text for text in label_texts)


def test_backend_dialog_blocks_accept_when_no_api_available(monkeypatch):
    """A2：无可用档案时 _on_accept 应拦截，不进入 accept。"""
    _app()
    import src.ui.generation.backend_choose_dialog as bcd
    monkeypatch.setattr(bcd, "has_available_api_profile", lambda: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    dialog = BackendChooseDialog(
        estimation={
            "mode": "synergy",
            "model": "m",
            "items": 1,
            "estimated_input_tokens": 1,
            "estimated_output_tokens": 1,
            "estimated_tokens": 2,
            "estimated_cost_cny": 0.01,
        }
    )
    assert bcd.has_available_api_profile() is False
    accepted = []
    monkeypatch.setattr(dialog, "accept", lambda: accepted.append(True))
    dialog._on_accept()
    assert accepted == []  # 被拦截，未 accept


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


def test_has_available_api_profile_checks_usable(monkeypatch):
    """BUG-6：可用性判定校验 enabled+URL+Key，仅 enabled 但 Key 空不算可用。

    判定逻辑已归位 src.config.env.has_available_api_profile（dialog 复刻版删除）。
    """
    from src.config import env

    # enabled 但 requires_key 供应商的 Key 为空 → 不可用
    monkeypatch.setattr(env, "load_api_profiles", lambda: {"profiles": [
        {"name": "bad", "provider": "deepseek", "enabled": True,
         "api_url": "https://x", "api_key": ""},
    ]})
    assert env.has_available_api_profile() is False
    # enabled + URL + Key 非空 → 可用
    monkeypatch.setattr(env, "load_api_profiles", lambda: {"profiles": [
        {"name": "good", "provider": "deepseek", "enabled": True,
         "api_url": "https://x", "api_key": "sk-x"},
    ]})
    assert env.has_available_api_profile() is True
    # 停用档案不算可用
    monkeypatch.setattr(env, "load_api_profiles", lambda: {"profiles": [
        {"name": "off", "provider": "deepseek", "enabled": False,
         "api_url": "https://x", "api_key": "sk-x"},
    ]})
    assert env.has_available_api_profile() is False
