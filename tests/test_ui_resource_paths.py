"""UI 分包后的资源根目录回归测试。"""

from src.config.env import PROJECT_ROOT
from src.ui.app.app_icon import APP_ICON_PATH
from src.ui.configuration.faction_color_dialog import COLORS_FILE
from src.ui.configuration.mumu_config_dialog import DEFAULT_SCREENSHOTS_DIR, DEFAULT_TEMPLATE_DIR
from src.ui.match.match_guide_panel import IMAGES_DIR as MATCH_IMAGES_DIR
from src.ui.match.match_guide_panel import SCREENSHOTS_DIR as MATCH_SCREENSHOTS_DIR
from src.ui.recommendation.hero_card_widget import IMAGES_DIR as RECOMMENDATION_IMAGES_DIR
from src.ui.recommendation.recommendation_panel import SCREENSHOTS_DIR as RECOMMENDATION_SCREENSHOTS_DIR
from src.ui.shared.faction_colors import FACTION_COLORS_FILE


def test_ui_resources_resolve_from_project_root() -> None:
    assert APP_ICON_PATH == PROJECT_ROOT / "mjs.ico"
    assert RECOMMENDATION_IMAGES_DIR == PROJECT_ROOT / "images"
    assert MATCH_IMAGES_DIR == PROJECT_ROOT / "images"
    assert RECOMMENDATION_SCREENSHOTS_DIR == PROJECT_ROOT / "screenshots"
    assert MATCH_SCREENSHOTS_DIR == PROJECT_ROOT / "screenshots"
    assert DEFAULT_TEMPLATE_DIR == PROJECT_ROOT / "templates"
    assert DEFAULT_SCREENSHOTS_DIR == PROJECT_ROOT / "screenshots"
    assert COLORS_FILE == PROJECT_ROOT / "config" / "faction_colors.json"
    assert COLORS_FILE == FACTION_COLORS_FILE
