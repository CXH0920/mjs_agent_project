"""config 域与头像的运行时根守护：打包态（BUNDLE_ROOT≠PROJECT_ROOT）下
用户可改配置读写必须落可写根，头像读取须运行时根优先回退打包基线。

曾发生的缺陷（同榜单读写分裂一族）：
- 阵营配色（faction_colors.json）与模型价格（model_pricing.json）读写钉在
  BUNDLE_ROOT/config——frozen 下写入只读 _internal，升级被新包覆盖、用户
  自定义静默丢失，Program Files 安装下保存直接 PermissionError；
- 头像读取固定 BUNDLE_ROOT/images，crawler 下载写 PROJECT_ROOT/images——
  frozen 下新下载头像 UI 永不显示（env.py 旧注释登记的独立债）。

开发态两根相等无法暴露，故沿用 test_ranking_csv_runtime_root 的
monkeypatch 双根 + importlib.reload 范式模拟 frozen 分裂。注意不能 reload
env 本身（PROJECT_ROOT 由 __file__ 重算会覆盖补丁），pricing 常量定义在
env 内，改用"PROJECT_ROOT 派生"的静态契约守卫。
"""

from __future__ import annotations

import base64
import importlib
import inspect
from pathlib import Path

import pytest
from src.config import env

_RELOAD_MODULES = (
    "src.ui.shared.faction_colors",
)

# 1x1 透明 PNG：头像加载行为测试的真实可解码图片
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def split_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模拟 frozen 根分裂：bundle（只读基线）与 runtime（可写根）分离。"""
    bundle = tmp_path / "_internal"
    runtime = tmp_path / "runtime"
    bundle.mkdir()
    runtime.mkdir()
    monkeypatch.setattr(env, "BUNDLE_ROOT", bundle)
    monkeypatch.setattr(env, "PROJECT_ROOT", runtime)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))
    yield bundle, runtime
    monkeypatch.undo()
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))


def test_config_paths_follow_writable_root(split_roots) -> None:
    """阵营配色与模型价格的默认路径必须落可写根（含首启部署副本语义）。"""
    _bundle, runtime = split_roots
    from src.ui.shared.faction_colors import FACTION_COLORS_FILE

    assert FACTION_COLORS_FILE == runtime / "config" / "faction_colors.json"
    # DEFAULT_PRICING_FILE 定义于 env（模块加载时绑定、reload 会重算根，
    # 均无法分裂模拟），静态契约锁定其表达式必须 PROJECT_ROOT 派生
    pricing_def = next(
        line for line in inspect.getsource(env).splitlines()
        if line.startswith("DEFAULT_PRICING_FILE")
    )
    assert "PROJECT_ROOT" in pricing_def


def test_portrait_prefers_runtime_root_over_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp
) -> None:
    """crawler 下载到运行时根的新头像必须可被 UI 加载；仅在打包基线的回退可读。"""
    from src.ui.shared import portrait

    bundle = tmp_path / "bundle_images"
    runtime = tmp_path / "runtime_images"
    bundle.mkdir()
    runtime.mkdir()
    monkeypatch.setattr(portrait, "IMAGES_DIR", bundle, raising=False)
    monkeypatch.setattr(portrait, "IMAGES_OUTPUT_DIR", runtime, raising=False)
    portrait.load_portrait.cache_clear()

    # 仅运行时根有（crawler 新下载）：必须加载到
    (runtime / "新武将.png").write_bytes(_ONE_PIXEL_PNG)
    assert portrait.load_portrait("新武将", 96, 129) is not None

    # 仅打包基线有（随包头像）：回退仍可读，存量头像不受影响
    (bundle / "旧武将.png").write_bytes(_ONE_PIXEL_PNG)
    assert portrait.load_portrait("旧武将", 96, 129) is not None

    portrait.load_portrait.cache_clear()
