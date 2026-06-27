"""
名将杀 Agent - AI 批量生成器

封装 DeepSeek API 调用、重试、限速、JSON 提取和 Pydantic 校验。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from src.scraper.ai_utils import load_prompt

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
            "max_tokens": 4096,
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

    @staticmethod
    def _extract_json(text: str) -> dict:
        """从 API 返回文本中提取 JSON（raw_decode 容忍尾部多余字符 + 修复未转义字符）"""
        text = text.strip()

        def _repair_strings(s: str) -> str:
            """修复 JSON 字符串值内的字面换行"""
            result = []
            in_string = False
            i = 0
            while i < len(s):
                c = s[i]
                if c == '\\' and in_string:
                    result.append(c)
                    if i + 1 < len(s):
                        result.append(s[i + 1])
                        i += 2
                    else:
                        i += 1
                    continue
                if c == '"':
                    in_string = not in_string
                    result.append(c)
                    i += 1
                    continue
                if in_string and c in '\r\n':
                    result.append('\\n')
                    i += 1
                    continue
                result.append(c)
                i += 1
            return ''.join(result)

        def _raw_parse(s: str) -> dict | None:
            try:
                decoder = json.JSONDecoder()
                obj, _ = decoder.raw_decode(s)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
            return None

        def _try_all(candidates: list[str]) -> dict | None:
            for c in candidates:
                result = _raw_parse(c)
                if result:
                    return result
                repaired = _repair_strings(c)
                if repaired != c:
                    result = _raw_parse(repaired)
                    if result:
                        return result
            return None

        # 1. 直接全文
        result = _try_all([text])
        if result:
            return result

        # 2. 从 ```json 或 ``` 代码块提取
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            result = _try_all([m.group(1).strip()])
            if result:
                return result

        # 3. 通过 --- 分隔线提取最后一段
        last_sep = text.rfind("\n---\n")
        if last_sep < 0:
            last_sep = text.rfind("\n---")
        if last_sep >= 0:
            result = _try_all([text[last_sep + 5:].strip()])
            if result:
                return result

        # 4. 找到第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            result = _try_all([text[start:end + 1]])
            if result:
                return result

        raise ValueError(f"无法从响应中提取 JSON:\n{text[:500]}")

    # ---------------------------------------------------------------
    # Prompt 构建
    # ---------------------------------------------------------------

    def _build_guide_prompt(self, hero: dict) -> str:
        """构建单个武将的攻略 prompt"""
        lines = [f"武将: {hero.get('name', '')}"]
        lines.append(f"势力: {hero.get('faction', '')}")
        lines.append(f"定位: {hero.get('position', '')}")
        lines.append(f"体力: {hero.get('max_hp', 4)}  手牌: {hero.get('max_hand', 4)}")
        lines.append(f"性别: {hero.get('gender', '男')}")
        lines.append(f"难度: {hero.get('difficulty', 2)}")
        if hero.get("skills"):
            lines.append("")
            lines.append("技能:")
            for sk in hero["skills"]:
                lines.append(f"  - {sk.get('name', '')}: {sk.get('description', '')}")
        return "\n".join(lines)

    def _build_synergy_prompt(self, hero_a: dict, hero_b: dict) -> str:
        """构建武将对的相性评分 prompt"""
        def hero_block(label: str, h: dict) -> list[str]:
            lines = [f"## {label}: {h.get('name', '')}"]
            lines.append(f"  势力: {h.get('faction', '')}")
            lines.append(f"  定位: {h.get('position', '')}")
            lines.append(f"  体力/手牌: {h.get('max_hp', 4)}/{h.get('max_hand', 4)}")
            if h.get("skills"):
                lines.append("  技能:")
                for sk in h["skills"]:
                    lines.append(f"    - {sk.get('name', '')}: {sk.get('description', '')}")
            return lines

        lines = []
        lines.extend(hero_block("武将 A", hero_a))
        lines.append("")
        lines.extend(hero_block("武将 B", hero_b))
        return "\n".join(lines)

    # ---------------------------------------------------------------
    # 工具方法
    # ---------------------------------------------------------------

    @staticmethod
    def _convert_ids_to_int(data: dict, fields: list[str]) -> dict:
        """将指定字段中的 ID 元素统一转为 int"""
        for field in fields:
            if field in data and isinstance(data[field], list):
                data[field] = [int(v) for v in data[field]]
        return data

    # ---------------------------------------------------------------
    # Pydantic 校验
    # ---------------------------------------------------------------

    @staticmethod
    def _validate_guide(data: dict) -> dict | None:
        """通过 Pydantic HeroGuide 模型校验攻略数据"""
        try:
            from src.data.models import HeroGuide
            validated = HeroGuide.model_validate(data)
            return validated.model_dump(mode="json")
        except Exception:
            return None

    @staticmethod
    def _validate_synergy(data: dict) -> dict | None:
        """通过 Pydantic SynergyScore 模型校验相性数据"""
        try:
            from src.data.models import SynergyScore
            validated = SynergyScore.model_validate(data)
            return validated.model_dump(mode="json")
        except Exception:
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

        user_prompt = self._build_guide_prompt(hero)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._call_api(messages, temperature=0.7)
        if not response:
            return None, None

        usage = response.get("usage", {})
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not content:
            logger.warning("API 返回内容为空")
            return None, usage

        try:
            raw = self._extract_json(content)
        except ValueError as e:
            logger.warning("JSON 提取失败: %s", e)
            return None, usage

        raw["hero_id"] = hero.get("id", 0)
        self._convert_ids_to_int(raw, ["counters", "synergizes_with"])

        result = self._validate_guide(raw)
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

        user_prompt = self._build_synergy_prompt(hero_a, hero_b)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._call_api(messages, temperature=0.7)
        if not response:
            return None, None

        usage = response.get("usage", {})
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not content:
            logger.warning("API 返回内容为空")
            return None, usage

        try:
            raw = self._extract_json(content)
        except ValueError as e:
            logger.warning("JSON 提取失败: %s", e)
            return None, usage

        raw["hero_a_id"] = hero_a.get("id", 0)
        raw["hero_b_id"] = hero_b.get("id", 0)

        # 兼容旧 prompt 中的 combat_synergy 字段
        if "combat_synergy" in raw and "combo_ceiling" not in raw:
            raw["combo_ceiling"] = raw.pop("combat_synergy")

        result = self._validate_synergy(raw)
        if result is None:
            logger.warning("相性 Pydantic 校验失败")
            logger.debug("异常数据: %s", json.dumps(raw, ensure_ascii=False))
            return None, usage
        return result, usage

    def close(self):
        """关闭 HTTP 客户端"""
        self._client.close()
