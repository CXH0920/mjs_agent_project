"""
名将杀 Agent - 配置管理

提供 .env 配置文件的解析、加载、保存功能，
以及 API 配置和运行时参数的获取。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / "config.env"

# ============================================================
# DeepSeek API 默认值
# ============================================================

DEFAULT_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"

# deepseek-v4-pro 定价（RMB / 百万 tokens）
PRICE_INPUT_PER_M = 3.0     # CNY3 / 百万输入 tokens（缓存未命中）
PRICE_OUTPUT_PER_M = 6.0    # CNY6 / 百万输出 tokens

# ============================================================
# 配置加载
# ============================================================

def parse_env_file(env_path=None):
    """解析标准 .env 格式文件

    支持 KEY=VALUE 格式，忽略空行和 # 注释行，自动去除值两侧的引号。

    Args:
        env_path: .env 文件路径，默认为项目根目录下的 config.env

    Returns:
        dict[str, str]: 解析出的键值对
    """
    if env_path is None:
        env_path = DEFAULT_ENV_FILE
    path = Path(env_path)
    if not path.exists():
        logger.debug(".env 文件不存在: %s，使用默认值", path)
        return {}

    result = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                result[key] = value
        logger.debug("已加载 .env 配置: %s (%d 项)", path, len(result))
        return result
    except Exception as e:
        logger.warning(".env 文件解析失败 %s: %s", path, e)
        return {}

def load_env_config(env_path=None):
    """从 .env 文件加载配置（统一小写键名，便于使用）

    将 config.env 中的大写 KEY 映射为小写键名供程序内部使用。
    若文件不存在或解析失败则返回空 dict。

    Args:
        env_path: .env 文件路径

    Returns:
        dict: 小写键名的配置 dict，如 {"api_key": "...", "api_url": "..."}
    """
    raw = parse_env_file(env_path)
    key_mapping = {
        "DEEPSEEK_API_KEY": "api_key",
        "DEEPSEEK_API_URL": "api_url",
        "DEEPSEEK_MODEL": "model",
        "REQUESTS_PER_MINUTE": "requests_per_minute",
        "HTTP_TIMEOUT": "http_timeout",
        "MAX_RETRIES": "max_retries",
        "LOG_LEVEL": "log_level",
        "LOG_TO_FILE": "log_to_file",
    }
    config = {}
    for env_key, cfg_key in key_mapping.items():
        if env_key in raw:
            value = raw[env_key]
            if cfg_key in ("requests_per_minute", "max_retries", "http_timeout"):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    logger.warning("配置 %s 值不是有效整数: %s，使用默认值", env_key, value)
                    continue
            config[cfg_key] = value
    return config

def get_api_config():
    """获取 API 配置（合并 config.env、环境变量、默认值）

    优先级：config.env > 环境变量 > 默认值

    Returns:
        {"api_key": str, "api_url": str, "model": str}
    """
    config = load_env_config()

    api_key = (
        config.get("api_key", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    )
    api_url = config.get("api_url", "") or DEFAULT_API_URL
    model = config.get("model", "") or DEFAULT_MODEL

    return {"api_key": api_key, "api_url": api_url, "model": model}

def get_runtime_params():
    """从 config.env 获取运行时参数（带默认值）

    Returns:
        {"requests_per_minute": int, "max_retries": int,
         "http_timeout": int, "log_level": str, "log_to_file": bool}
    """
    config = load_env_config()
    log_to_file = config.get("log_to_file", "true")
    return {
        "requests_per_minute": config.get("requests_per_minute", 30),
        "max_retries": config.get("max_retries", 3),
        "http_timeout": config.get("http_timeout", 300),
        "log_level": config.get("log_level", "INFO"),
        "log_to_file": log_to_file.lower() in ("true", "1", "yes"),
    }

# ============================================================
# 配置保存
# ============================================================

def save_env_file(env_path, data):
    """原子写入 .env 文件

    保留原文件中的注释行和已有无关配置，
    新增或更新指定配置项。

    Args:
        env_path: .env 文件路径
        data: 要写入的键值对
    """
    existing_keys = set()
    lines = []
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
            else:
                key = stripped.split("=")[0].strip() if "=" in stripped else ""
                if key not in data:
                    lines.append(line)
                if key:
                    existing_keys.add(key)

    for key in data:
        if key not in existing_keys:
            lines.append(f"{key}={data[key]}")

    data_copy = dict(data)
    result_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=")[0].strip()
            if key in data_copy:
                result_lines.append(f"{key}={data_copy.pop(key)}")
                continue
        result_lines.append(line)

    for key, value in data_copy.items():
        result_lines.append(f"{key}={value}")

    tmp_path = env_path.with_suffix(".env.tmp")
    tmp_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
    tmp_path.replace(env_path)
