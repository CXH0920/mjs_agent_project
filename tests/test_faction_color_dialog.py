"""势力配色文件读写回归测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.ui.configuration.faction_color_dialog import (
    FactionColorDialog,
    load_faction_colors,
    save_faction_colors,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_faction_colors_round_trip(tmp_path) -> None:
    path = tmp_path / "faction_colors.json"
    colors = {"东汉": "#abcdef", "孙吴": "#123456"}

    save_faction_colors(colors, path)

    assert load_faction_colors(path) == {
        "东汉": "#ABCDEF",
        "孙吴": "#123456",
    }


def test_save_faction_colors_rejects_invalid_hex(tmp_path) -> None:
    path = tmp_path / "faction_colors.json"

    try:
        save_faction_colors({"东汉": "red"}, path)
    except ValueError as exc:
        assert "有效 Hex" in str(exc)
    else:
        raise AssertionError("无效颜色应该被拒绝")


def test_dialog_adds_faction_and_saves_to_config(tmp_path) -> None:
    _app()
    path = tmp_path / "faction_colors.json"
    save_faction_colors({"东汉": "#ABCDEF"}, path)
    dialog = FactionColorDialog(path)

    dialog._new_faction_name_input.setText("新势力")
    dialog._new_faction_picker.set_color("#123456")
    dialog._add_faction()
    dialog._save()

    assert load_faction_colors(path) == {"东汉": "#ABCDEF", "新势力": "#123456"}


def test_dialog_rejects_blank_or_duplicate_faction(tmp_path, monkeypatch) -> None:
    _app()
    path = tmp_path / "faction_colors.json"
    save_faction_colors({"东汉": "#ABCDEF"}, path)
    dialog = FactionColorDialog(path)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _, __, message: warnings.append(message))

    dialog._add_faction()
    dialog._new_faction_name_input.setText("东汉")
    dialog._add_faction()

    assert warnings == ["请输入势力名称。", "势力“东汉”已存在。"]
    assert set(dialog._pickers) == {"东汉"}
