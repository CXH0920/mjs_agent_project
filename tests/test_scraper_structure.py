"""Scraper 分包后的资源根目录回归测试。"""

from pathlib import Path

from src.config.env import PROJECT_ROOT
from src.scraper.ai import api_generator, batch, browser_generator, browser_session
from src.scraper.official_source import crawler, full, incremental


def test_scraper_resources_resolve_from_project_root() -> None:
    assert full.DEFAULT_OUTPUT == PROJECT_ROOT / "data" / "heroes.json"
    assert incremental.DEFAULT_DATA_DIR == PROJECT_ROOT / "data"
    assert crawler.IMAGES_DIR == PROJECT_ROOT / "images"
    assert batch.DEFAULT_DATA_DIR == PROJECT_ROOT / "data"
    assert api_generator.PROMPT_DIR == PROJECT_ROOT / "docs" / "prompts"
    assert browser_generator.PROMPT_DIR == PROJECT_ROOT / "docs" / "prompts"
    assert Path(browser_session.DEFAULT_BROWSER_CONFIG["user_data_dir"]) == PROJECT_ROOT / "data" / "edge_profile"
