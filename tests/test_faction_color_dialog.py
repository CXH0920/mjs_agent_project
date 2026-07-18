"""势力配色文件读写回归测试。"""

from src.ui.faction_color_dialog import load_faction_colors, save_faction_colors


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
