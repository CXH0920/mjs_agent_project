"""任务结果台账回归：JSONL 追加、过滤读取、写失败不影响任务本身。"""

from __future__ import annotations

from src.business.common.task_ledger import read_entries, record_task


def test_record_and_read_round_trip(tmp_path):
    ledger = tmp_path / "task_results.jsonl"
    record_task("ops_weekly", ok=True, total=4, duration_s=12.34, path=ledger)
    record_task("pull_data_push", ok=False, exit_code=1, failed=2,
                duration_s=3.5, reason="manifest 不一致", path=ledger)

    entries = read_entries(ledger)
    assert len(entries) == 2
    assert entries[0]["task"] == "ops_weekly"
    assert entries[0]["ok"] is True and entries[0]["total"] == 4
    assert entries[1]["ok"] is False
    assert entries[1]["reason"] == "manifest 不一致"
    assert entries[1]["failed"] == 2


def test_read_filters_by_task_and_failures(tmp_path):
    ledger = tmp_path / "task_results.jsonl"
    record_task("ops_weekly", ok=True, path=ledger)
    record_task("rag_index", ok=False, exit_code=1, path=ledger)
    record_task("rag_index", ok=True, path=ledger)

    assert [e["ts"] for e in read_entries(ledger, task="rag_index", only_failures=True)]
    assert len(read_entries(ledger, task="rag_index")) == 2
    assert len(read_entries(ledger)) == 3
    assert read_entries(ledger, task="不存在") == []


def test_record_failure_does_not_raise(tmp_path):
    """台账写失败（路径是目录）只降级为告警，不得影响调用方任务流程"""
    blocked = tmp_path / "task_results.jsonl"
    blocked.mkdir()

    record_task("x", ok=True, path=blocked)  # 不应抛出
    assert read_entries(blocked) == []


def test_read_tolerates_corrupt_line(tmp_path):
    ledger = tmp_path / "task_results.jsonl"
    ledger.write_text('{"task": "a", "ok": true}\n{broken}\n', encoding="utf-8")

    entries = read_entries(ledger)

    assert len(entries) == 1
    assert entries[0]["task"] == "a"
