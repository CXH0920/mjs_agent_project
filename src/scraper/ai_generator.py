"""
名将杀 Agent - AI 批量生成器

封装 DeepSeek API 调用、重试、限速、JSON 提取和 Pydantic 校验。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from src.scraper.prompt_utils import (
    load_prompt,
    build_guide_prompt,
    build_synergy_prompt,
)
from src.scraper.json_extract import extract_json
from src.scraper.ai_utils import (
    convert_ids_to_int,
    validate_guide,
    validate_synergy,
)

logger = logging.getLogger(__name__)

# 路径常量（相对 PROJECT_ROOT 定位 Prompt 文件）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_DIR = PROJECT_ROOT / "docs" / "prompts"
GUIDE_PROMPT_FILE = PROMPT_DIR / "hero_guide.md"
SYNERGY_PROMPT_FILE = PROMPT_DIR / "synergy_score.md"


class AIBatchGenerator:
    """AI 批量生成器

    封装 DeepSeek API 调用、重试、限速、JSON 提取和 Pydantic 校验。
    保持与 ai_batch.py 的向后兼容接口（generate_guide, generate_synergy, close）。
    """

    def __init__(
        self,
        api_key: str,
        api_url: str | None = None,
        model: str | None = None,
        requests_per_minute: int = 30,
        max_retries: int = 3,
        http_timeout: int = 300,
    ):
        if not api_key:
            raise ValueError("api_key 不能为空")

        self.api_key = api_key
        self.api_url = api_url or "https://api.deepseek.com/v1/chat/completions"
        self.model = model or "deepseek-v4-pro"
        self.max_retries = max_retries
        self.http_timeout = http_timeout
        self._client = httpx.Client(timeout=http_timeout)

        # 限速控制
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self._last_request_time = 0.0

    def _call_api(self, messages: list[dict], temperature: float = 0.7) -> dict | None:
        """调用 DeepSeek API，带指数退避重试"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 8192,
        }

        for attempt in range(1, self.max_retries + 1):
            elapsed = time.time() - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)

            try:
                resp = self._client.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                )
                self._last_request_time = time.time()
                resp.raise_for_status()
                data = resp.json()
                return data
            except httpx.HTTPStatusError as e:
                logger.warning("API 返回错误 [%d/%d]: HTTP %s",
                               attempt, self.max_retries, e.response.status_code)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
            except Exception as e:
                logger.warning("API 请求异常 [%d/%d]: %s",
                               attempt, self.max_retries, e)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)

        logger.error("API 请求超过最大重试次数 %d", self.max_retries)
        return None

    # ---------------------------------------------------------------
    # 生成攻略
    # ---------------------------------------------------------------

    def generate_guide(self, hero: dict) -> tuple[dict | None, dict | None]:
        """为单个武将生成攻略"""
        system_prompt = load_prompt(GUIDE_PROMPT_FILE)
        if not system_prompt:
            logger.error("攻略 prompt 模板未找到: %s", GUIDE_PROMPT_FILE)
            return None, None

        user_prompt = build_guide_prompt(hero)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._call_api(messages, temperature=0.7)
        if not response:
            return None, None

        usage = response.get("usage", {})
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        finish_reason = response.get("choices", [{}])[0].get("finish_reason", "")

        if finish_reason == "length":
            logger.warning("API 输出被 max_tokens 截断（finish_reason=length），"
                           "可增加 max_tokens 或优化 prompt 减少输出量")

        if not content:
            logger.warning("API 返回内容为空")
            return None, usage

        try:
            raw = extract_json(content)
        except ValueError as e:
            if finish_reason == "length":
                logger.warning("JSON 提取失败，且 API 输出已被 max_tokens 截断，"
                               "很可能因输出不完整导致")
            else:
                logger.warning("JSON 提取失败: %s", e)
            return None, usage

        raw["hero_id"] = hero.get("id", 0)
        convert_ids_to_int(raw, ["synergizes_with"])

        result = validate_guide(raw)
        if result is None:
            logger.warning("攻略 Pydantic 校验失败")
            logger.debug("异常数据: %s", json.dumps(raw, ensure_ascii=False))
            return None, usage
        return result, usage

    # ---------------------------------------------------------------
    # 生成相性评分
    # ---------------------------------------------------------------

    def generate_synergy(self, hero_a: dict, hero_b: dict) -> tuple[dict | None, dict | None]:
        """为武将对生成相性评分"""
        system_prompt = load_prompt(SYNERGY_PROMPT_FILE)
        if not system_prompt:
            logger.error("相性 prompt 模板未找到: %s", SYNERGY_PROMPT_FILE)
            return None, None

        user_prompt = build_synergy_prompt(hero_a, hero_b)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._call_api(messages, temperature=0.7)
        if not response:
            return None, None

        usage = response.get("usage", {})
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        finish_reason = response.get("choices", [{}])[0].get("finish_reason", "")

        if finish_reason == "length":
            logger.warning("API 输出被 max_tokens 截断（finish_reason=length），"
                           "可增加 max_tokens 或优化 prompt 减少输出量")

        if not content:
            logger.warning("API 返回内容为空")
            return None, usage

        try:
            raw = extract_json(content)
        except ValueError as e:
            if finish_reason == "length":
                logger.warning("JSON 提取失败，且 API 输出已被 max_tokens 截断，"
                               "很可能因输出不完整导致")
            else:
                logger.warning("JSON 提取失败: %s", e)
            return None, usage

        raw["hero_a_id"] = hero_a.get("id", 0)
        raw["hero_b_id"] = hero_b.get("id", 0)

        # 兼容旧 prompt 中的 combat_synergy 字段
        if "combat_synergy" in raw and "combo_ceiling" not in raw:
            raw["combo_ceiling"] = raw.pop("combat_synergy")

        result = validate_synergy(raw)
        if result is None:
            logger.warning("相性 Pydantic 校验失败")
            logger.debug("异常数据: %s", json.dumps(raw, ensure_ascii=False))
            return None, usage
        return result, usage

    def close(self):
        """关闭 HTTP 客户端"""
        self._client.close()
