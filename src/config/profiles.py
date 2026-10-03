"""名将杀 Agent - API 档案配置（api_profiles.json）

多供应商 API 档案的读取、归一化、写入、任务侧解析与旧配置迁移。
自 env.py 拆出（审计 G7：配置读取与持久化/迁移职责分离，env.py 是
fan-in 最高的模块，写入/迁移面收窄后读侧签名得以冻结）。
依赖单向指向 env.py 的路径常量与 .env 解析原语，禁止反向 import
（反向会构成循环，被 test_architecture 的 SCC 守护拦截）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.config.env import (
    DEFAULT_API_URL,
    DEFAULT_MODEL,
    DEFAULT_PROFILES_FILE,
    PROVIDER_PRESETS,
    load_env_config,
    parse_env_file,
)

logger = logging.getLogger(__name__)


# ============================================================
# 档案可用性判定与 API 配置解析
# ============================================================

def _usable_profile_config(profile: dict) -> dict | None:
    """档案是否可用：enabled + URL 非空 + 供应商 Key 语义。可用返回 config，否则 None。

    空 URL 不回退 DeepSeek 默认（BUG-5：跨供应商场景会把请求发错端点），视为无效跳过。
    空 Key 仅对 requires_key=False 的供应商（如 ollama）允许。
    """
    if not profile.get("enabled", True) or not profile.get("api_url"):
        return None
    provider = profile.get("provider", "deepseek")
    if PROVIDER_PRESETS.get(provider, {}).get("requires_key", True) and not profile.get("api_key"):
        return None
    return {
        "provider": provider,
        "api_key": profile.get("api_key", ""),
        "api_url": profile.get("api_url", ""),
        "model": profile.get("model", "") or DEFAULT_MODEL,
    }


def has_available_api_profile() -> bool:
    """是否存在可用 API 档案（enabled + URL 非空 + 供应商 Key 语义）。

    UI 展示层与生成链路共用的唯一判定（此前后端选择对话框曾复制一份，
    且误用 has_key 展示字段，与生成链路的 api_key 判定存在漂移风险）。
    """
    for profile in load_api_profiles()["profiles"]:
        if _usable_profile_config(profile) is not None:
            return True
    return False


def get_api_config():
    """获取 API 配置（启用档案优先，其次 config.env → 环境变量 → 默认值）

    取 api_profiles.json 中第一个 enabled 档案（同时只允许一个启用，
    启用即当前使用的 API）；档案文件存在但无启用档案（全停用/全删光）时，
    只回退环境变量 + 默认值，刻意不读 config.env 旧键，使"停用"真正生效
    （A1：避免旧 Key 静默回跑）；档案文件不存在（从未配置过档案）时才走
    _legacy_api_config 旧链。

    Returns:
        {"api_key": str, "api_url": str, "model": str}
    """
    for profile in load_api_profiles()["profiles"]:
        config = _usable_profile_config(profile)
        if config:
            return config
    # 档案体系已启用（文件存在）但无启用档案：仅环境变量 + 默认值，不读 config.env 旧键
    if Path(DEFAULT_PROFILES_FILE).exists():
        return _env_var_fallback()
    # 从未配置过档案：旧链兜底（config.env → 环境变量 → 默认值）
    return _legacy_api_config()


def _env_var_fallback() -> dict:
    """档案体系已启用但无可用默认时的兜底：仅环境变量 + 默认值。

    刻意不读 config.env 的 DEEPSEEK_* 键，使"停用/删光档案"语义生效；
    环境变量作为脚本/CI 注入路径的最后兜底（决策 3：长期保留）。
    """
    return {
        "provider": "deepseek",
        "api_key": os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""),
        "api_url": os.getenv("DEEPSEEK_API_URL", "") or DEFAULT_API_URL,
        "model": os.getenv("DEEPSEEK_MODEL", "") or DEFAULT_MODEL,
    }


def _legacy_api_config():
    """旧链兜底：config.env > 环境变量 > 默认值（无默认档案时使用）。"""
    config = load_env_config()

    api_key = (
        config.get("api_key", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    )
    api_url = config.get("api_url", "") or DEFAULT_API_URL
    model = config.get("model", "") or DEFAULT_MODEL

    return {"provider": "deepseek", "api_key": api_key, "api_url": api_url, "model": model}


# ============================================================
# 档案文件读写（api_profiles.json）
# ============================================================

def _as_bool(value, default: bool) -> bool:
    """宽容布尔转换：None 用 default，bool 原值，字符串按 true/1/yes 判定，其余按真值。

    使 default 对 null/缺失值生效（A3：旧实现 None 落到 bool(None)=False，
    令 enabled:null 被误判为停用）。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _normalize_profiles(profiles) -> list[dict]:
    """加载/保存前的容错归一化：跳过非法项、补默认字段、修正名称重复、启用互斥。

    启用互斥语义：同时至多一个 enabled=true（启用即当前使用的 API）。
    多个 enabled 时保留第一个、其余置 false（打 warning），保证数据层面互斥，
    打开配置即生效——避免历史文件多 enabled 导致界面显示多个启用。
    is_default 字段已废弃，旧文件中的 is_default 被静默丢弃。
    """
    result: list[dict] = []
    seen: set[str] = set()
    enabled_seen = False
    for i, raw in enumerate(profiles):
        if not isinstance(raw, dict):
            logger.warning("API 档案第 %d 项不是对象，已跳过", i + 1)
            continue
        name = str(raw.get("name", "")).strip() or f"profile-{i + 1}"
        if name in seen:
            suffix = 2
            while f"{name}-{suffix}" in seen:
                suffix += 1
            logger.warning("API 档案名称重复: %s，已重命名为 %s-%d", name, name, suffix)
            name = f"{name}-{suffix}"
        enabled = _as_bool(raw.get("enabled"), True)
        if enabled:
            if enabled_seen:
                logger.warning("存在多个启用的 API 档案，仅保留第一个，其余停用: %s", name)
                enabled = False
            else:
                enabled_seen = True
        result.append({
            "name": name,
            "provider": str(raw.get("provider", "openai-compatible")).strip() or "openai-compatible",
            "api_key": str(raw.get("api_key", "")),
            "api_url": str(raw.get("api_url", "")).strip(),
            "model": str(raw.get("model", "")).strip(),
            "enabled": enabled,
            "note": str(raw.get("note", "")),
        })
        seen.add(name)
    return result


def load_api_profiles(profiles_path=None) -> dict:
    """读取 API 档案配置；文件不存在或损坏时返回空档案列表，不阻断调用方。

    Returns:
        {"version": int, "profiles": [{"name", "provider", "api_key", "api_url",
         "model", "enabled", "note"}, ...]}
    """
    path = Path(profiles_path or DEFAULT_PROFILES_FILE)
    default = {"version": 1, "profiles": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("profiles", []), list):
            raise ValueError("API 档案配置必须包含 profiles 列表")
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        logger.warning("API 档案配置不可用 %s: %s", path, error)
        return default
    return {
        "version": data.get("version", 1),
        "profiles": _normalize_profiles(data.get("profiles", [])),
    }


def save_api_profiles(data: dict, profiles_path=None) -> None:
    """原子写入 API 档案配置（UTF-8 无 BOM、LF、indent=2），保存前归一化（启用互斥/名称去重）。

    profiles 为空时不写空文件（已存在则删除）——让"删光档案→保存"回到"从未配置"
    状态，get_api_config 走 _legacy_api_config 读 config.env 旧键兜底（BUG-4 修复），
    避免空文件被误判为"仍在档案体系"导致旧 Key 静默旁路。
    """
    path = Path(profiles_path or DEFAULT_PROFILES_FILE)
    if not isinstance(data, dict):
        data = {}
    profiles = _normalize_profiles(data.get("profiles", []))
    if not profiles:
        # 空档案：不落盘空文件；已有文件则删除，保持"从未配置档案"状态
        if path.exists():
            try:
                path.unlink()
            except OSError as error:
                logger.warning("删除空档案文件失败 %s: %s", path, error)
        return
    payload = {
        "version": int(data.get("version", 1)),
        "profiles": profiles,
    }
    # 函数内导入：config 是被 src.data 各仓库依赖的底层包，
    # 模块级导入 src.data.json_repository 会触发 src.data.__init__ 循环初始化
    from src.data.json_repository import atomic_write_text

    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content)


def list_api_profiles() -> list[dict]:
    """返回供 UI 展示的档案列表；api_key 一律以 has_key 标记代替，不回显明文。"""
    result = []
    for p in load_api_profiles()["profiles"]:
        result.append({
            "name": p["name"],
            "provider": p["provider"],
            "has_key": bool(p.get("api_key")),
            "api_url": p["api_url"],
            "model": p["model"],
            "enabled": p["enabled"],
            "note": p["note"],
        })
    return result


def get_api_profile(name: str) -> dict | None:
    """按名称取完整档案（含 api_key），仅供任务解析路径使用，不得进入日志/UI。"""
    if not name:
        return None
    for p in load_api_profiles()["profiles"]:
        if p["name"] == name:
            return dict(p)
    return None


def resolve_api_config(name: str | None = None) -> dict:
    """任务侧唯一 API 解析入口。

    - name 指定且启用：取该档案三件套。
    - name 指定但不存在/停用：warning 并回退默认解析。
    - name 为空：走 get_api_config()（默认档案 → 旧链兜底）。

    Returns:
        {"api_key": str, "api_url": str, "model": str}
    """
    if name:
        profile = get_api_profile(name)
        config = _usable_profile_config(profile) if profile else None
        if config:
            return config
        logger.warning("API 档案不存在或已停用: %s，回退默认配置", name)
    return get_api_config()


def migrate_legacy_api_config(env_path=None, profiles_path=None) -> bool:
    """把旧 config.env 的 DEEPSEEK_* 三件套迁移为默认档案（幂等）。

    档案文件已存在或三件套全空时不迁移，避免覆盖用户配置。

    Returns:
        bool: 本次是否实际创建了档案
    """
    path = Path(profiles_path or DEFAULT_PROFILES_FILE)
    if path.exists():
        return False
    legacy = parse_env_file(env_path)
    api_key = legacy.get("DEEPSEEK_API_KEY", "")
    api_url = legacy.get("DEEPSEEK_API_URL", "")
    model = legacy.get("DEEPSEEK_MODEL", "")
    if not any((api_key, api_url, model)):
        return False
    save_api_profiles(
        {
            "version": 1,
            "profiles": [{
                "name": "deepseek-main",
                "provider": "deepseek",
                "api_key": api_key,
                "api_url": api_url or DEFAULT_API_URL,
                "model": model or DEFAULT_MODEL,
                "enabled": True,
                "note": "由旧配置自动迁移",
            }],
        },
        profiles_path=path,
    )
    logger.info("已将旧 API 配置迁移为默认档案 deepseek-main: %s", path)
    return True
