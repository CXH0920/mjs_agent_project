import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
from src.config.env import parse_env_file, save_env_file
from src.ui.generation.backend_choose_dialog import BackendChooseDialog


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



def test_has_available_api_profile_checks_usable(monkeypatch):
    """BUG-6：可用性判定校验 enabled+URL+Key，仅 enabled 但 Key 空不算可用。

    判定逻辑已归位 src.config.profiles.has_available_api_profile（dialog 复刻版删除）。
    """
    from src.config import profiles

    # enabled 但 requires_key 供应商的 Key 为空 → 不可用
    monkeypatch.setattr(profiles, "load_api_profiles", lambda: {"profiles": [
        {"name": "bad", "provider": "deepseek", "enabled": True,
         "api_url": "https://x", "api_key": ""},
    ]})
    assert profiles.has_available_api_profile() is False
    # enabled + URL + Key 非空 → 可用
    monkeypatch.setattr(profiles, "load_api_profiles", lambda: {"profiles": [
        {"name": "good", "provider": "deepseek", "enabled": True,
         "api_url": "https://x", "api_key": "sk-x"},
    ]})
    assert profiles.has_available_api_profile() is True
    # 停用档案不算可用
    monkeypatch.setattr(profiles, "load_api_profiles", lambda: {"profiles": [
        {"name": "off", "provider": "deepseek", "enabled": False,
         "api_url": "https://x", "api_key": "sk-x"},
    ]})
    assert profiles.has_available_api_profile() is False
