"""AI 生成任务的提交边界测试。"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

from src.business.fetching import fetch_utils
from src.business.fetching.base_fetch_service import BaseFetchService
from src.business.fetching.fetch_utils import cancel_process
from src.scraper.ai.api_generator import (
    AIBatchGenerator,
    MAX_OUTPUT_TOKENS,
    OUTPUT_BUDGET_EXHAUSTED_MESSAGE,
    _read_completion_content,
)
from src.scraper.ai.generation import (
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


class _FakeHttpResponse:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


class _FakeHttpClient:
    def __init__(self, response: dict):
        self._response = response
        self.payload: dict | None = None

    def post(self, _url: str, *, headers: dict, json: dict):
        self.payload = json
        return _FakeHttpResponse(self._response)


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


def test_api_request_disables_thinking_and_discards_reasoning(caplog) -> None:
    secret_reasoning = "不应进入后续链路"
    client = _FakeHttpClient({
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": "最终正文", "reasoning_content": secret_reasoning},
        }],
        "usage": {"completion_tokens": 12},
    })
    generator = AIBatchGenerator(api_key="test", max_retries=1)
    generator._client.close()
    generator._client = client

    with caplog.at_level("DEBUG", logger="src.scraper.ai"):
        response = generator._call_api([{"role": "user", "content": "test"}])

    assert client.payload["max_tokens"] == MAX_OUTPUT_TOKENS == 16_384
    assert client.payload["thinking"] == {"type": "disabled"}
    assert response == {
        "content": "最终正文",
        "finish_reason": "stop",
        "usage": {"completion_tokens": 12},
    }
    assert secret_reasoning not in repr(response)
    assert secret_reasoning not in caplog.text


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [("部分正文", "length"), ("", "stop")],
)
def test_api_output_budget_failure_has_explicit_message(
    content: str,
    finish_reason: str,
    caplog,
) -> None:
    with caplog.at_level("ERROR", logger="src.scraper.ai.api_generator"):
        result, usage = _read_completion_content({
            "content": content,
            "finish_reason": finish_reason,
            "usage": {"completion_tokens": MAX_OUTPUT_TOKENS},
        })

    assert result is None
    assert usage == {"completion_tokens": MAX_OUTPUT_TOKENS}
    assert OUTPUT_BUDGET_EXHAUSTED_MESSAGE in caplog.text


def test_base_fetch_service_surfaces_output_budget_failure() -> None:
    service = _LineRecordingService()
    service._process = _FakeProcess([
        f"[ERROR] {OUTPUT_BUDGET_EXHAUSTED_MESSAGE}\n".encode("utf-8")
    ])
    errors: list[str] = []
    service.error_occurred.connect(errors.append)

    service._on_finished(1)

    assert errors == [OUTPUT_BUDGET_EXHAUSTED_MESSAGE]


def test_base_fetch_service_does_not_duplicate_failed_process_output(caplog) -> None:
    output_line = "UNIQUE_CHILD_FAILURE_DETAIL"
    service = _LineRecordingService()
    service._process = _FakeProcess([f"{output_line}\n".encode("utf-8")])

    with caplog.at_level("INFO"):
        service._on_finished(1)

    assert caplog.text.count(output_line) == 1


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


def test_terminate_process_tree_uses_taskkill_for_all_descendants(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    launched = object()

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return launched

    monkeypatch.setattr(fetch_utils.subprocess, "Popen", fake_popen)

    assert fetch_utils._terminate_process_tree(2468) is launched
    assert calls == [
        (
            ["taskkill", "/PID", "2468", "/T", "/F"],
            {
                "stdout": fetch_utils.subprocess.DEVNULL,
                "stderr": fetch_utils.subprocess.DEVNULL,
                "creationflags": getattr(fetch_utils.subprocess, "CREATE_NO_WINDOW", 0),
            },
        ),
    ]


def test_base_fetch_service_reports_user_cancellation_after_process_exit() -> None:
    class RunningProcess(_FakeProcess):
        def state(self):
            return QProcess.ProcessState.Running

        def kill(self) -> None:
            pass

    service = _LineRecordingService()
    service._process = RunningProcess()
    cancelled: list[bool] = []
    statuses: list[str] = []
    service.cancelled.connect(lambda: cancelled.append(True))
    service.status_changed.connect(statuses.append)

    service.cancel()
    service._on_finished(1)

    assert cancelled == [True]
    assert statuses[-1] == "子进程已中止"


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


def test_full_synergy_failure_commits_successes_and_preserves_failed_pair(
    tmp_path: Path, capsys,
) -> None:
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
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5, "last_updated": date.today().isoformat()},
        {"hero_a_id": 1, "hero_b_id": 3, "score": 2},
        {"hero_a_id": 2, "hero_b_id": 3, "score": 6, "last_updated": date.today().isoformat()},
    ]
    output = capsys.readouterr().out
    assert "[1/3] 甲 <-> 乙 OK" in output
    assert "[2/3] 甲 <-> 丙 FAIL" in output


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
    ])

    result = run_synergy_pair_generation(
        pair_file=str(pair_file), heroes=[], generator=generator, synergy_path=synergy_path,
        existing_synergy_dict={(1, 3): original[0]}, existing_synergy_keys={(1, 3)},
    )

    assert not result.succeeded
    assert result.skipped == 1
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == [
        {"hero_a_id": 1, "hero_b_id": 3, "score": 2},
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5, "last_updated": date.today().isoformat()},
    ]


def test_synergy_pair_overwrites_existing_when_requested(tmp_path: Path) -> None:
    synergy_path = tmp_path / "synergies.json"
    original = {"hero_a_id": 1, "hero_b_id": 2, "score": 2}
    pair_file = tmp_path / "pairs.json"
    pair_file.write_text(json.dumps([
        {"id": 1, "name": "甲"},
        {"id": 2, "name": "乙"},
    ]), encoding="utf-8")

    result = run_synergy_pair_generation(
        pair_file=str(pair_file), heroes=[], generator=FakeGenerator(synergies=[
            {"hero_a_id": 1, "hero_b_id": 2, "score": 8},
        ]), synergy_path=synergy_path,
        existing_synergy_dict={(1, 2): original}, existing_synergy_keys={(1, 2)},
        update_mode=True,
    )

    assert result.completed == 1
    assert result.skipped == 0
    assert json.loads(synergy_path.read_text(encoding="utf-8")) == [
        {"hero_a_id": 1, "hero_b_id": 2, "score": 8, "last_updated": date.today().isoformat()},
    ]


def test_synergy_single_failure_commits_successes_and_preserves_failed_pair(
    tmp_path: Path, capsys,
) -> None:
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
        {"hero_a_id": 1, "hero_b_id": 2, "score": 5, "last_updated": date.today().isoformat()},
    ]


def test_synergy_single_reports_progress_for_existing_pair(tmp_path: Path, capsys) -> None:
    synergy_path = tmp_path / "synergies.json"
    single_file = tmp_path / "single.json"
    single_file.write_text(json.dumps([{"id": 1, "name": "甲"}]), encoding="utf-8")

    result = run_synergy_single_generation(
        single_file=str(single_file),
        heroes=[{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}],
        generator=FakeGenerator(),
        synergy_path=synergy_path,
        existing_synergy_dict={(1, 2): {"hero_a_id": 1, "hero_b_id": 2, "score": 5}},
        existing_synergy_keys={(1, 2)},
    )

    assert result.skipped == 1
    assert "[1/1] 乙 SKIP（已有相性）" in capsys.readouterr().out


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
    import src.scraper.ai.browser_generator as ai_playwright

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
    method(*args)

    assert events == ["send", "rest", "send", "rest", "send"]


def test_browser_mode_does_not_require_api_key(monkeypatch, tmp_path: Path) -> None:
    """浏览器后端应跳过 API Key 校验，并以结构化结果结束任务。"""
    import src.scraper.ai.batch as ai_batch
    import src.scraper.ai.generation as ai_generation
    import src.scraper.ai.browser_generator as ai_playwright

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
    import src.scraper.ai.batch as ai_batch
    import src.scraper.ai.generation as ai_generation
    import src.scraper.ai.browser_generator as ai_playwright

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
    from src.business.fetching.guide_fetch_service import GuideFetchService
    from src.business.fetching.synergy_fetch_service import SynergyFetchService
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


def test_fetch_services_route_subprocess_logs_by_workflow(tmp_path: Path) -> None:
    from src.business.fetching.guide_fetch_service import GuideFetchService
    from src.business.fetching.hero_fetch_service import HeroFetchService
    from src.business.fetching.synergy_fetch_service import SynergyFetchService
    from src.data.guide_manager import GuideManager

    hero_service = HeroFetchService()
    guide_service = GuideFetchService(GuideManager(tmp_path / "guides.json"))
    synergy_service = SynergyFetchService()

    assert hero_service._log_stdout.name == "subprocess.official.stdout"
    assert hero_service._log_stderr.name == "subprocess.official.stderr"
    assert guide_service._log_stdout.name == "subprocess.ai.stdout"
    assert guide_service._log_stderr.name == "subprocess.ai.stderr"
    assert synergy_service._log_stdout.name == "subprocess.ai.stdout"
    assert synergy_service._log_stderr.name == "subprocess.ai.stderr"


def test_generation_progress_does_not_forward_arbitrary_child_logs(tmp_path: Path) -> None:
    from src.business.fetching.guide_fetch_service import GuideFetchService
    from src.data.guide_manager import GuideManager

    service = GuideFetchService(GuideManager(tmp_path / "guides.json"))
    forwarded: list[str] = []
    service.progress_output.connect(forwarded.append)

    service._on_stdout_line("2026-07-31 [ERROR] src.scraper.ai: 原始回复：敏感正文")
    service._on_stdout_line("[1/2] 甲 OK")
    service._on_stdout_line("2026-07-31 [INFO] src.scraper.ai: [休息] 随机休息 60 秒...")

    assert forwarded == [
        "[1/2] 甲 OK",
        "2026-07-31 [INFO] src.scraper.ai: [休息] 随机休息 60 秒...",
    ]


def test_browser_generator_logs_no_reply_or_parsed_content(monkeypatch, caplog) -> None:
    import src.scraper.ai.browser_generator as browser_generator

    secret_reply = "SECRET_REPLY_CONTENT"
    secret_parsed = "SECRET_PARSED_CONTENT"
    generator = object.__new__(browser_generator.PlaywrightGenerator)
    generator._guide_system_sent = False
    generator._guide_rest_required = False
    generator._send_and_wait = lambda _prompt: secret_reply
    monkeypatch.setattr(browser_generator, "load_prompt", lambda _path: "system")
    monkeypatch.setattr(browser_generator, "build_guide_prompt", lambda _hero: "guide")
    monkeypatch.setattr(browser_generator, "extract_json", lambda _reply: {"description": secret_parsed})
    monkeypatch.setattr(browser_generator, "validate_guide", lambda _raw: None)

    with caplog.at_level("DEBUG", logger="src.scraper.ai"):
        generator.generate_guide({"id": 1, "name": "甲"})

    assert secret_reply not in caplog.text
    assert secret_parsed not in caplog.text


def test_synergy_progress_advances_only_after_terminal_result() -> None:
    from src.business.fetching.synergy_fetch_service import SynergyFetchService

    service = SynergyFetchService()
    progress_values: list[tuple[int, int]] = []
    service.progress_value.connect(lambda current, total: progress_values.append((current, total)))

    service._on_stdout_line("[1/3] 甲 <-> 乙 START")
    service._on_stdout_line("[1/3] 甲 <-> 乙 OK - 评分: 8")
    service._on_stdout_line("[2/3] 甲 <-> 丙 FAIL")
    service._on_stdout_line("[3/3] 甲 <-> 丁 SKIP（已有相性）")

    assert progress_values == [(1, 3), (2, 3), (3, 3)]
