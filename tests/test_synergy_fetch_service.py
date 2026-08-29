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


def test_fetch_pairs_list_builds_synergy_list_args(monkeypatch) -> None:
    """实战配队清单：经典模式参数包含 --synergy-list 与 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pairs_list(
        [{"hero_a_id": 1, "hero_b_id": 2}],
        backend="api",
        overwrite=False,
        use_rag=False,
    )
    args = captured["args"]
    assert "--synergy-list" in args
    assert "--no-rag" in args
    assert "--update" not in args


def test_fetch_pairs_list_overwrite_appends_update(monkeypatch) -> None:
    """实战配队清单：覆盖模式参数包含 --update 且不包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pairs_list(
        [{"hero_a_id": 1, "hero_b_id": 2}],
        backend="api",
        overwrite=True,
        use_rag=True,
    )
    args = captured["args"]
    assert "--synergy-list" in args
    assert "--update" in args
    assert "--no-rag" not in args
