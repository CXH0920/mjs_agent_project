"""进程终止原语：Windows 进程树强杀执行体与看门狗基准时限（单一事实源）。

被两套 QProcess 编排共用，收口此前逐行重复的两份实现：
- business/execution/script_runner.py（维护脚本执行器）
- business/fetching/fetch_utils.py（抓取/生成服务的取消树杀）

本模块只回答「怎么杀」；「何时杀」（看门狗触发条件、取消时机、失败收尾
契约）由各编排方自理，刻意不在此合并。

Qt6 行为锚（两处 Starting 补杀的共同依据）：QProcess 尚处 Starting 状态时
kill() 会在事件循环内原生 abort，绝不能直接 kill；必须挂 started 信号、
等进程真正起来后再补杀（见 ScriptRunner._on_started_after_watchdog 与
BaseFetchService._on_started_after_cancel）。
"""

from __future__ import annotations

import subprocess

# 两套看门狗共用的基准时限（半小时量级的兜底，而非预期时长），两侧语义不同：
# - ScriptRunner：总时长上限——维护脚本正常数十秒～几分钟，上限只拦「永不结束」；
# - BaseFetchService：无进度窗口上限——进度行持续喂狗重置，正常任务长于
#   30 分钟也放行（browser 模式配对间有反风控随机休息，总时长语义会误杀
#   健康任务）。调整此值须核对 fetch 侧下限：最长静默间隔（休息 180 秒 +
#   单条生成约 3 分钟 ≈ 6 分钟）须保留数倍余量。
DEFAULT_WATCHDOG_MS = 30 * 60 * 1000


def terminate_process_tree(process_id: int) -> subprocess.Popen:
    """异步终止整棵进程树（Windows taskkill /T /F，子孙进程一并回收）。

    返回 taskkill 的 Popen，供需要等待清理结束的调用方轮询；不等待时由
    OS 自行收尾。启动 taskkill 失败抛 OSError，由调用方回退 process.kill()。
    仅在 Windows（os.name == "nt"）被调用，调用方已按平台分支。
    """
    return subprocess.Popen(
        ["taskkill", "/PID", str(process_id), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
