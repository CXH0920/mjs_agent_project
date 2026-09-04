# 模块：AI 批量生成

> 对应目录：`src/scraper/ai/`
> 职责：通过 AI（DeepSeek API 或浏览器自动化）批量生成武将攻略和相性评分

---

## 一、模块职责

本模块是项目的**智能内容生成引擎**：接收武将数据，调用 AI 模型生成攻略或相性评分，从 AI 回复中提取 JSON，经 Pydantic 校验后持久化到数据文件。

核心能力：
- **双模式生成** — API 模式（httpx 直连 DeepSeek）和浏览器模式（Playwright + Edge 自动化操作 DeepSeek 网页版）
- **五种生成模式** — 全量攻略、全量相性、指定配对（2~8 武将 × itertools.combinations）、选定武将 × 全体、实战配队清单（显式 id 配对列表）
- **JSON 提取** — 从 AI 的不规范回复中宽容提取 JSON，支持 4 种回退策略
- **分批原子提交** — 每累计 10 条攻略或相性通过校验的结果即原子写入正式 JSON；失败项保留对应旧数据
- **RAG 语料增强** — 攻略与相性生成默认注入官方规则语料（ChromaDB 向量检索 + 关键词 RRF）；支持「RAG 语料增强（推荐）/ 经典模式（无 RAG 注入）」双版本，运行时异常自动降级为经典模式
- **两层重试机制** — HTTP 层指数退避（含 429 限流退避）+ 输出额度层重试（思考过程耗尽正文额度时自动重试）

---

## 二、文件结构

```
src/scraper/
├── ai_batch.py              # 兼容 CLI 入口
└── ai/
    ├── __init__.py          # 空包标记
    ├── batch.py             # 参数解析、配置加载与任务分发
    ├── api_generator.py     # API 调用核心（含两层重试）
    ├── browser_generator.py # 浏览器模式生成器
    ├── browser_session.py   # DeepSeek 页面会话（Playwright 生命周期）
    ├── generation.py        # 五种生成编排函数
    ├── prompt_utils.py      # Prompt 构建与成本估算
    ├── rag_prompt.py        # RAG 语料检索与注入（攻略/相性，含降级提示）
    ├── rule_summary.py      # 核心规则摘要/卡牌体系段加载（RAG 兜底注入）
    ├── json_extract.py      # AI 回复 JSON 提取
    └── utils.py             # 数据加载、校验与原子保存
```

### 模块间调用关系

```
ai_batch.py (兼容 CLI) -> ai/batch.py
 ├── 选择生成器: AIBatchGenerator (api) / PlaywrightGenerator (browser)
 └── generation.py → 根据参数分发到:
      ├── run_guide_generation()
      ├── run_synergy_generation()
      ├── run_synergy_pair_generation()
      ├── run_synergy_single_generation()
      └── run_synergy_list_generation()
```

---

## 三、核心逻辑

### 3.1 双模式生成器

两个生成器满足统一接口，可互换：

```
generate_guide(hero)    → (dict|None, usage|None)
generate_synergy(a, b)  → (dict|None, usage|None)
close()                 → None
```

| 特性 | API 模式 | 浏览器模式 |
|------|----------|------------|
| 类名 | `AIBatchGenerator` | `PlaywrightGenerator` |
| 数据源 | DeepSeek API（默认 provider=deepseek） | DeepSeek 网页版（chat.deepseek.com） |
| 限速 | RPM 控制（默认 30 req/min）+ 指数退避 | 每次成功生成后，在下一次请求前随机休息 60-180 秒 |
| Token 统计 | 支持（拆分 reasoning/content 记录） | 返回 None |
| 输出上限 | 默认 16384 token（可经 `MAX_OUTPUT_TOKENS` 配置调大） | 无上限限制（读取网页最终回复） |
| 输出处理 | 关闭思考（`thinking.type=disabled`）；仅解析最终 `content`；思考耗尽正文额度时自动重试 | 读取网页最终回复；JSON 提取失败时发送纠正消息重试 |
| 成本估算 | 支持 dry-run | 不支持 |
| 必备条件 | API Key（ollama 本地服务除外） | 已登录的 Edge 浏览器 |
| 取消支持 | `cancel()` 方法（置标志位，重试循环下次循环开头退出） | 无 |
| RAG 预算 | `RAG_PROMPT_CHARS`（默认 6000 字符） | `RAG_BROWSER_PROMPT_CHARS`（默认 3000 字符） |

浏览器模式按职责拆为两层：`PlaywrightGenerator` 负责提示词、JSON 提取、ID 转换和 Pydantic 校验；`DeepSeekBrowserSession` 负责 Edge/Playwright 生命周期、登录等待、页面诊断、消息发送和流式回复稳定检测。`PlaywrightGenerator._send_and_wait()` 保留为兼容委托入口，不再直接操作页面。

每个武将/武将对都重发完整 system prompt + 数据（与 API 模式对齐），避免网页版会话中首轮格式指令在后续轮次衰减导致输出偏离 JSON；另在消息末尾追加 `GUIDE_FORMAT_REMINDER` / `SYNERGY_FORMAT_REMINDER` 利用"最近指令权重最高"特性稳住 JSON 输出结构。

### 3.2 AIBatchGenerator（API 模式）

```
构造函数 → 创建 httpx.Client() + 初始化限速器
  │
generate_guide(hero)
  ├── load_prompt("docs/prompts/hero_guide.md") → system_prompt
  ├── build_guide_prompt(hero)                  → user_prompt
  │   ├── build_rag_context(hero)               → RAG 语料区块（若启用）
  │   ├── _skill_lines()                        → 技能行（已注入语料块时指针化省 token）
  │   └── load_card_system()                    → 卡牌体系段兜底（防牌名串味）
  ├── _request_content(messages=[system, user])
  │   ├── _call_api(messages, temperature)
  │   │   ├── 限速检查（距上次请求不足 60/RPM 秒则 sleep）
  │   │   ├── POST /v1/chat/completions（thinking.type=disabled，max_tokens=max_output_tokens）
  │   │   ├── 解析 response：content / finish_reason / usage
  │   │   ├── 400/401/403/404/422 → 立即失败（Key/参数问题重试无意义）
  │   │   ├── 429 → 优先读 Retry-After 头（3-30s），无则 5*attempt（5/10/15s）
  │   │   ├── 其他 5xx/网络错误 → 2^attempt（2/4/8s）
  │   │   └── 连接类异常 → 重建 httpx.Client 后重试（避免级联失败）
  │   ├── _read_completion_content(response)
  │   │   ├── finish_reason="length" 或 content 为空 → 返回 None
  │   │   └── 否则返回 (content, usage)
  │   ├── _log_usage(label, usage)             → 记录 prompt/completion 与 reasoning/content 拆分
  │   └── content=None 且 attempt < max_retries → 输出 [重试] 思考过程耗尽输出额度
  │       （思考长度随采样波动，重试通常能让正文挤进额度；每次重试向 stdout 输出 [重试] 进度行）
  ├── extract_json(content)                     → raw dict
  ├── inject hero_id / convert_ids_to_int(synergizes_with)
  ├── has_required_guide_fields(raw)            → 必填字段 + 占位符/过短正文预检
  └── validate_guide(raw) → Pydantic 校验
```

**两层重试的区别：**

| 层级 | 方法 | 触发条件 | 退避策略 | 说明 |
|------|------|----------|----------|------|
| HTTP 层 | `_call_api()` | HTTP 错误（429/5xx）、网络异常、连接超时 | 429：Retry-After 头或 5/10/15s；其他：2^attempt（2/4/8s） | 不可重试状态（400/401/403/404/422）立即失败 |
| 额度层 | `_request_content()` | `finish_reason="length"` 或 content 为空（思考过程耗尽正文额度） | 2^attempt（2/4/8s） | 思考长度随采样波动，重试通常能让正文挤进额度 |

### 3.3 JSON 提取策略

AI 的回复格式高度不可控，`extract_json()` 按优先级依次尝试 4 种策略：

| 优先级 | 策略 | 说明 |
|--------|------|------|
| 1 | 全文 raw_decode | 直接解析整个字符串，容忍尾部多余字符 |
| 2 | ```json 代码块 | 正则提取 Markdown 包裹的 JSON |
| 3 | --- 分隔线后 | `rfind("\n---\n")` 或 `rfind("\n---")` 取最后一段（切片长度与匹配的分隔符等长） |
| 4 | { 到 } 区间 | `find("{")` ~ `rfind("}")` 截取兜底 |

每次尝试前调用 `_repair_strings()` 修复字符串值内的字面换行和未转义引号。修复使用 `in_string` 状态机逐字符扫描，跳过转义序列（`\\` + 后一个字符），避免全局替换破坏非字符串内容。

浏览器模式在 JSON 提取失败时会发送 `GUIDE_RETRY_PROMPT` / `SYNERGY_RETRY_PROMPT` 纠正消息重试（相当于代码版"重新生成"），而非直接失败。

### 3.4 相性配对（多武将组合）

`generation.py` 中的 `run_synergy_pair_generation()` 支持选择 2~8 个武将，用 `itertools.combinations` 遍历所有 C(N,2) 组合。`run_synergy_list_generation()` 支持显式 id 配对列表（`[{"hero_a_id": int, "hero_b_id": int}, ...]`），按全量武将表解析，解析失败的配对记为失败项。

四个相性生成循环共享 `_run_synergy_pairs()` 核心函数，通过参数化协议行展示名和失败项格式适配不同模式：

```python
for idx, (ha, hb) in enumerate(pairs, start=1):
    pair_key = tuple(sorted([ha["id"], hb["id"]]))
    if skip_existing and pair_key in existing_synergy_keys:
        result_summary.skipped += 1
        print(f"  [{idx}/{total}] {label_of(ha, hb)} SKIP（已有相性）")
        continue
    print(f"  [{idx}/{total}] {label_of(ha, hb)} START")
    generated, usage = generator.generate_synergy(ha, hb)
    if generated:
        working_synergies[pair_key] = _with_synergy_updated_date(generated)
        print(f"  [{idx}/{total}] {label_of(ha, hb)} OK - 评分: {generated.get('score', '?')}")
    else:
        result_summary.failed_items.append(fail_label_of(ha, hb))
        print(f"  [{idx}/{total}] {label_of(ha, hb)} FAIL")
```

**要点：** 每组配对开始时输出 `[i/total] ... START`，仅展示当前请求而不推进进度。AI 结果通过校验为 `OK`、校验失败为 `FAIL` 或确认已有数据为 `SKIP` 后，才输出对应终态行并推进 UI 进度条。浏览器模式的随机休息在下一组请求开始前执行，因此 N 组实际生成只休息 N-1 次，最后一组校验通过后直接保存和结束。指定配对默认跳过已有相性；当 UI 明确传入 `--update` 时才重新生成并覆盖已有相性。全量模式使用 `score_threshold` 过滤：评分低于下限的配对从 `working_synergies` 中移除旧记录。

### 3.5 任务结果与提交边界

每个编排函数返回 `GenerationResult`，其中包含 token 用量、完成数、跳过数、失败项和提交状态。CLI 只根据该结构化结果决定退出码：任一失败项都会以非零退出，API 与浏览器模式的规则一致；此前已成功的批次不回滚。

单个生成任务每累计 10 条攻略或相性校验成功，即通过临时文件 `replace()` 原子提交到正式 `guides.json` / `synergies.json`；任务结束时会提交不足一批的成功结果。任一失败项只保留原有对应记录，不回滚已成功批次。用户在进度对话框选择中止时会终止子进程，已提交批次保留，正在处理且尚未提交的数据不会写入。浏览器模式没有 token usage，不会因缺少 usage 被误判为失败，也不要求 API Key。

**相性日期标记：** 每次校验成功的相性结果通过 `_with_synergy_updated_date()` 写入 `last_updated` 字段（本次生成日期），用于追踪数据新鲜度。

### 3.6 RAG 语料注入（攻略 / 相性）

`src/scraper/ai/rag_prompt.py` 负责把官方规则语料检索结果格式化为 prompt 区块；任何异常一律降级为空串，不影响生成链路。降级原因记录在模块级 `degraded_reason` 变量中，由生成循环通过 `take_degraded_reason()` 消费一次并在 stdout 输出 `[RAG] 语料不可用，本次已降级为经典模式（原因）`，进度窗口可见。

- **攻略 `build_rag_context(hero, max_chars=None)`**：先 `hero_blocks()` 取该武将全部语料块（hero 技能/结算 + guide 攻略 + classification），再跨类检索召回 combo 块（无 hero，按 `heroes` 列表过滤，含目标武将才保留）与规则/卡牌等；只注入目标武将相关的块。查询串 = 武将名 + 技能名 + 技能描述中命中的 `retriever.KEYWORDS` 机制词（去重、上限 20）。
- **相性 `build_synergy_rag_context(hero_a, hero_b, max_chars=None)`**：
  1. 第一段确定性召回双方武将全部语料块（`hero_blocks()`，含 guide/classification）；
  2. 第二段双 query 融合（不带武将过滤）：query1 = 双方武将名（找基础信息），query2 = 技能名 + 机制词（找联动效果），各取半数 `top_k` 去重合并；
  3. post-filter：`metadata.hero` 存在且不属于两名目标武将的块丢弃；combo 块（无 hero）按 `heroes` 列表过滤，含任一目标武将才保留（根治"text 提'类XX'"的跨武将噪声）。
- **分两段注入 `_format_rag_chunks`**：按 kind 分两段——「官方规则语料」（hero/rule/card/faq 等硬依据）与「社区实战参考」（combo/guide 启发层）；官方/社区独立预算池（`core_ratio=0.7` 给官方，剩余给社区，官方未用滚给社区），社区池内 combo 优先于 guide（组合信息对相性更直接，避免长攻略挤掉组合块）；整块丢弃不截断。含 `staleness_reason` 的块标记"过时风险"提示生成侧勿当硬依据。
- **技能行指针化省 token**：`_skill_lines()` 在语料块已注入时（判定 `hero_{id}_skill_{name}` 存在于 RAG 文本中）用指针标注替代完整描述（含结算），节省 token；RAG 关闭/运行时降级/预算挤掉整块时自动回退完整描述。
- **双版本选择**：UI 层可选「RAG 语料增强 / 经典模式」，经典模式向子进程追加 `--no-rag`（等价于 `RAG_ENABLED=false`）。
- **卡牌体系防串味兜底**：RAG 开启时，卡牌类语料块因向量召不回，`build_guide_prompt`/`build_synergy_prompt` 会在 RAG 段后兜底注入 `rule_summary.load_card_system()` 提取的「卡牌体系」段（行动/战法/装备/专属牌名清单），防止 AI 用三国杀牌名串味；RAG 关闭且无语料召回时仍注入完整 `load_core_rules()`。
- **配置**：`RAG_ENABLED` / `RAG_TOP_K`（12）/ `RAG_PROMPT_CHARS`（6000，攻略）/ `RAG_BROWSER_PROMPT_CHARS`（3000，浏览器模式）/ `RAG_SYNERGY_PROMPT_CHARS`（6000，相性注入预算）/ `RAG_MODEL_DIR`。

---

## 四、关键代码片段

### 4.1 两层重试机制

**HTTP 层（`_call_api`）：**

```python
def _call_api(self, messages: list[dict], temperature: float = 0.7) -> dict | None:
    for attempt in range(1, self.max_retries + 1):
        if self._cancelled:
            return None  # 取消标志：面板销毁/中止时退出
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        try:
            resp = self._client.post(self.api_url, headers=headers, json=payload)
            self._last_request_time = time.time()
            resp.raise_for_status()
            # 解析 response: choices[0].message.content / finish_reason / usage
            return {"content": ..., "finish_reason": ..., "usage": ...}
        except httpx.HTTPStatusError as e:
            if status in _NON_RETRYABLE_STATUS:  # 400/401/403/404/422 立即失败
                raise
            wait = self._retry_wait(status, attempt, e.response.headers)
            # 429: Retry-After 头（3-30s）或 5*attempt（5/10/15s）
            # 其他: 2^attempt（2/4/8s）
        except Exception as e:
            if isinstance(e, _CONN_ERRORS):  # 连接类异常重建 client
                self._client = httpx.Client(timeout=self.http_timeout)
```

**额度层（`_request_content`）：**

```python
def _request_content(self, messages, temperature, label):
    for attempt in range(1, self.max_retries + 1):
        response = self._call_api(messages, temperature=temperature)
        content, attempt_usage = _read_completion_content(response, self.max_output_tokens)
        # finish_reason="length" 或 content 为空 → None（思考耗尽正文额度）
        if content is not None:
            return content, usage
        if attempt < self.max_retries:
            wait = 2 ** attempt
            print(f"  [重试] 思考过程耗尽输出额度，第 {attempt}/{max} 次，{wait} 秒后重试")
            time.sleep(wait)
```

> **设计思路：** 前置限速比后端限速更可靠——API 被 429 限流后虽然可以重试，但被限流的请求已经消耗了网络资源。`_min_interval` 控制每秒最多 N 次请求，RPM 可配置。不可重试状态（400/401/403/404/422）立即失败避免白等退避。连接类异常（RemoteProtocolError/ReadError 等）后重建 httpx.Client 避免复用损坏 client 导致级联失败。思考长度随采样波动，重试通常能让正文挤进额度；每次重试都输出 `[重试]` 进度行，避免子进程长时间静默让用户以为卡死。输出额度上限默认 16384 token（`MAX_OUTPUT_TOKENS` 常量，可经 config.env 调大以适配思考型模型），缓解长攻略正文被截断（`finish_reason=length`）。

### 4.2 状态机修复字面换行

```python
def _repair_strings(s: str) -> str:
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and in_string:  # 跳过转义序列
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
        if in_string and c in '\r\n':  # 字符串内换行 → \n
            result.append('\\n')
            i += 1
            continue
        result.append(c)
        i += 1
    return ''.join(result)
```

> **设计思路：** AI 回复中的技能描述字段经常包含真实的换行符，导致 `json.loads()` 报错。全局替换 `\n` 会破坏键名中的合法字符。只有逐字符跟踪 `in_string` 状态才能精确定位字符串值内的换行并修复，保持 JSON 结构键名和分隔符不变。转义序列（`\\` + 后一个字符）被整体跳过，避免错误反转义。

### 4.3 Token 用量拆分记录

```python
def _log_usage(self, label: str, usage: dict) -> None:
    prompt = usage.get("prompt_tokens", 0)
    comp = usage.get("completion_tokens", 0)
    details = usage.get("completion_tokens_details") or {}
    reason = details.get("reasoning_tokens") or 0
    logger.info("[%s] token: prompt=%d completion=%d (reasoning=%d, content=%d)",
                label, prompt, comp, reason, comp - reason)
```

> **设计思路：** DeepSeek API 返回的 `usage.completion_tokens_details.reasoning_tokens` 记录思考过程消耗的 token 数，`comp - reason` 为正文 token。拆分记录便于定位"思考挤占正文预算"问题——当 `reasoning_tokens` 接近 `max_output_tokens` 时，正文往往被截断（`finish_reason=length`），额度层重试（4.1）正是针对此场景。

### 4.4 RAG 语料任务定义（task_defs.py，2026-08 新增）

`src/business/rag/task_defs.TASKS` 是 RAG 语料任务的**单一事实源**，工作台（`rag_maintenance_panel.py`）与调度脚本（`maintain_rag.py`）共用。10 个任务：

| # | 任务名 | 脚本 | 主要源 | 输出 |
|---|--------|------|--------|------|
| 1 | 武将语料 | build_rag_corpus.py | heroes/cards/mjs_adjustments | 武将RAG语料.json（615 块） |
| 2 | 卡牌语料 | build_card_corpus.py | cards | 卡牌RAG语料.json（49 块） |
| 3 | 点数花色语料 | build_cardpts.py | card_points | 卡牌点数花色语料.json（49 块） |
| 4 | 装备属性语料 | build_equip_attr.py | cards/equip_attrs/卡牌RAG语料 | 装备属性语料.json（27 块） |
| 5 | 加强削弱语料 | build_modify_corpus.py | cards/card_annotations | 加强削弱语料.json（49 块） |
| 6 | 元规则/术语/FAQ | build_rule_corpus.py | 元规则整理-完整版.md | 元规则RAG语料-章节块.json + 术语表.json + FAQ裁定块.json（snapshot） |
| 7 | 特殊机制语料 | build_special_corpus.py | special_cards | 特殊机制语料.json（83 块） |
| 8 | 武将分类语料 | build_classification_corpus.py | hero_classification/heroes | 武将分类语料.json（动态） |
| 9 | 组合语料 | build_combo_corpus.py | raw_guides/jinxia/combos/ + heroes | 组合RAG语料.json（动态） |
| 10 | 武将攻略语料 | build_guide_corpus.py | raw_guides/jinxia/guides/ + heroes/mjs_adjustments | 武将攻略RAG语料.json（动态） |

字段：`name` / `script` / `sources` / `outputs` / `expected`（int 精确匹配 / "snapshot" 只增不删 / None 动态数量只报不校验）。新增/修改语料任务只需改此文件。

---

## 五、接口说明

### CLI 入口（ai_batch.py）

```bash
python -m src.scraper.ai_batch --guide                    # 全量攻略
python -m src.scraper.ai_batch --synergy                   # 全量相性
python -m src.scraper.ai_batch --synergy-pair heroes.json  # 指定配对
python -m src.scraper.ai_batch --synergy-single hero.json  # 选定武将
python -m src.scraper.ai_batch --synergy-list pairs.json   # 实战配队清单
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--guide` | False | 生成攻略 |
| `--synergy` | False | 生成全量相性 |
| `--synergy-pair` | None | 指定配对（2~8 武将 JSON 文件） |
| `--synergy-single` | None | 选定武将 vs 全体 |
| `--synergy-list` | None | 实战配队清单（`[{"hero_a_id": int, "hero_b_id": int}, ...]` JSON 文件） |
| `--browser` | False | 使用浏览器模式 |
| `--dry-run` | False | 预览成本（仅 API 模式） |
| `--update` | False | 更新模式（重新生成已有数据） |
| `--heroes-file` | `data/heroes.json` | 武将数据文件 |
| `--guides-file` | `data/guides.json` | 攻略输出路径 |
| `--synergies-file` | `data/synergies.json` | 相性输出路径 |
| `--score-threshold` | 0 | 相性评分下限 |
| `--verbose` / `-v` | False | 详细日志 |
| `--no-rag` | False | 禁用 RAG 语料增强（默认启用） |
| `--rebuild-rag-index` | False | 重建 RAG 向量索引后退出 |

### AIBatchGenerator 公开方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `generate_guide` | `(hero: dict) -> tuple[dict\|None, dict\|None]` | 为单个武将生成攻略 |
| `generate_synergy` | `(hero_a: dict, hero_b: dict) -> tuple[dict\|None, dict\|None]` | 为武将对生成相性评分 |
| `complete` | `(messages: list[dict], temperature: float = 0.7) -> dict\|None` | 公开对话补全接口（供业务层调用） |
| `cancel` | `() -> None` | 请求中断：重试循环将在下次循环开头退出 |
| `close` | `() -> None` | 关闭 HTTP 客户端 |

### 公共函数

| 函数 | 文件 | 说明 |
|------|------|------|
| `extract_json(text)` | `json_extract.py` | 4 策略宽容提取 JSON；失败抛 `ValueError` |
| `validate_guide(raw)` | `utils.py` | Pydantic 校验攻略 |
| `validate_synergy(raw)` | `utils.py` | Pydantic 校验相性 |
| `has_required_guide_fields(raw)` | `utils.py` | 攻略必填字段预检（key_points/description 存在、正文 ≥200 字、无模板占位符），命中缺失可省去一次 Pydantic 异常开销 |
| `has_required_synergy_fields(raw)` | `utils.py` | 相性必填字段预检（score/description 存在、正文 ≥200 字、无模板占位符） |
| `load_core_rules()` | `rule_summary.py` | 加载核心规则摘要全文（RAG 关闭兜底）；缺失返回空串 |
| `load_card_system()` | `rule_summary.py` | 加载核心规则摘要的「卡牌体系」段（RAG 开启时防牌名串味兜底）；缺失返回空串 |
| `estimate_cost(count, mode, model, use_rag=True)` | `prompt_utils.py` | 按模型价格表预览 Token 和费用；`use_rag=False` 为经典模式（输入更少）；未知模型不估价 |
| `estimate_item_cost(item_count, mode, model, use_rag=True)` | `prompt_utils.py` | 按实际 API 请求项数预览 Token 和费用，用于指定范围的相性生成 |
| `build_rag_context(hero, max_chars)` | `rag_prompt.py` | 检索并格式化攻略 RAG 语料区块；异常返回空串 |
| `build_synergy_rag_context(hero_a, hero_b, max_chars)` | `rag_prompt.py` | 检索并格式化相性 RAG 语料区块（目标武将块 + 过滤后的跨类块） |
| `is_rag_enabled()` | `rag_prompt.py` | RAG 增强开关：环境变量 `RAG_ENABLED`（`--no-rag` 覆盖）优先，其次 config.env |
| `load_heroes(path)` | `utils.py` | 通过 `HeroManager` 完整校验武将 JSON；任一错误均拒绝部分加载 |
| `_save_json(path, data)` | `utils.py` | 原子写入 JSON（临时文件 + `replace()`） |

`ai/batch.py` 的断点加载通过 `GuideManager` / `SynergyManager` 逐条校验。发现无效 JSON、错误记录或重复键时，原文件先保留为同目录 `.corrupt-时间戳.json`，随后仅将通过校验的记录原子写回；如果备份失败，任务中止且不覆盖原文件。

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.models` | 使用 Hero / HeroGuide / SynergyScore 模型进行 Pydantic 校验 |
| 依赖 | `src.config.env` | 读取 API Key/URL/Model 等配置；`PROVIDER_PRESETS` 决定 Key 校验策略（ollama 本地服务不需 Key）；`MAX_OUTPUT_TOKENS` 控制输出上限 |
| 依赖 | `src.rag`（config/indexer/retriever） | ChromaDB 向量检索、bge-small-zh 嵌入与关键词 RRF 混合检索；`Retriever` 含武将/牌名倒排 `_hero_index` 与 KEYWORDS 关键词倒排 `_keyword_index`；`hero_blocks()`/`_keyword_hits()` 不再线性遍历全量块 |
| 依赖 | `src.scraper.ai.prompt_utils` | 共享 prompt 构建函数（`load_prompt`/`build_guide_prompt`/`build_synergy_prompt`），消除 API 与浏览器生成器之间的代码重复 |
| 被调用方 | `src.business.fetching.guide_fetch_service` | 通过 QProcess 启动 AI 攻略生成 |
| 被调用方 | `src.business.fetching.synergy_fetch_service` | 通过 QProcess 启动 AI 相性生成 |
| 被调用方 | `src.ui.app.main_window` | 菜单"数据 → 攻略/相性"触发生成 |
