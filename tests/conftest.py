"""pytest 配置：将项目根目录加入 sys.path，统一使用 `from src.xxx` 导入模式"""

import atexit
import faulthandler
import os
import sys
from pathlib import Path

import pytest
import pytest_timeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Qt 离屏渲染统一在 conftest 收口（conftest 先于所有测试模块导入），
# 各测试文件无需再各自 setdefault
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.ocr import character_feature_repository
from src.business.recognition import ocr_worker as _ocr_worker_module

# 注销生产环境的退役 worker 进程退出钩子。该钩子在 xdist worker 子进程退出时会
# 因 QThread 未及时结束而调用 os._exit(1) 杀掉整个进程，导致 xdist 报
# "node down: Not properly terminated" 并把该进程上正在运行的测试记为失败。
# 测试进程无需该兜底，提前注销即可。
atexit.unregister(_ocr_worker_module._drain_retired_workers)

# pytest-timeout 的 thread 方法把超时线程栈写到 pytest 终端，而 xdist worker 被
# 强杀（node down）时该输出随缓冲丢失，CI 上只能看到用例名看不到卡在哪一行。
# 把栈同时写入每个 worker 独立的日志文件（logs/pytest-timeout-<pid>.log），
# CI 末尾统一 cat 出来即可定位卡死点。
_TIMEOUT_DUMP_HANDLE = None
_orig_dump_stacks = pytest_timeout.dump_stacks


def _dump_stacks_to_file(terminal) -> None:
    global _TIMEOUT_DUMP_HANDLE
    try:
        if _TIMEOUT_DUMP_HANDLE is None:
            log_dir = os.environ.get("MJS_TIMEOUT_DUMP_DIR") or "logs"
            os.makedirs(log_dir, exist_ok=True)
            _TIMEOUT_DUMP_HANDLE = open(
                os.path.join(log_dir, f"pytest-timeout-{os.getpid()}.log"),
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
def _clear_ocr_retired_workers() -> None:
    """每个测试后清空退役 worker 列表，防止残留的未退出 QThread 在后续测试中累积。"""
    _ocr_worker_module._RETIRED_WORKERS.clear()


@pytest.fixture(scope="session")
def qapp():
    """session 级 QApplication：整个测试进程仅创建一次，新测试直接以参数注入。

    取代各文件自定义的 _app() 样板（存量测试不强制迁移，两者共享同一实例）。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
