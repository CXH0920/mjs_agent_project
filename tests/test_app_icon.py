"""应用图标资源路径测试。"""

from src.ui.app.app_icon import APP_ICON_PATH


def test_app_icon_path_points_to_project_icon() -> None:
    assert APP_ICON_PATH.name == "mjs.ico"
    assert APP_ICON_PATH.is_file()
