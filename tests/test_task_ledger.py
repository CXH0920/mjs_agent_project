"""任务结果台账回归：JSONL 追加、过滤读取、写失败不影响任务本身。"""

from __future__ import annotations

from src.business.execution.task_ledger import read_entries, record_task


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


def test_read_skips_non_dict_json_line(tmp_path):
    """回归（VUL-B4-001）：合法 JSON 但不是对象时不得整体崩溃。

    台账是长期追加的审计资产，一旦被外部工具追加了 123 / ["x"] 这类行，
    旧实现 entry.get 会抛 AttributeError，让整份台账读不出来——
    恰好摧毁"任何时候可查"这个唯一存在理由。
    """
    ledger = tmp_path / "task_results.jsonl"
    ledger.write_text(
        '{"task": "a", "ok": true}\n123\n["x"]\n"str"\nnull\n',
        encoding="utf-8",
    )

    entries = read_entries(ledger)

    assert len(entries) == 1
    assert entries[0]["task"] == "a"


def test_record_task_tolerates_bad_field_types(tmp_path):
    """回归（TD-A5-002）：字段类型脏值只降级告警，不得逃逸到调用方。

    归一化原先在 try 之外，int("bad") 会直接抛 ValueError 打断任务收尾，
    与"台账不得影响任务本身"的设计承诺相反。
    """
    ledger = tmp_path / "task_results.jsonl"

    record_task("x", ok=True, exit_code="not-an-int", total=None,
                failed="bad", duration_s="bad", path=ledger)  # 不应抛出

    assert read_entries(ledger) == []


def test_record_task_serialises_concurrent_writers(tmp_path):
    """回归（VUL-B3-001）：多进程并发追加不得丢记录。

    实测四个进程各写 400 条（NTFS）时，仅靠 O_APPEND 会静默丢 2%~12%，
    且不抛任何异常——台账出现无声空洞。这里用线程模拟并发写入，
    断言条数完全一致。
    """
    import threading

    ledger = tmp_path / "task_results.jsonl"
    per_writer, writers = 40, 4

    def _write(tag: str) -> None:
        for i in range(per_writer):
            record_task(f"{tag}-{i}", ok=True, total=1, path=ledger)

    threads = [threading.Thread(target=_write, args=(f"w{n}",)) for n in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(read_entries(ledger)) == per_writer * writers
