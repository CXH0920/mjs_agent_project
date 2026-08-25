# 模块：AI 批量生成

> 对应目录：`src/scraper/ai/`
> 职责：通过 AI（DeepSeek API 或浏览器自动化）批量生成武将攻略和相性评分

---

## 一、模块职责

本模块是项目的**智能内容生成引擎**：接收武将数据，调用 AI 模型生成攻略或相性评分，从 AI 回复中提取 JSON，经 Pydantic 校验后持久化到数据文件。

核心能力：
- **双模式生成** — API 模式（httpx 直连 DeepSeek）和浏览器模式（Playwright + Edge 自动化操作 DeepSeek 网页版）
- **四种生成模式** — 全量攻略、全量相性、指定配对（2~8 武将 × itertools.combinations）、选定武将 × 全体
- **JSON 提取** — 从 AI 的不规范回复中宽容提取 JSON，支持 4 种回退策略
- **分批原子提交** — 每累计 10 条攻略或相性通过校验的结果即原子写入正式 JSON；失败项保留对应旧数据
- **RAG 语料增强** — 攻略与相性生成默认注入官方规则语料（ChromaDB 向量检索 + 关键词 RRF）；支持「RAG 语料增强（推荐）/ 经典模式（无 RAG 注入）」双版本，运行时异常自动降级为经典模式

---

## 二、文件结构

```
src/scraper/
├── ai_batch.py              # 兼容 CLI 入口
└── ai/
    ├── batch.py             # 参数解析、配置加载与任务分发
    ├── api_generator.py     # API 调用核心
    ├── browser_generator.py # 浏览器模式生成器
    ├── browser_session.py   # DeepSeek 页面会话
    ├── generation.py        # 四种生成编排函数
    ├── prompt_utils.py      # Prompt 构建与成本估算
    ├── rag_prompt.py       # RAG 语料检索与注入（攻略/相性，含降级提示）
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
      └── run_synergy_single_generation()
```

---

## 三、核心逻辑

### 3.1 双模式生成器

两个生成器满足统一接口，可互换：

```
generate_guide(hero)    → (dict|None, usage|None)
generate_synergy(a, b)  → (dict|None, usage|None)
```

| 特性 | API 模式 | 浏览器模式 |
|------|----------|------------|
| 类名 | `AIBatchGenerator` | `PlaywrightGenerator` |
| 数据源 | DeepSeek API | DeepSeek 网页版 |
| 限速 | RPM 控制 + 指数退避 | 每次成功生成后，在下一次请求前随机休息 60-180 秒 |
| Token 统计 | ✅ 支持 | ❌ 返回 None |
| 输出处理 | 关闭思考，上限 16384；仅解析最终 `content` | 读取网页最终回复 |
| 成本估算 | ✅ 支持 dry-run | ❌ 不支持 |
| 必备条件 | API Key | 已登录的 Edge 浏览器 |

浏览器模式按职责拆为两层：`PlaywrightGenerator` 负责提示词、JSON 提取、ID 转换和 Pydantic 校验；`DeepSeekBrowserSession` 负责 Edge/Playwright 生命周期、登录等待、页面诊断、消息发送和流式回复稳定检测。`PlaywrightGenerator._send_and_wait()` 保留为兼容委托入口，不再直接操作页面。

### 3.2 AIBatchGenerator（API 模式）

```
构造函数 → 创建 httpx.Client() + 初始化限速器
  │
generate_guide(hero)
  ├── load_prompt("docs/prompts/hero_guide.md") → system_prompt
  ├── build_guide_prompt(hero)                  → user_prompt
  ├── _call_api(messages=[system, user])
  │   ├── 限速检查（距上次请求不足 60/RPM 秒则 sleep）
  │   ├── POST /v1/chat/completions（thinking.type=disabled）
  │   ├── 丢弃思考内容，仅保留 content / finish_reason / usage
  │   ├── content 为空或 finish_reason=length → 明确报告输出额度耗尽
  │   └── 失败 3 次内指数退避重试（2s/4s/8s）
  ├── _extract_json(response.text)              → raw dict
  ├── inject hero_id / convert_ids_to_int(synergizes_with)
  └── _validate_guide(raw) → Pydantic 校验
```

### 3.3 JSON 提取策略

AI 的回复格式高度不可控，`_extract_json()` 按优先级依次尝试 4 种策略：

| 优先级 | 策略 | 说明 |
|--------|------|------|
| 1 | 全文 raw_decode | 直接解析整个字符串 |
| 2 | ```json 代码块 | 正则提取 Markdown 包裹的 JSON |
| 3 | --- 分隔线后 | `rfind("---")` 取最后一段 |
| 4 | { 到 } 区间 | `find("{")` ~ `rfind("}")` 截取兜底 |

每次尝试前调用 `_repair_strings()` 修复字符串值内的字面换行符（`\n` → `\\n`）。修复使用状态机跟踪是否在字符串上下文内，避免全局替换破坏非字符串内容。

### 3.4 相性配对（多武将组合）

`generation.py` 中的 `run_synergy_pair_generation()` 支持选择 2~8 个武将，用 `itertools.combinations` 遍历所有 C(N,2) 组合：

```python
for idx, (ha, hb) in enumerate(itertools.combinations(pair_heroes, 2), start=1):
    result, usage = generator.generate_synergy(ha, hb)
    if result:
        # 保存
    else:
        print(f"FAIL")  # 单对失败不阻断
```

**要点：** 每组配对开始时输出 `[i/total] ... START`，仅展示当前请求而不推进进度。AI 结果通过校验为 `OK`、校验失败为 `FAIL` 或确认已有数据为 `SKIP` 后，才输出对应终态行并推进 UI 进度条。浏览器模式的随机休息在下一组请求开始前执行，因此 N 组实际生成只休息 N-1 次，最后一组校验通过后直接保存和结束。指定配对默认跳过已有相性；当 UI 明确传入 `--update` 时才重新生成并覆盖已有相性。这样进度始终反映已完成校验或已确认跳过的配对。

### 3.5 任务结果与提交边界

每个编排函数返回 `GenerationResult`，其中包含 token 用量、完成数、跳过数、失败项和提交状态。CLI 只根据该结构化结果决定退出码：任一失败项都会以非零退出，API 与浏览器模式的规则一致；此前已成功的批次不回滚。

单个生成任务每累计 10 条攻略或相性校验成功，即通过临时文件 `replace()` 原子提交到正式 `guides.json` / `synergies.json`；任务结束时会提交不足一批的成功结果。任一失败项只保留原有对应记录，不回滚已成功批次。用户在进度对话框选择中止时会终止子进程，已提交批次保留，正在处理且尚未提交的数据不会写入。浏览器模式没有 token usage，不会因缺少 usage 被误判为失败，也不要求 API Key。

### 3.6 RAG 语料注入（攻略 / 相性）

`src/scraper/ai/rag_prompt.py` 负责把官方规则语料检索结果格式化为 prompt 区块；任何异常一律降级为空串，不影响生成链路。

- **攻略 `build_rag_context(hero)`**：先 `hero_blocks()` 取该武将全部语料块（hero 技能/结算 + guide 攻略 + classification），再跨类检索召回 combo 块（无 hero，按 `heroes` 列表过滤，含目标武将才保留）与规则/卡牌等；只注入目标武将相关的块。
- **相性 `build_synergy_rag_context(hero_a, hero_b)`**：
  1. 第一段确定性召回双方武将全部语料块（`hero_blocks()`，含 guide/classification）；
  2. 第二段跨类检索（不带武将过滤），查询串 = 双方武将名 + 技能名 + 技能描述中命中的 `retriever.KEYWORDS` 机制词（去重、上限 20），让规则/FAQ/卡牌/装备/combo 等跨类块进入；
  3. post-filter：`metadata.hero` 存在且不属于两名目标武将的块丢弃；combo 块（无 hero）按 `heroes` 列表过滤，含任一目标武将才保留（根治"text 提'类XX'"的跨武将噪声）。
- **分两段注入 `_format_rag_chunks`**：按 kind 分两段——「官方规则语料」（hero/rule/card/faq 等硬依据）与「社区实战参考」（combo/guide 启发层）；官方/社区独立预算池（core_ratio 给官方，剩余给社区，官方未用滚给社区），社区池内 combo 优先于 guide（组合信息对相性更直接，避免长攻略挤掉组合块）；社区段约束"取思路非文风"，不照搬口语网络用语。
- **双版本选择**：UI 层可选「RAG 语料增强 / 经典模式」，经典模式向子进程追加 `--no-rag`（等价于 `RAG_ENABLED=false`）。
- **降级提示**：检索/注入异常时记录 `rag_prompt.degraded_reason`，生成循环消费一次并在 stdout 输出 `[RAG] 语料不可用，本次已降级为经典模式（原因）`，进度窗口可见。
- **配置**：`RAG_ENABLED` / `RAG_TOP_K`（12）/ `RAG_PROMPT_CHARS`（6000）/ `RAG_BROWSER_PROMPT_CHARS`（3000）/ `RAG_SYNERGY_PROMPT_CHARS`（6000，相性注入预算）/ `RAG_MODEL_DIR`。

---

## 四、关键代码片段

### 4.1 限速与重试

```python
def _call_api(self, messages, temperature=0.7):
    # 限速控制
    elapsed = time.time() - self._last_request_time
    if elapsed < self._min_interval:
        time.sleep(self._min_interval - elapsed)

    for attempt in range(1, self.max_retries + 1):
        try:
            resp = self._client.post(self.api_url, json=payload)
            resp.raise_for_status()
            self._last_request_time = time.time()
            return resp.json()
        except (httpx.HTTPError, Exception):
            if attempt == self.max_retries:
                return None
            time.sleep(2 ** attempt)  # 2s, 4s, 8s
```

> **设计思路：** 前置限速比后端限速更可靠——API 被 429 限流后虽然可以重试，但被限流的请求已经消耗了网络资源。`_min_interval` 控制每秒最多 N 次请求，RPM 可配置。指数退避的 2/4/8 秒间隔在 3 次内覆盖了大多数临时故障。

### 4.2 状态机修复字面换行

```python
def _repair_strings(s: str) -> str:
    result, in_string = [], False
    for i, ch in enumerate(s):
        if ch == '"' and (i == 0 or s[i-1] != '\\'):
            in_string = not in_string
        if in_string and ch == '\n':
            result.append('\\n')
        elif in_string and ch == '\r':
            result.append('\\r')
        else:
            result.append(ch)
    return ''.join(result)
```

> **设计思路：** AI 回复中的技能描述字段经常包含真实的换行符，导致 `json.loads()` 报错。全局替换 `\n` 会破坏键名中的合法字符。只有逐字符跟踪 `in_string` 状态才能精确定位字符串值内的换行并修复，保持 JSON 结构键名和分隔符不变。

---

## 五、接口说明

### CLI 入口（ai_batch.py）

```bash
python -m src.scraper.ai_batch --guide                    # 全量攻略
python -m src.scraper.ai_batch --synergy                   # 全量相性
python -m src.scraper.ai_batch --synergy-pair heroes.json  # 指定配对
python -m src.scraper.ai_batch --synergy-single hero.json  # 选定武将
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--guide` | False | 生成攻略 |
| `--synergy` | False | 生成全量相性 |
| `--synergy-pair` | None | 指定配对（2~8 武将 JSON 文件） |
| `--synergy-single` | None | 选定武将 vs 全体 |
| `--browser` | False | 使用浏览器模式 |
| `--dry-run` | False | 预览成本（仅 API 模式） |
| `--update` | False | 更新模式（重新生成已有数据） |
| `--heroes-file` | `data/heroes.json` | 武将数据文件 |
| `--score-threshold` | 0 | 相性评分下限 |
| `--no-rag` | False | 禁用 RAG 语料增强（默认启用） |
| `--rebuild-rag-index` | False | 重建 RAG 向量索引后退出 |

### 公共函数

| 函数 | 文件 | 说明 |
|------|------|------|
| `extract_json(text)` | `json_extract.py` | 4 策略宽容提取 JSON |
| `validate_guide(raw)` | `utils.py` | Pydantic 校验攻略 |
| `validate_synergy(raw)` | `utils.py` | Pydantic 校验相性 |
| `estimate_cost(count, mode, model, use_rag=True)` | `prompt_utils.py` | 按模型价格表预览 Token 和费用；`use_rag=False` 为经典模式（输入更少）；未知模型不估价 |
| `estimate_item_cost(item_count, mode, model, use_rag=True)` | `prompt_utils.py` | 按实际 API 请求项数预览 Token 和费用，用于指定范围的相性生成 |
| `build_rag_context(hero, max_chars)` | `rag_prompt.py` | 检索并格式化攻略 RAG 语料区块；异常返回空串 |
| `build_synergy_rag_context(hero_a, hero_b, max_chars)` | `rag_prompt.py` | 检索并格式化相性 RAG 语料区块（目标武将块 + 过滤后的跨类块） |
| `load_heroes(path)` | `utils.py` | 通过 `HeroManager` 完整校验武将 JSON；任一错误均拒绝部分加载 |
| `_save_json(path, data)` | `utils.py` | 原子写入 JSON |

`ai/batch.py` 的断点加载通过 `GuideManager` / `SynergyManager` 逐条校验。发现无效 JSON、错误记录或重复键时，原文件先保留为同目录 `.corrupt-时间戳.json`，随后仅将通过校验的记录原子写回；如果备份失败，任务中止且不覆盖原文件。

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.models` | 使用 Hero / HeroGuide / SynergyScore 模型进行 Pydantic 校验 |
| 依赖 | `src.config.env` | 读取 API Key/URL/Model 等配置 |
| 依赖 | `src.rag`（config/indexer/retriever） | ChromaDB 向量检索、bge-small-zh 嵌入与关键词 RRF 混合检索（2026-08：`Retriever` 新增武将/牌名倒排 `_hero_index` 与 KEYWORDS 关键词倒排 `_keyword_index`，`hero_blocks()`/`_keyword_hits()` 不再线性遍历全量块） |
| 被调用方 | `src.business.fetching.guide_fetch_service` | 通过 QProcess 启动 AI 攻略生成 |
| 被调用方 | `src.business.fetching.synergy_fetch_service` | 通过 QProcess 启动 AI 相性生成 |
| 被调用方 | `src.ui.app.main_window` | 菜单"数据 → 攻略/相性"触发生成 |
