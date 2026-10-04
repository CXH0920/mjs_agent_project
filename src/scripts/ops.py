# -*- coding: utf-8 -*-
"""ops.py — 周更运维统一入口

子命令：
    weekly        周更流程编排（自动步骤 0/2/3/7/8，人工步骤打印提示）
    health        doctor 自检（透传 --offline）
    retry-failed  重跑上次 AI 生成失败项（batch.py --retry-failed --guide）
    clean         清理 .tmp_test 下 7 天前残留（--dry 只打印）

只做顺序编排与命令转发，不做服务、不做调度框架。
release/backup 不在此收口：备份轮转已由 snapshot_to_backups 内建，
发布仍直接用 release.py（待 P2-1 收口后评估纳入）。

用法：
    python -m src.scripts.ops weekly [--skip-tests]
    python -m src.scripts.ops health [--offline]
    python -m src.scripts.ops retry-failed
    python -m src.scripts.ops clean [--dry]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import date

from src.config.env import PROJECT_ROOT as ROOT
from src.scripts.rag_common import install_crash_logger


def _run(cmd: list[str], *, timeout: int | None = None) -> int:
    printable = " ".join(str(part) for part in cmd)
    print(f"\n$ {printable}", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  [超时] 超过 {timeout}s，中止", flush=True)
        return 1
    return proc.returncode


def cmd_weekly(args) -> int:
    """手册步骤 0→2→3→7→8 自动化；步骤 1/4/5/6 为人工动作，开头末尾均提示。"""
    from src.business.common.task_ledger import record_task

    started = time.monotonic()
    print(f"══ 周更开始 {date.today().isoformat()} ══")
    print("[提醒] 若本轮有新数据（手册步骤 1：覆盖 heroes/cards 等源文件），"
          "应已在本次运行前完成——diff 才能检出变更。", flush=True)

    failures: list[str] = []
    if _run([sys.executable, "-m", "src.scripts.pull_data", "pull"], timeout=300) != 0:
        failures.append("步骤0 私有仓pull")
    else:
        # 步骤 2：差异检测（无基线跳过会在终局输出显式标注）
        _run([sys.executable, "-m", "src.scripts.diff_source_data"])
        # 步骤 3：语料维护（增量；--force 全量请手动跑）
        if _run([sys.executable, "-m", "src.scripts.maintain_rag"], timeout=3600) != 0:
            failures.append("步骤3 语料维护")
        if args.skip_tests:
            print("\n[步骤7] 按要求跳过回归测试", flush=True)
        elif _run([sys.executable, "-m", "pytest", "-q"], timeout=900) != 0:
            failures.append("步骤7 pytest")
        # 步骤 8：回推私有仓（前面有失败时不推，避免把可疑数据固化）
        if failures:
            print("\n[步骤8] 前置步骤失败，跳过 push（数据不会被固化到私有仓）", flush=True)
        elif _run([sys.executable, "-m", "src.scripts.pull_data", "push"], timeout=600) != 0:
            failures.append("步骤8 私有仓push")

    print(f"\n{'─' * 30}")
    print("[人工步骤提醒] 步骤 1 获取新数据｜步骤 4 LLM 精化（可选）｜"
          "步骤 5 人工审阅变更清单｜步骤 6 更新 changelog——如本轮有变更请手动完成")
    print(f"{'─' * 30}")

    duration = time.monotonic() - started
    record_task("ops_weekly", ok=not failures, failed=len(failures),
                duration_s=duration, reason="；".join(failures))
    summary = "、".join(failures) if failures else "自动化步骤全部成功"
    print(f"\n══ 周更结束：{summary}（耗时 {duration:.0f}s）══")
    return 1 if failures else 0


def cmd_health(args) -> int:
    cmd = [sys.executable, "-m", "src.scripts.doctor"]
    if args.offline:
        cmd.append("--offline")
    return _run(cmd, timeout=120)


def cmd_retry_failed(args) -> int:
    print("[提示] 定向重试只跑上次失败项；恢复情况见运行结束的 [定向重试] 汇总行", flush=True)
    return _run([sys.executable, "-m", "src.scraper.ai_batch", "--retry-failed", "--guide"])


def cmd_clean(args) -> int:
    """清理 .tmp_test 下 7 天前的条目（timeout_dumps 由 conftest 自管，跳过）。"""
    tmp_root = ROOT / ".tmp_test"
    now = time.time()
    removed = 0
    entries = sorted(tmp_root.iterdir()) if tmp_root.is_dir() else []
    for entry in entries:
        if entry.name == "timeout_dumps":
            continue
        try:
            age_days = (now - entry.stat().st_mtime) / 86400
        except OSError:
            continue
        if age_days < 7:
            continue
        print(f"{'[dry] 将删除' if args.dry else '删除'}: {entry.name}（{age_days:.0f} 天前）")
        if not args.dry:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        removed += 1
    print(f"\n{'[dry] ' if args.dry else ''}共 {removed} 个条目{'待删除' if args.dry else '已处理'}")
    return 0


def main() -> None:
    install_crash_logger("ops")
    parser = argparse.ArgumentParser(description="周更运维统一入口")
    sub = parser.add_subparsers(dest="command", required=True)

    p_weekly = sub.add_parser("weekly", help="周更编排：pull→diff→maintain→pytest→push")
    p_weekly.add_argument("--skip-tests", action="store_true", help="跳过步骤 7 回归测试")

    p_health = sub.add_parser("health", help="doctor 自检")
    p_health.add_argument("--offline", action="store_true", help="跳过网络检查")

    sub.add_parser("retry-failed", help="重跑上次 AI 生成失败项（guide）")

    p_clean = sub.add_parser("clean", help="清理 .tmp_test 下 7 天前残留")
    p_clean.add_argument("--dry", action="store_true", help="只打印，不删除")

    args = parser.parse_args()
    sys.exit({
        "weekly": cmd_weekly,
        "health": cmd_health,
        "retry-failed": cmd_retry_failed,
        "clean": cmd_clean,
    }[args.command](args))


if __name__ == "__main__":
    main()
