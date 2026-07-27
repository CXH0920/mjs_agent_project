import tempfile
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QTabWidget
from src.config.env import parse_env_file, save_env_file
from src.ui.backend_choose_dialog import BackendChooseDialog
from src.ui.settings_dialog import SettingsDialog


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

    dialog = SettingsDialog(env_path=env_path, pricing_path=pricing_path)
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == ["参数配置", "价格配置"]
    assert dialog.minimumWidth() == 480
    assert dialog.minimumHeight() == 350
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
