# -*- coding: utf-8 -*-
"""doctor.py — 一键自检 CLI 入口（只读）

检查逻辑住在 src/business/diagnostics/doctor_checks.py（首启向导与本 CLI 共用），
本模块只负责命令行装配：参数解析、报告/JSON 输出与退出码。

用法：
    python -m src.scripts.doctor              # 全量检查
    python -m src.scripts.doctor --offline    # 跳过网络检查（合规触发器）
    python -m src.scripts.doctor --json       # 机器可读输出（ops.py 复用）
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from src.business.diagnostics.doctor_checks import FAIL, render, run_checks
from src.scripts.rag_script_io import install_crash_logger


def main() -> None:
    install_crash_logger("doctor")
    parser = argparse.ArgumentParser(description="一键自检（只读）")
    parser.add_argument("--offline", action="store_true", help="跳过网络检查（合规触发器）")
    parser.add_argument("--json", action="store_true", help="机器可读输出（ops.py 复用）")
    args = parser.parse_args()

    started = time.perf_counter()
    findings = run_checks(offline=args.offline)
    elapsed = time.perf_counter() - started

    if args.json:
        print(json.dumps(
            [{"group": f.group, "name": f.name, "status": f.status,
              "detail": f.detail, "fix": f.fix} for f in findings],
            ensure_ascii=False, indent=2))
    else:
        print(render(findings, elapsed))
    sys.exit(0 if not any(f.status == FAIL for f in findings) else 1)


if __name__ == "__main__":
    main()
