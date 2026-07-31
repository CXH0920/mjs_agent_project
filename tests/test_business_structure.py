"""Business 分包后的结构与资源路径回归测试。"""

from src import business
from src.business.emulator.capture_service import DEFAULT_SCREENSHOTS_DIR
from src.business.fetching.guide_fetch_service import GuideFetchService
from src.business.fetching.hero_fetch_service import HeroFetchService
from src.business.fetching.synergy_fetch_service import SynergyFetchService
from src.business.maintenance.data_management_service import DataManagementService
from src.business.recognition.ocr_worker import DEFAULT_SCREENSHOT_DATA_DIR
from src.business.recognition.official_data_import_service import DATA_DIR, REVIEW_DIR
from src.config.env import PROJECT_ROOT


def test_business_public_exports_follow_new_packages() -> None:
    assert business.HeroFetchService is HeroFetchService
    assert business.GuideFetchService is GuideFetchService
    assert business.SynergyFetchService is SynergyFetchService
    assert business.DataManagementService is DataManagementService


def test_business_resources_resolve_from_project_root() -> None:
    assert DEFAULT_SCREENSHOTS_DIR == PROJECT_ROOT / "screenshots"
    assert DEFAULT_SCREENSHOT_DATA_DIR == PROJECT_ROOT / "screenshot_data"
    assert DATA_DIR == PROJECT_ROOT / "data"
    assert REVIEW_DIR == PROJECT_ROOT / "screenshot_data" / "official_import"
