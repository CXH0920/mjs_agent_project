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
    assert len(_managed_handlers()) == 10
    assert root.level == logging.DEBUG

    logging.getLogger("src.data.test").info("data message")
    logging.getLogger("src.ui.test").info("ui message")
    for handler in _managed_handlers():
        handler.flush()

    data_log = (tmp_path / "data" / "data.log").read_text(encoding="utf-8")
    app_log = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "data message" in data_log
    assert "data message" not in app_log
    assert "ui message" in app_log

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
