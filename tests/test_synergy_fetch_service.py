"""相性获取业务服务测试。"""

from __future__ import annotations

from src.business.fetching.synergy_fetch_service import SynergyFetchService


def _service_with_captured_args(monkeypatch) -> tuple[SynergyFetchService, dict[str, list[str]]]:
    service = SynergyFetchService()
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(service, "_start_process", lambda args: captured.setdefault("args", args))
    return service, captured


def test_fetch_pair_appends_no_rag_when_classic(monkeypatch) -> None:
    """经典模式（use_rag=False）时相性配对子进程参数应包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pair(
        [{"id": 1, "name": "曹操"}, {"id": 2, "name": "刘备"}],
        backend="api",
        overwrite=False,
        use_rag=False,
    )
    assert "--no-rag" in captured["args"]


def test_fetch_pair_omits_no_rag_when_rag_enabled(monkeypatch) -> None:
    """RAG 增强（use_rag=True）时相性配对子进程参数不应包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pair(
        [{"id": 1, "name": "曹操"}, {"id": 2, "name": "刘备"}],
        backend="api",
        overwrite=False,
        use_rag=True,
    )
    assert "--no-rag" not in captured["args"]


def test_fetch_single_appends_no_rag_when_classic(monkeypatch) -> None:
    """经典模式（use_rag=False）时选定武将相性子进程参数应包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_single(
        {"id": 1, "name": "曹操"},
        [{"id": 1, "name": "曹操"}, {"id": 2, "name": "刘备"}],
        backend="api",
        use_rag=False,
    )
    assert "--no-rag" in captured["args"]
