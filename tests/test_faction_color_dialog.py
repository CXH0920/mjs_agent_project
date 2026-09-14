"""势力配色文件读写与筛选顺序回归测试。"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import src.ui.shared.faction_colors as faction_colors
from PySide6.QtWidgets import QApplication, QMessageBox
from src.ui.configuration.faction_color_dialog import (
    FactionColorDialog,
    load_faction_colors,
    save_faction_colors,
)
from src.ui.shared.faction_colors import sort_factions_by_config


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
    assert list(load_faction_colors(path)) == ["东汉", "孙吴"]


def test_saved_file_uses_array_schema(tmp_path) -> None:
    path = tmp_path / "faction_colors.json"

    save_faction_colors({"东汉": "#ABCDEF"}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"faction": "东汉", "color": "#ABCDEF"}
    ]


def test_load_faction_colors_rejects_legacy_flat_dict(tmp_path) -> None:
    path = tmp_path / "faction_colors.json"
    path.write_text(json.dumps({"东汉": "#ABCDEF"}), encoding="utf-8")

    assert load_faction_colors(path) == {}


def test_load_faction_colors_skips_invalid_entries(tmp_path) -> None:
    path = tmp_path / "faction_colors.json"
    payload = [
        {"faction": "东汉", "color": "#ABCDEF"},
        {"faction": "坏色", "color": "red"},
        {"color": "#111111"},
        {"faction": "孙吴", "color": "#123456"},
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_faction_colors(path) == {"东汉": "#ABCDEF", "孙吴": "#123456"}


def test_save_faction_colors_rejects_invalid_hex(tmp_path) -> None:
    path = tmp_path / "faction_colors.json"

    try:
        save_faction_colors({"东汉": "red"}, path)
    except ValueError as exc:
        assert "有效 Hex" in str(exc)
    else:
        raise AssertionError("无效颜色应该被拒绝")


def test_sort_factions_orders_known_and_appends_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        faction_colors,
        "_faction_colors_cache",
        {"东汉": "#ABCDEF", "孙吴": "#123456", "蜀汉": "#921822"},
    )

    result = sort_factions_by_config(["蜀汉", "新势力", "孙吴", "东汉", "群雄"])

    assert result == ["东汉", "孙吴", "蜀汉", "新势力", "群雄"]


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
    assert list(load_faction_colors(path)) == ["东汉", "新势力"]


def test_dialog_moves_faction_up_and_persists_order(tmp_path) -> None:
    _app()
    path = tmp_path / "faction_colors.json"
    save_faction_colors({"东汉": "#ABCDEF", "孙吴": "#123456"}, path)
    dialog = FactionColorDialog(path)

    dialog._move_faction("孙吴", -1)
    dialog._save()

    assert list(load_faction_colors(path)) == ["孙吴", "东汉"]
    assert load_faction_colors(path)["东汉"] == "#ABCDEF"


def test_dialog_move_at_boundary_is_ignored(tmp_path) -> None:
    _app()
    path = tmp_path / "faction_colors.json"
    save_faction_colors({"东汉": "#ABCDEF", "孙吴": "#123456"}, path)
    dialog = FactionColorDialog(path)

    dialog._move_faction("东汉", -1)
    dialog._move_faction("孙吴", 1)

    assert list(dialog._pickers) == ["东汉", "孙吴"]


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
