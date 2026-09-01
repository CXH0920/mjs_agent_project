"""
名将杀 Agent - AI 批量生成器

封装 DeepSeek API 调用、重试、限速、JSON 提取和 Pydantic 校验。
"""

from __future__ import annotations

import logging
import time
import httpx

from src.config.env import BUNDLE_ROOT, PROVIDER_PRESETS
from src.scraper.ai.prompt_utils import (
    load_prompt,
    build_guide_prompt,
    build_synergy_prompt,
)
from src.scraper.ai.json_extract import extract_json
from src.scraper.ai.utils import (
    convert_ids_to_int,
    has_required_guide_fields,
    has_required_synergy_fields,
    validate_guide,
    validate_synergy,
)

logger = logging.getLogger(__name__)

# 路径常量（相对 BUNDLE_ROOT 定位 Prompt 文件）
PROMPT_DIR = BUNDLE_ROOT / "docs" / "prompts"
GUIDE_PROMPT_FILE = PROMPT_DIR / "hero_guide.md"
SYNERGY_PROMPT_FILE = PROMPT_DIR / "synergy_score.md"
MAX_OUTPUT_TOKENS = 16_384
OUTPUT_BUDGET_EXHAUSTED_MESSAGE = "思考过程耗尽输出额度"

# 连接类异常：损坏 httpx.Client/连接池，重试前需重建 client，否则后续请求级联失败
# （RemoteProtocolError 后复用同 client 会抛 RuntimeError，#61）
_CONN_ERRORS = (
    httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError,
    httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout,
    httpx.WriteError, httpx.WriteTimeout, RuntimeError,
)

# 不可重试的 HTTP 状态：Key 配错/参数/权限问题重试只会白等退避（429 走限流退避、
# 408/5xx 属瞬时故障，均不在其列）
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 422})


def _read_completion_content(response: dict, max_tokens: int = MAX_OUTPUT_TOKENS) -> tuple[str | None, dict]:
    """只读取最终正文；思考过程不进入日志、持久化或界面链路。"""
    usage = response.get("usage", {})
    content = response.get("content", "")
    finish_reason = response.get("finish_reason", "")
    if finish_reason == "length" or not isinstance(content, str) or not content.strip():
        logger.error("%s（max_tokens=%d）", OUTPUT_BUDGET_EXHAUSTED_MESSAGE, max_tokens)
        return None, usage
    return content, usage


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
        provider: str = "deepseek",
        requests_per_minute: int = 30,
        max_retries: int = 3,
        http_timeout: int = 300,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ):
        # 供应商语义 Key 校验：requires_key=False（如 ollama 本地服务）允许空 Key
        if PROVIDER_PRESETS.get(provider, {}).get("requires_key", True) and not api_key:
            raise ValueError(f"{provider} 供应商要求填写 API Key")

        self.api_key = api_key
        self.provider = provider
        self.api_url = api_url or "https://api.deepseek.com/v1/chat/completions"
        self.model = model or "deepseek-v4-pro"
        self.max_retries = max_retries
        self.http_timeout = http_timeout
        # 思考+正文共享的输出额度；思考型模型经 config.env MAX_OUTPUT_TOKENS 按供应商上限调大
        self.max_output_tokens = max_output_tokens
        self._client = httpx.Client(timeout=http_timeout)

        # 限速控制
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self._last_request_time = 0.0
        # 取消标志：reject/面板销毁时置 True，重试循环在下次循环开头退出，避免 in-flight close 后继续 post
        self._cancelled = False

    def cancel(self) -> None:
        """请求中断：重试循环将在下次循环开头退出。

        正在进行的 HTTP 请求靠超时退出（cancel 不打断 in-flight 请求）。
        """
        self._cancelled = True

    def complete(self, messages: list[dict], temperature: float = 0.7) -> dict | None:
        """公开的对话补全接口（供业务层调用，内部复用 _call_api）。"""
        return self._call_api(messages, temperature=temperature)

    def _request_content(
        self, messages: list[dict], temperature: float, label: str,
    ) -> tuple[str | None, dict | None]:
        """请求补全并读取正文；思考过程耗尽输出额度时重试至多 max_retries 次。

        思考长度随采样波动，重试通常能让正文挤进额度；每次重试都输出 [重试]
        进度行，避免子进程长时间静默让用户以为卡死。
        """
        content: str | None = None
        usage: dict | None = None
        for attempt in range(1, self.max_retries + 1):
            response = self._call_api(messages, temperature=temperature)
            if not response:
                return None, usage
            content, attempt_usage = _read_completion_content(response, self.max_output_tokens)
            usage = attempt_usage or usage
            self._log_usage(label, usage or {})
            if content is not None:
                return content, usage
            if attempt < self.max_retries:
                wait = 2 ** attempt
                print(
                    f"  [重试] {OUTPUT_BUDGET_EXHAUSTED_MESSAGE}，"
                    f"第 {attempt}/{self.max_retries} 次，{wait} 秒后重试",
                    flush=True,
                )
                time.sleep(wait)
        return None, usage

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
            "max_tokens": self.max_output_tokens,
        }
        # thinking 是 DeepSeek 私有参数，非 DeepSeek 端点会因未知字段返回 400
        if self.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}

        for attempt in range(1, self.max_retries + 1):
            if self._cancelled:
                logger.info("API 请求已取消")
                return None
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
                choices = data.get("choices", [])
                choice = choices[0] if choices and isinstance(choices[0], dict) else {}
                message = choice.get("message", {})
                if not isinstance(message, dict):
                    message = {}
                usage = data.get("usage", {})
                return {
                    "content": message.get("content", ""),
                    "finish_reason": choice.get("finish_reason", ""),
                    "usage": usage if isinstance(usage, dict) else {},
                }
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in _NON_RETRYABLE_STATUS:
                    # Key 配错/参数错误重试无意义：立即失败，避免每武将白等退避
                    logger.error("API 请求不可重试: HTTP %s（不再重试）", status)
                    raise
                logger.warning("API 返回错误 [%d/%d]: HTTP %s",
                               attempt, self.max_retries, status)
                if attempt < self.max_retries:
                    wait = self._retry_wait(status, attempt, e.response.headers)
                    print(f"  [重试] HTTP {status}，第 {attempt}/{self.max_retries} 次，{wait} 秒后重试", flush=True)
                    time.sleep(wait)
            except Exception as e:
                logger.warning("API 请求异常 [%d/%d]: %s",
                               attempt, self.max_retries, type(e).__name__)
                # 连接类异常损坏 client/连接池，重建以避免后续重试级联失败
                # （RemoteProtocolError 后复用同 client 会抛 RuntimeError）
                if isinstance(e, _CONN_ERRORS):
                    try:
                        self._client.close()
                    except Exception as error:
                        logger.debug("旧 client 关闭失败: %s", error)
                    self._client = httpx.Client(timeout=self.http_timeout)
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    print(f"  [重试] {type(e).__name__}，第 {attempt}/{self.max_retries} 次，{wait} 秒后重试", flush=True)
                    time.sleep(wait)

        logger.error("API 请求超过最大重试次数 %d", self.max_retries)
        return None

    @staticmethod
    def _retry_wait(status: int, attempt: int, headers) -> int:
        """429 限流优先读 Retry-After，否则指数退避；429 下限 3s。"""
        if status != 429:
            return 2 ** attempt
        retry_after = headers.get("retry-after", "").strip()
        if retry_after.isdigit():
            return max(min(int(retry_after), 30), 3)
        return max(5 * attempt, 3)  # 5/10/15s，比通用退避更长

    # ---------------------------------------------------------------
    # 生成攻略
    # ---------------------------------------------------------------

    def _log_usage(self, label: str, usage: dict) -> None:
        """记录单次 API token 用量（拆分 reasoning/content，定位思考挤占正文预算）。"""
        if not usage:
            return
        prompt = usage.get("prompt_tokens", 0)
        comp = usage.get("completion_tokens", 0)
        details = usage.get("completion_tokens_details") or {}
        reason = details.get("reasoning_tokens") or 0
        logger.info("[%s] token: prompt=%d completion=%d (reasoning=%d, content=%d)",
                    label, prompt, comp, reason, comp - reason)

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

        content, usage = self._request_content(messages, temperature=0.7, label=hero.get("name", ""))
        if content is None:
            return None, usage

        try:
            raw = extract_json(content)
        except ValueError as e:
            logger.warning("JSON 提取失败: %s", e)
            return None, usage

        raw["hero_id"] = hero.get("id", 0)
        convert_ids_to_int(raw, ["synergizes_with"])
        if not has_required_guide_fields(raw):
            logger.warning("攻略必填字段缺失: %s", sorted(raw))
            return None, usage

        result = validate_guide(raw)
        if result is None:
            logger.warning("攻略 Pydantic 校验失败")
            logger.debug("攻略校验失败字段: %s", sorted(raw))
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

        label = f"{hero_a.get('name','')}/{hero_b.get('name','')}"
        content, usage = self._request_content(messages, temperature=0.3, label=label)
        if content is None:
            return None, usage

        try:
            raw = extract_json(content)
        except ValueError as e:
            logger.warning("JSON 提取失败: %s", e)
            return None, usage

        raw["hero_a_id"] = hero_a.get("id", 0)
        raw["hero_b_id"] = hero_b.get("id", 0)

        # 兼容旧 prompt 中的 combat_synergy 字段
        if "combat_synergy" in raw and "combo_ceiling" not in raw:
            raw["combo_ceiling"] = raw.pop("combat_synergy")

        if not has_required_synergy_fields(raw):
            logger.warning("相性必填字段缺失: %s", sorted(raw))
            return None, usage

        result = validate_synergy(raw)
        if result is None:
            logger.warning("相性 Pydantic 校验失败")
            logger.debug("相性校验失败字段: %s", sorted(raw))
            return None, usage
        return result, usage

    def close(self):
        """关闭 HTTP 客户端"""
        self._client.close()
