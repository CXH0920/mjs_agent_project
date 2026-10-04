"""pytest 配置：将项目根目录加入 sys.path，统一使用 `from src.xxx` 导入模式"""

import atexit
import faulthandler
import os
import sys
import time
from pathlib import Path

import pytest
import pytest_timeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Qt 离屏渲染统一在 conftest 收口（conftest 先于所有测试模块导入），
# 各测试文件无需再各自 setdefault
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.business.recognition import ocr_worker as _ocr_worker_module
from src.ocr import character_feature_repository
from src.scraper.ai import rag_prompt

# 注销生产环境的退役 worker 进程退出钩子。该钩子在 xdist worker 子进程退出时会
# 因 QThread 未及时结束而调用 os._exit(1) 杀掉整个进程，导致 xdist 报
# "node down: Not properly terminated" 并把该进程上正在运行的测试记为失败。
# 测试进程无需该兜底，提前注销即可。
atexit.unregister(_ocr_worker_module._drain_retired_workers)

# pytest-timeout 的 thread 方法把超时线程栈写到 pytest 终端，而 xdist worker 被
# 强杀（node down）时该输出随缓冲丢失，CI 上只能看到用例名看不到卡在哪一行。
# 把栈同时写入每个 worker 独立的日志文件（.tmp_test/timeout_dumps/pytest-timeout-<pid>.log），
# CI 末尾统一 cat 出来即可定位卡死点。dump 与 pytest 临时文件同收 .tmp_test，不再污染 logs/。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# pyproject 的 --basetemp=.tmp_test/pytest-tmp：pathlib.mkdir 默认不建父级，CI 全新
# checkout 时 .tmp_test 不在仓库里，xdist sessionstart 会直接 INTERNALERROR（2026-10-04
# Actions 实证）。conftest 先于 sessionstart 导入，在此兜底建父目录；xdist 各 worker
# 重复执行幂等无害。
(_PROJECT_ROOT / ".tmp_test").mkdir(parents=True, exist_ok=True)
_TIMEOUT_DUMP_DIR = Path(
    os.environ.get("MJS_TIMEOUT_DUMP_DIR") or (_PROJECT_ROOT / ".tmp_test" / "timeout_dumps")
)
_TIMEOUT_DUMP_HANDLE = None
_orig_dump_stacks = pytest_timeout.dump_stacks


def _prune_stale_timeout_dumps(keep_days: int = 7) -> None:
    """清理超期转储；转储是诊断辅助，清理失败静默（不构成失败）。"""
    cutoff = time.time() - keep_days * 86400
    try:
        for path in _TIMEOUT_DUMP_DIR.glob("pytest-timeout-*.log"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
    except OSError:
        pass


# xdist 下每个 worker 导入 conftest 时各清一次，幂等无害
_prune_stale_timeout_dumps()


def _dump_stacks_to_file(terminal) -> None:
    global _TIMEOUT_DUMP_HANDLE
    try:
        if _TIMEOUT_DUMP_HANDLE is None:
            _TIMEOUT_DUMP_DIR.mkdir(parents=True, exist_ok=True)
            _TIMEOUT_DUMP_HANDLE = open(
                _TIMEOUT_DUMP_DIR / f"pytest-timeout-{os.getpid()}.log",
                "a", encoding="utf-8",
            )
        faulthandler.dump_traceback(file=_TIMEOUT_DUMP_HANDLE)
    except OSError:
        pass
    _orig_dump_stacks(terminal)


pytest_timeout.dump_stacks = _dump_stacks_to_file


@pytest.fixture(autouse=True)
def _disable_user_character_cache(monkeypatch) -> None:
    """测试默认禁用汉字特征用户层缓存，避免动态构建写入仓库 data/ 目录。"""
    monkeypatch.setattr(character_feature_repository, "USER_CHARACTER_FEATURE_CACHE", None)


@pytest.fixture(autouse=True)
def _isolate_api_profiles_file(tmp_path, monkeypatch) -> None:
    """测试默认把 api_profiles.json 默认路径指向 tmp，保证任何 patch 失效也
    不会写真实用户配置（2026-09-30 事故：profiles 域拆分期间 patch 目标与
    值副本绑定脱节，无参 save_api_profiles 覆盖了真实档案）。需要写默认路径
    的测试显式传 path 或自行 patch；本 fixture 是兜底而非替代。"""
    from src.config import profiles as config_profiles

    monkeypatch.setattr(
        config_profiles, "DEFAULT_PROFILES_FILE", tmp_path / "api_profiles.json"
    )


@pytest.fixture(autouse=True)
def _isolate_task_ledger(tmp_path, monkeypatch) -> None:
    """测试默认把任务台账指向 tmp：fetch 系用例触发 _on_finished 时会写台账，
    不隔离则测试运行持续污染真实 logs/task_results.jsonl（2026-10-04 实证）。"""
    from src.business.common import task_ledger

    monkeypatch.setattr(task_ledger, "LEDGER_PATH", tmp_path / "task_results.jsonl")


@pytest.fixture(autouse=True)
def _clear_ocr_retired_workers() -> None:
    """每个测试后从退役列表移除已结束的 worker；仍在运行的必须保留。

    _RETIRED_WORKERS 是 shutdown 超时转退役、线程尚未退出的 QThread 唯一的
    保活引用，直接 clear() 会令对象在线程运行中被 GC 析构，原生层访问已释放
    内存直接杀死测试进程（xdist 报 node down: Not properly terminated，
    2026-09-16 CI 实证；最小复现为运行中 QThread 零引用后退出码 127）。
    仍在运行的保留下次 teardown 再清，届时线程早已退出，析构安全。
    """
    _ocr_worker_module._RETIRED_WORKERS[:] = [
        w for w in _ocr_worker_module._RETIRED_WORKERS if not w.isRunning()
    ]


@pytest.fixture(autouse=True)
def _reset_rag_degraded_reason() -> None:
    """每个测试前清空 rag_prompt.degraded_reason 模块全局。

    CI 精简环境缺 RAG 依赖，走真实现的用例（如 test_build_guide_prompt）会把
    ModuleNotFoundError 写入该全局且不消费；残留被同 xdist worker 后续生成循环
    用例的 _report_rag_degradation 消费后多打一行降级提示，击穿
    test_generation_loops 的 stdout 逐字符断言（时绿时红，取决于调度）。
    """
    rag_prompt.degraded_reason = None


@pytest.fixture(scope="session")
def qapp():
    """session 级 QApplication：整个测试进程仅创建一次，新测试直接以参数注入。

    取代各文件自定义的 _app() 样板（存量测试不强制迁移，两者共享同一实例）。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
