"""AI 生成任务的提交边界测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.scraper.ai_generation import (
    GenerationResult,
    run_guide_generation,
    run_synergy_generation,
)


class FakeGenerator:
    """按预设顺序返回生成结果，避免真实网络调用。"""

    def __init__(self, guides=None, synergies=None) -> None:
        self._guides = iter(guides or [])
        self._synergies = iter(synergies or [])

    def generate_guide(self, _hero: dict):
        return next(self._guides), {"prompt_tokens": 1, "completion_tokens": 2}

    def generate_synergy(self, _hero_a: dict, _hero_b: dict):
        return next(self._synergies), {"prompt_tokens": 1, "completion_tokens": 2}


def test_guide_failure_keeps_canonical_file_and_preserves_staging(tmp_path: Path) -> None:
    guide_path = tmp_path / "guides.json"
    original = [{"hero_id": 1, "description": "旧攻略"}]
    guide_path.write_text(json.dumps(original), encoding="utf-8")
    existing = {1: original[0]}
    generator = FakeGenerator(guides=[
        {"hero_id": 1, "description": "新攻略"},
        None,
    ])

    result = run_guide_generation(
        heroes=[{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}],
        generator=generator,
        guide_path=guide_path,
        existing_guides=existing,
        api_config={"model": "test"},
        update_mode=True,
    )

    assert not result.succeeded
    assert not result.committed
    assert json.loads(guide_path.read_text(encoding="utf-8")) == original
    assert result.staging_path == tmp_path / "guides.json.staging"
    assert json.loads(result.staging_path.read_text(encoding="utf-8")) == [
        {"hero_id": 1, "description": "新攻略"},
    ]


def test_full_synergy_failure_keeps_canonical_file(tmp_path: Path) -> None:
    synergy_path = tmp_path / "synergies.json"
    original = [{"hero_a_id": 1, "hero_b_id": 2, "score": 1}]
    synergy_path.write_text(json.dumps(original), encoding="utf-8")
    generator = FakeGenerator(synergies=[
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5},
        None,
        {"hero_a_id": 2, "hero_b_id": 3, "score": 6},
    ])

    result = run_synergy_generation(
        heroes=[
            {"id": 1, "name": "甲"},
            {"id": 2, "name": "乙"},
            {"id": 3, "name": "丙"},
        ],
        generator=generator,
        synergy_path=synergy_path,
        existing_synergy_dict={(1, 2): original[0]},
        existing_synergy_keys={(1, 2)},
        score_threshold=0,
        api_config={"model": "test"},
    )

    assert not result.succeeded
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == original
    assert result.staging_path is not None and result.staging_path.exists()


def test_successful_generation_atomically_commits_and_removes_staging(tmp_path: Path) -> None:
    guide_path = tmp_path / "guides.json"
    generator = FakeGenerator(guides=[{"hero_id": 1, "description": "新攻略"}])

    result = run_guide_generation(
        heroes=[{"id": 1, "name": "甲"}],
        generator=generator,
        guide_path=guide_path,
        existing_guides={},
        api_config={"model": "test"},
    )

    assert result.succeeded
    assert result.committed
    assert json.loads(guide_path.read_text(encoding="utf-8")) == [
        {"hero_id": 1, "description": "新攻略"},
    ]
    assert not (tmp_path / "guides.json.staging").exists()


def test_generation_result_is_successful_without_token_usage() -> None:
    """浏览器模式不返回 usage，不能据此判定任务失败。"""
    result = GenerationResult(completed=1)
    assert result.succeeded


def test_browser_mode_does_not_require_api_key(monkeypatch, tmp_path: Path) -> None:
    """浏览器后端应跳过 API Key 校验，并以结构化结果结束任务。"""
    import src.scraper.ai_batch as ai_batch
    import src.scraper.ai_generation as ai_generation
    import src.scraper.ai_playwright as ai_playwright

    closed = []

    class FakeBrowserGenerator:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(ai_batch, "load_heroes", lambda _path: [{"id": 1, "name": "甲"}])
    monkeypatch.setattr(ai_batch, "get_api_config", lambda: {"api_key": "", "api_url": "", "model": "test"})
    monkeypatch.setattr(
        ai_batch,
        "get_runtime_params",
        lambda: {"log_level": "WARNING", "log_to_file": False, "requests_per_minute": 1, "max_retries": 0, "http_timeout": 1},
    )
    monkeypatch.setattr(ai_playwright, "PlaywrightGenerator", FakeBrowserGenerator)
    monkeypatch.setattr(
        ai_generation,
        "run_guide_generation",
        lambda **_kwargs: GenerationResult(completed=1),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ai_batch", "--guide", "--browser", "--guides-file", str(tmp_path / "guides.json")],
    )

    ai_batch.main()

    assert closed == [True]


def test_cli_returns_nonzero_when_generation_result_has_failures(monkeypatch, tmp_path: Path) -> None:
    import src.scraper.ai_batch as ai_batch
    import src.scraper.ai_generation as ai_generation
    import src.scraper.ai_playwright as ai_playwright

    monkeypatch.setattr(ai_batch, "load_heroes", lambda _path: [{"id": 1, "name": "甲"}])
    monkeypatch.setattr(ai_batch, "get_api_config", lambda: {"api_key": "", "api_url": "", "model": "test"})
    monkeypatch.setattr(
        ai_batch,
        "get_runtime_params",
        lambda: {"log_level": "WARNING", "log_to_file": False, "requests_per_minute": 1, "max_retries": 0, "http_timeout": 1},
    )
    monkeypatch.setattr(ai_playwright, "PlaywrightGenerator", lambda: type("Generator", (), {"close": lambda self: None})())
    monkeypatch.setattr(
        ai_generation,
        "run_guide_generation",
        lambda **_kwargs: GenerationResult(failed_items=["甲"], staging_path=tmp_path / "guides.json.staging"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ai_batch", "--guide", "--browser", "--guides-file", str(tmp_path / "guides.json")],
    )

    with pytest.raises(SystemExit) as exc_info:
        ai_batch.main()

    assert exc_info.value.code == 1


def test_fetch_services_use_cli_exit_code_instead_of_stdout_failure_protocol(tmp_path: Path) -> None:
    from src.business.guide_fetch_service import GuideFetchService
    from src.business.synergy_fetch_service import SynergyFetchService
    from src.data.guide_manager import GuideManager

    guide_service = GuideFetchService(GuideManager(tmp_path / "guides.json"))
    synergy_service = SynergyFetchService()
    guide_results: list[tuple[bool, str]] = []
    synergy_results: list[tuple[bool, str]] = []
    guide_service.fetch_completed.connect(lambda success, detail: guide_results.append((success, detail)))
    synergy_service.fetch_completed.connect(lambda success, detail: synergy_results.append((success, detail)))

    guide_service._on_stdout_line("RESULT: FAIL=甲")
    synergy_service._on_stdout_line("RESULT: FAIL=甲<->乙")
    guide_service._on_process_finished(0)
    synergy_service._on_process_finished(0)
    guide_service._on_process_finished(1)
    synergy_service._on_process_finished(1)

    assert guide_results == [(True, "攻略生成完成")]
    assert synergy_results == [(True, "相性生成完成")]
