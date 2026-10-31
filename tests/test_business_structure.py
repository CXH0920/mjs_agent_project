"""Business 分包后的结构与资源路径回归测试。"""

from src.business.emulator.capture_service import DEFAULT_SCREENSHOTS_DIR
from src.business.recognition.ocr_worker import DEFAULT_SCREENSHOT_DATA_DIR
from src.business.recognition.official_data_import_service import DATA_DIR, REVIEW_DIR
from src.config.env import PROJECT_ROOT


def test_business_resources_resolve_from_project_root() -> None:
    assert DEFAULT_SCREENSHOTS_DIR == PROJECT_ROOT / "screenshots"
    assert DEFAULT_SCREENSHOT_DATA_DIR == PROJECT_ROOT / "screenshot_data"
    assert DATA_DIR == PROJECT_ROOT / "data"
    assert REVIEW_DIR == PROJECT_ROOT / "screenshot_data" / "official_import"
