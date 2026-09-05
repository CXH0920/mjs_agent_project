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
    # 重置反转级别留下的 logger 级别，避免跨用例污染
    for name in ("src", "subprocess"):
        logging.getLogger(name).setLevel(logging.NOTSET)


def test_setup_logging_reconfigures_only_project_handlers(tmp_path, monkeypatch) -> None:
    root = logging.getLogger()
    external = logging.NullHandler()
    root.addHandler(external)
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)

    logging_config.setup_logging(log_level="DEBUG", log_to_file=True)
    assert external in root.handlers
    # 12 个路由文件 + 1 console + 1 debug.log
    assert len(_managed_handlers()) == 14
    # 反转级别：root 下限 WARNING（压制第三方），项目 src 前缀恒定 DEBUG
    assert root.level == logging.WARNING
    assert logging.getLogger("src").level == logging.DEBUG

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
        "token_rag": ("src.rag.test", "rag/rag.log"),
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


def test_ai_child_no_longer_writes_files_directly(tmp_path, monkeypatch) -> None:
    """B1：AI 子进程不再特殊，与普通 qprocess child 一致只输出控制台。"""
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)
    monkeypatch.setenv(logging_config._QPROCESS_CHILD_ENV, "1")
    monkeypatch.setenv("MJS_AI_CHILD", "1")

    logging_config.setup_logging(log_level="INFO", log_to_file=True)

    assert len(_managed_handlers()) == 1
    assert not any(tmp_path.rglob("*.log"))


def test_third_party_suppressed_and_debug_log_keeps_full(tmp_path, monkeypatch) -> None:
    """反转级别 + D3：第三方 INFO/DEBUG 被 root=WARNING 压制、不创建 LogRecord；
    项目 src 的 DEBUG/INFO 在 WARNING 模式下不进纯 src 常规文件，但进 debug.log 留底。"""
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)
    logging_config.setup_logging(log_level="WARNING", log_to_file=True)

    third = logging.getLogger("zfake_third_party")
    third.info("tp_info_token")
    third.debug("tp_debug_token")
    proj = logging.getLogger("src.dummy")
    proj.debug("proj_debug_token")
    proj.info("proj_info_token")
    proj.warning("proj_warn_token")
    for handler in _managed_handlers():
        handler.flush()

    debug_content = (tmp_path / "debug.log").read_text(encoding="utf-8")
    app_content = (tmp_path / "app.log").read_text(encoding="utf-8")

    # 第三方 INFO/DEBUG 被 root=WARNING 挡，任何文件都没有
    assert "tp_info_token" not in debug_content
    assert "tp_debug_token" not in debug_content
    # 项目 DEBUG/INFO 在 WARNING 模式不进纯 src 常规文件（handler=WARNING 裁剪）
    assert "proj_debug_token" not in app_content
    assert "proj_info_token" not in app_content
    assert "proj_warn_token" in app_content
    # 项目 DEBUG/INFO/WARNING 进 debug.log 全量留底（src=DEBUG 创建，debug.log=DEBUG 收）
    assert "proj_debug_token" in debug_content
    assert "proj_info_token" in debug_content
    assert "proj_warn_token" in debug_content
