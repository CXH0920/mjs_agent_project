"""AI 生成任务的提交边界测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

from src.business.base_fetch_service import BaseFetchService
from src.business.fetch_utils import cancel_process
from src.scraper.ai_generation import (
    GenerationResult,
    run_guide_generation,
    run_synergy_generation,
    run_synergy_pair_generation,
    run_synergy_single_generation,
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


class _FakeProcess:
    def __init__(self, stdout_chunks: list[bytes] | None = None, stderr_chunks: list[bytes] | None = None):
        self._stdout_chunks = list(stdout_chunks or [])
        self._stderr_chunks = list(stderr_chunks or [])

    def readAllStandardOutput(self) -> bytes:
        return self._stdout_chunks.pop(0) if self._stdout_chunks else b""

    def readAllStandardError(self) -> bytes:
        return self._stderr_chunks.pop(0) if self._stderr_chunks else b""


class _LineRecordingService(BaseFetchService):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def _on_stdout_line(self, line: str) -> None:
        self.lines.append(line)


def test_base_fetch_service_buffers_partial_utf8_stdout_lines() -> None:
    line = "[1/2] 诸葛亮\n".encode("utf-8")
    service = _LineRecordingService()
    service._process = _FakeProcess([line[:8], line[8:]])

    service._on_stdout_ready()
    assert service.lines == []

    service._on_stdout_ready()
    assert service.lines == ["[1/2] 诸葛亮"]


def test_base_fetch_service_flushes_remaining_stdout_at_finish() -> None:
    service = _LineRecordingService()
    service._process = _FakeProcess(["最后一行".encode("utf-8")])

    service._on_finished(0)

    assert service.lines == ["最后一行"]


def test_cancel_process_does_not_block_event_loop() -> None:
    class RunningProcess:
        def __init__(self):
            self.killed = False
            self.wait_called = False

        def state(self):
            return QProcess.ProcessState.Running

        def kill(self) -> None:
            self.killed = True

        def waitForFinished(self, _timeout: int) -> None:
            self.wait_called = True

    process = RunningProcess()
    cancel_process(process)

    assert process.killed
    assert not process.wait_called


def test_guide_failure_commits_successes_and_preserves_failed_guide(tmp_path: Path) -> None:
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
    assert result.committed
    assert json.loads(guide_path.read_text(encoding="utf-8")) == [
        {"hero_id": 1, "description": "新攻略"},
    ]


def test_full_synergy_failure_commits_successes_and_preserves_failed_pair(tmp_path: Path) -> None:
    synergy_path = tmp_path / "synergies.json"
    original = [
        {"hero_a_id": 1, "hero_b_id": 2, "score": 1},
        {"hero_a_id": 1, "hero_b_id": 3, "score": 2},
    ]
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
        existing_synergy_dict={(1, 2): original[0], (1, 3): original[1]},
        existing_synergy_keys={(1, 2), (1, 3)},
        score_threshold=0,
        api_config={"model": "test"},
    )

    assert not result.succeeded
    assert result.committed
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == [
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5},
        {"hero_a_id": 1, "hero_b_id": 3, "score": 2},
        {"hero_a_id": 2, "hero_b_id": 3, "score": 6},
    ]


def test_successful_generation_atomically_commits(tmp_path: Path) -> None:
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


def test_synergy_pair_failure_commits_successes_and_preserves_failed_pair(tmp_path: Path) -> None:
    synergy_path = tmp_path / "synergies.json"
    original = [{"hero_a_id": 1, "hero_b_id": 3, "score": 2}]
    synergy_path.write_text(json.dumps(original), encoding="utf-8")
    pair_file = tmp_path / "pairs.json"
    pair_file.write_text(json.dumps([
        {"id": 1, "name": "甲"},
        {"id": 2, "name": "乙"},
        {"id": 3, "name": "丙"},
    ]), encoding="utf-8")
    generator = FakeGenerator(synergies=[
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5},
        None,
        {"hero_a_id": 2, "hero_b_id": 3, "score": 6},
    ])

    result = run_synergy_pair_generation(
        pair_file=str(pair_file), heroes=[], generator=generator, synergy_path=synergy_path,
        existing_synergy_dict={(1, 3): original[0]}, existing_synergy_keys={(1, 3)},
    )

    assert not result.succeeded
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == [
        {"hero_a_id": 1, "hero_b_id": 3, "score": 2},
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5},
        {"hero_a_id": 2, "hero_b_id": 3, "score": 6},
    ]


def test_synergy_single_failure_commits_successes_and_preserves_failed_pair(tmp_path: Path) -> None:
    synergy_path = tmp_path / "synergies.json"
    original = [{"hero_a_id": 1, "hero_b_id": 3, "score": 2}]
    synergy_path.write_text(json.dumps(original), encoding="utf-8")
    single_file = tmp_path / "single.json"
    single_file.write_text(json.dumps([{"id": 1, "name": "甲"}]), encoding="utf-8")
    generator = FakeGenerator(synergies=[{"hero_a_id": 1, "hero_b_id": 2, "score": 5}, None])

    result = run_synergy_single_generation(
        single_file=str(single_file),
        heroes=[{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}, {"id": 3, "name": "丙"}],
        generator=generator,
        synergy_path=synergy_path,
        existing_synergy_dict={(1, 3): original[0]},
        existing_synergy_keys=set(),
    )

    assert not result.succeeded
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == [
        {"hero_a_id": 1, "hero_b_id": 3, "score": 2},
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5},
    ]


def test_generation_result_is_successful_without_token_usage() -> None:
    """浏览器模式不返回 usage，不能据此判定任务失败。"""
    result = GenerationResult(completed=1)
    assert result.succeeded


@pytest.mark.parametrize(
    ("method_name", "system_sent_attr", "rest_required_attr", "args"),
    [
        ("generate_guide", "_guide_system_sent", "_guide_rest_required", ({"id": 1, "name": "甲"},)),
        (
            "generate_synergy",
            "_synergy_system_sent",
            "_synergy_rest_required",
            ({"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}),
        ),
    ],
)
def test_browser_generator_rests_before_next_successful_request(
    monkeypatch, method_name, system_sent_attr, rest_required_attr, args,
) -> None:
    import src.scraper.ai_playwright as ai_playwright

    events: list[str] = []
    generator = object.__new__(ai_playwright.PlaywrightGenerator)
    setattr(generator, system_sent_attr, False)
    setattr(generator, rest_required_attr, False)
    generator._send_and_wait = lambda _prompt: events.append("send") or "reply"
    generator._random_rest = lambda: events.append("rest")
    monkeypatch.setattr(ai_playwright, "load_prompt", lambda _path: "system")
    monkeypatch.setattr(ai_playwright, "build_guide_prompt", lambda _hero: "guide")
    monkeypatch.setattr(ai_playwright, "build_synergy_prompt", lambda _a, _b: "synergy")
    monkeypatch.setattr(ai_playwright, "extract_json", lambda _reply: {})
    monkeypatch.setattr(ai_playwright, "validate_guide", lambda raw: raw)
    monkeypatch.setattr(ai_playwright, "validate_synergy", lambda raw: raw)

    method = getattr(generator, method_name)
    method(*args)
    method(*args)

    assert events == ["send", "rest", "send"]


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
        lambda **_kwargs: GenerationResult(failed_items=["甲"]),
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
