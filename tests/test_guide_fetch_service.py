"""攻略生成服务测试。"""

from __future__ import annotations

from src.business.fetching.guide_fetch_service import GuideFetchService
from src.data.guide_manager import GuideManager
from src.data.models import HeroGuide


def test_incremental_fetch_without_missing_heroes_reports_status(tmp_path) -> None:
    guide_manager = GuideManager(tmp_path / "guides.json")
    guide_manager.add_guide(HeroGuide(hero_id=1))
    service = GuideFetchService(guide_manager)
    statuses: list[str] = []
    service.status_changed.connect(statuses.append)

    service.fetch_incremental([{"id": 1, "name": "曹操"}])

    assert statuses == ["所有武将已有攻略，无需生成"]

def test_fetch_specific_appends_no_rag_when_classic(tmp_path, monkeypatch) -> None:
    """经典模式（use_rag=False）时子进程参数应包含 --no-rag。"""
    guide_manager = GuideManager(tmp_path / "guides.json")
    service = GuideFetchService(guide_manager)
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(service, "_start_process", lambda args: captured.setdefault("args", args))
    service.fetch_specific([{"id": 1, "name": "曹操"}], backend="api", use_rag=False)
    assert "--no-rag" in captured["args"]


def test_fetch_all_omits_no_rag_when_rag_enabled(tmp_path, monkeypatch) -> None:
    """RAG 增强（use_rag=True）时子进程参数不应包含 --no-rag。"""
    guide_manager = GuideManager(tmp_path / "guides.json")
    service = GuideFetchService(guide_manager)
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(service, "_start_process", lambda args: captured.setdefault("args", args))
    service.fetch_all([{"id": 1, "name": "曹操"}], backend="api", use_rag=True)
    assert "--no-rag" not in captured["args"]


def test_fetch_specific_dump_failure_emits_error_and_removes_tmp(monkeypatch, tmp_path) -> None:
    """临时清单写失败（不可序列化对象）：句柄关闭、半截文件删除、发错误信号、不启动子进程。"""
    import tempfile
    from pathlib import Path

    from src.business.fetching import guide_fetch_service as guide_module

    service = GuideFetchService(GuideManager(tmp_path / "guides.json"))
    errors: list[str] = []
    service.error_occurred.connect(errors.append)
    started: list[list[str]] = []
    monkeypatch.setattr(service, "_start_process", lambda args: started.append(args))
    created: list[str] = []
    real_ntf = tempfile.NamedTemporaryFile

    def _spy(*args, **kwargs):
        handle = real_ntf(*args, **kwargs)
        created.append(handle.name)
        return handle

    monkeypatch.setattr(guide_module.tempfile, "NamedTemporaryFile", _spy)

    service.fetch_specific([{"id": 1, "name": "曹操", "bad": {1, 2}}])  # set 不可 JSON 序列化

    assert errors and "失败" in errors[0]
    assert started == []
    assert "tmp_path" not in service._context
    assert created and not Path(created[0]).exists()
