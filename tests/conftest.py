"""pytest 配置：将项目根目录加入 sys.path，统一使用 `from src.xxx` 导入模式"""

import atexit
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ocr import character_feature_repository
from src.business.recognition import ocr_worker as _ocr_worker_module

# 注销生产环境的退役 worker 进程退出钩子。该钩子在 xdist worker 子进程退出时会
# 因 QThread 未及时结束而调用 os._exit(1) 杀掉整个进程，导致 xdist 报
# "node down: Not properly terminated" 并把该进程上正在运行的测试记为失败。
# 测试进程无需该兜底，提前注销即可。
atexit.unregister(_ocr_worker_module._drain_retired_workers)


@pytest.fixture(autouse=True)
def _disable_user_character_cache(monkeypatch) -> None:
    """测试默认禁用汉字特征用户层缓存，避免动态构建写入仓库 data/ 目录。"""
    monkeypatch.setattr(character_feature_repository, "USER_CHARACTER_FEATURE_CACHE", None)


@pytest.fixture(autouse=True)
def _clear_ocr_retired_workers() -> None:
    """每个测试后清空退役 worker 列表，防止残留的未退出 QThread 在后续测试中累积。"""
    _ocr_worker_module._RETIRED_WORKERS.clear()
