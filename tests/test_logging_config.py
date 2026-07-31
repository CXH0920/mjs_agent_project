"""日志配置中心回归测试。"""

from __future__ import annotations

import logging
import logging.handlers

import pytest

import src.config.logging_config as logging_config


def _managed_handlers() -> list[logging.Handler]:
    root = logging.getLogger()
    return [
        handler
        for handler in root.handlers
        if getattr(handler, logging_config._MANAGED_HANDLER_ATTR, False)
    ]


@pytest.fixture(autouse=True)
def clean_logging_handlers():
    yield
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if getattr(handler, logging_config._MANAGED_HANDLER_ATTR, False):
            root.removeHandler(handler)
            handler.close()


def test_setup_logging_reconfigures_only_project_handlers(tmp_path, monkeypatch) -> None:
    root = logging.getLogger()
    external = logging.NullHandler()
    root.addHandler(external)
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)

    logging_config.setup_logging(log_level="DEBUG", log_to_file=True)
    assert external in root.handlers
    assert len(_managed_handlers()) == 12
    assert root.level == logging.DEBUG

    routes = {
        "token_app": ("src.ui.test", "app.log"),
        "token_official": ("src.scraper.official_source.crawler", "scraper/official.log"),
        "token_official_child": ("subprocess.official.stdout", "scraper/official.log"),
        "token_ai": ("src.scraper.ai.generation", "scraper/ai_generation.log"),
        "token_ai_child": ("subprocess.ai.stderr", "scraper/ai_generation.log"),
        "token_fetching": ("src.business.fetching.test", "business/fetching.log"),
        "token_emulator": ("src.business.emulator.test", "business/emulator.log"),
        "token_recognition": ("src.business.recognition.test", "business/recognition.log"),
        "token_analysis": ("src.business.analysis.test", "business/business.log"),
        "token_maintenance": ("src.business.maintenance.test", "business/business.log"),
        "token_data": ("src.data.test", "data/data.log"),
        "token_ocr": ("src.ocr.test", "ocr/ocr.log"),
        "token_capture": ("src.capture.test", "capture/capture.log"),
        "token_unclassified": ("subprocess.unknown.stdout", "subprocess/unclassified.log"),
    }
    for token, (logger_name, _target) in routes.items():
        logging.getLogger(logger_name).info(token)
    for handler in _managed_handlers():
        handler.flush()

    relative_paths = sorted({target for _logger_name, target in routes.values()})
    contents = {
        relative_path: (tmp_path / relative_path).read_text(encoding="utf-8")
        for relative_path in relative_paths
    }
    for token, (_logger_name, target) in routes.items():
        assert token in contents[target]
        assert sum(token in content for content in contents.values()) == 1

    logging_config.setup_logging(log_level="WARNING", log_to_file=False)
    managed = _managed_handlers()
    assert len(managed) == 1
    assert isinstance(managed[0], logging.StreamHandler)
    assert root.level == logging.WARNING

    root.removeHandler(external)


def test_qprocess_child_does_not_create_shared_file_handlers(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)
    monkeypatch.setenv(logging_config._QPROCESS_CHILD_ENV, "1")

    logging_config.setup_logging(log_level="INFO", log_to_file=True)

    assert len(_managed_handlers()) == 1
    assert not any(tmp_path.rglob("*.log"))
