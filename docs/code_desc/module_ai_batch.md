# 模块：AI 批量生成

> 对应目录：`src/scraper/ai/`
> 职责：通过 AI（多供应商 API 或浏览器自动化）批量生成武将攻略和相性评分；超时分层与定向重试、prompt 四层决策门/锚例校准/JSON 纪律、生成任务互斥锁
> 文档日期：2026-10-23（基线 a63bc3c）

---

## 一、模块职责

本模块是项目的**智能内容生成引擎**：接收武将数据，调用 AI 模型生成攻略或相性评分，从 AI 回复中提取 JSON，经 Pydantic 校验后持久化到数据文件。

核心能力：
- **双模式生成** — API 模式（httpx 直连，按 `provider` 适配 deepseek / openai / ollama / openai-compatible 多供应商档案）和浏览器模式（Playwright + Edge 自动化操作 DeepSeek 网页版）
- **五种生成模式** — 全量攻略、全量相性、指定配对（2~8 武将 × itertools.combinations）、选定武将 × 全体、实战配队清单（显式 id 配对列表）
- **JSON 提取** — 从 AI 的不规范回复中宽容提取 JSON，支持 4 种回退策略
- **分批原子提交** — 每累计 10 条攻略或相性通过校验的结果即原子写入正式 JSON；失败项保留对应旧数据
- **RAG 语料增强** — 攻略与相性生成默认注入官方规则语料（ChromaDB 向量检索 + 关键词 RRF）；支持「RAG 语料增强（推荐）/ 经典模式（无 RAG 注入）」双版本，运行时异常自动降级为经典模式
- **两层重试机制** — HTTP 层指数退避（429 优先读 Retry-After 头退避）+ 输出额度层重试（思考过程耗尽正文额度时自动重试）
- **连接恢复与取消** — 连接类异常（RemoteProtocolError/ReadError/ReadTimeout 等）先关闭并重建 httpx.Client，避免损坏连接池导致后续重试级联失败；`cancel()` 置 `_cancelled` 标志，重试循环在下次循环开头退出
- **限流与失败提示** — RPM 前置限流（默认 30 req/min）；每次重试向 stdout 输出 `[重试]` 进度行（子进程管道转发到进度窗口，避免长时间静默）；Pydantic 校验前先做必填字段预检，拦截模板占位符正文与过短正文，命中即快速失败省一次校验开销
- **生成任务互斥锁** — 入口 `acquire_generation_lock()` 用 OS 级文件锁（Windows `msvcrt` / POSIX `fcntl`）保证同一时刻只有一个批量生成任务运行（VUL-B4-001）：分批提交按"启动快照全量覆盖"写盘，并发任务会静默抹掉对方已提交的成果；持有者 PID + 时间写入旁路 `*.holder` 文件供占用提示；锁文件不可用时降级为哑锁放行，持锁进程退出或崩溃时锁由 OS 自动释放、无残留锁
- **中断状态与取消链路健壮性** — 不可重试异常（401/403 等）不再直接冒泡丢失信息：写 `interrupted: true` 中断标记（`failed_items` 留空，循环未跑完无从得知），已提交成果保留、退出 1；`--retry-failed` 检测到中断标记明确拒绝，避免按残留的过期失败项重跑错项（TD-A5-004）；锁在 `finally` 中释放
- **浏览器会话超时重建** — `DeepSeekBrowserSession` 等待/提取任一段失败（超时、选择器失效、填入选区失败）置 `_started=False` 标记页面失效，下次请求先在 `_ensure_browser` 中 `close()` 释放旧上下文再重建，避免复用停在验证码/登录失效页的页面导致后续每个请求都等满超时级联失败，也避免覆盖 `_playwright`/`_context` 造成 Edge 进程泄漏与 `user_data_dir` 锁残留（VUL-B2-001）；两段等待各用独立超时预算

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
    ├── generation_lock.py   # 生成任务互斥锁（OS 级文件锁，防并发覆盖）
    ├── prompt_utils.py      # Prompt 构建与成本估算
    ├── rag_prompt.py        # RAG 语料检索与注入（攻略/相性，含降级提示）
    ├── rule_summary.py      # 核心规则摘要/卡牌体系段加载（RAG 兜底注入）
    ├── json_extract.py      # AI 回复 JSON 提取
    └── utils.py             # 数据加载、校验与原子保存
```

### 模块间调用关系

```
ai_batch.py (兼容 CLI) -> ai/batch.py
 ├── acquire_generation_lock()                     # 生成任务互斥：拒绝并发启动（VUL-B4-001）
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
| 数据源 | 供应商端点（默认 `provider=deepseek`；支持 openai / ollama / openai-compatible，档案取自 `config/api_profiles.json`） | DeepSeek 网页版（chat.deepseek.com） |
| 限速 | RPM 前置限流（默认 30 req/min，`_min_interval = 60/RPM`）+ 指数退避 | 每次成功生成后，在下一次请求前随机休息 60-180 秒 |
| Token 统计 | 支持（拆分 reasoning/content 记录） | 返回 None |
| 输出上限 | 默认 32768 token（思考+正文共享额度，可经 `MAX_OUTPUT_TOKENS` 按供应商上限调整） | 无上限限制（读取网页最终回复） |
| 输出处理 | 仅 `provider=deepseek` 注入私有参数 `thinking.type=disabled`（非 DeepSeek 端点收到未知字段会 400）；仅解析最终 `content`；思考耗尽正文额度时自动重试 | 读取网页最终回复；JSON 提取失败时发送纠正消息重试 |
| 成本估算 | 支持 dry-run | 不支持 |
| 必备条件 | API Key（`requires_key=False` 的 ollama 本地服务可空；构造期按 `PROVIDER_PRESETS` 校验，不满足抛 `ValueError`） | 已登录的 Edge 浏览器 |
| 取消支持 | `cancel()` 方法（置 `_cancelled` 标志，重试循环下次循环开头退出；不打断 in-flight 请求，靠超时退出） | 无 |
| 会话/连接健壮性 | 连接类异常（`_CONN_ERRORS`）关闭并重建 httpx.Client（分层超时在重建时保持） | 等待/提取失败置 `_started=False`，下次请求先释放旧上下文再重建浏览器（见 3.9） |
| RAG 预算 | `RAG_PROMPT_CHARS`（默认 6000 字符）/ `RAG_SYNERGY_PROMPT_CHARS`（相性，默认 6000） | `RAG_BROWSER_PROMPT_CHARS`（默认 3000 字符） |

浏览器模式按职责拆为两层：`PlaywrightGenerator` 负责提示词、JSON 提取、ID 转换和 Pydantic 校验；`DeepSeekBrowserSession` 负责 Edge/Playwright 生命周期、登录等待、页面诊断、消息发送和流式回复稳定检测。`PlaywrightGenerator._send_and_wait()` 保留为兼容委托入口，不再直接操作页面。

每个武将/武将对都重发完整 system prompt + 数据（与 API 模式对齐），避免网页版会话中首轮格式指令在后续轮次衰减导致输出偏离 JSON；另在消息末尾追加 `GUIDE_FORMAT_REMINDER` / `SYNERGY_FORMAT_REMINDER` 利用"最近指令权重最高"特性稳住 JSON 输出结构。

### 3.2 AIBatchGenerator（API 模式）

```
构造函数 → 供应商 Key 语义校验（PROVIDER_PRESETS[provider].requires_key，不满足抛 ValueError）
         → 创建 httpx.Client(timeout=_build_timeout())（分层超时：connect/pool=5s、write=30s、read=http_timeout 默认 300s）
         + 初始化限速器（_min_interval = 60/RPM）+ 取消标志 _cancelled=False
  │
generate_guide(hero)
  ├── load_prompt("docs/prompts/hero_guide.md") → system_prompt（模板缺失直接返回 None）
  ├── build_guide_prompt(hero)                  → user_prompt
  │   ├── build_rag_context(hero)               → RAG 语料区块（若启用）
  │   ├── _skill_lines()                        → 技能行（语料块已注入时指针化省 token，未注入回退完整描述+结算）
  │   └── load_card_system()                    → 卡牌体系段兜底（防牌名串味）
  ├── _request_content(messages=[system, user], temperature=0.55, label=hero.name)
  │   ├── _call_api(messages, temperature)
  │   │   ├── 循环开头检查 _cancelled → 已取消返回 None（不打断 in-flight 请求，靠超时退出）
  │   │   ├── 限速检查（距上次请求不足 60/RPM 秒则 sleep）
  │   │   ├── POST api_url（仅 provider=deepseek 注入 thinking.type=disabled；max_tokens=max_output_tokens）
  │   │   ├── 解析 response：content / finish_reason / usage
  │   │   ├── 400/401/403/404/422 → 立即抛错失败（Key/参数问题重试无意义）
  │   │   ├── 429 → 优先读 Retry-After 头（钳到 3-30s），无头则 max(5*attempt, 3)（5/10/15s）
  │   │   ├── 其他 408/5xx → 2^attempt（2/4/8s）
  │   │   └── 连接类异常（_CONN_ERRORS）→ 关闭并重建 httpx.Client（timeout=_build_timeout()，分层超时在重建时保持）后重试（避免连接池损坏级联失败）
  │   │       （每次重试向 stdout 输出 [重试] 行，进度窗口可见）
  │   ├── _read_completion_content(response)
  │   │   ├── finish_reason="length" 或 content 为空 → 返回 None
  │   │   └── 否则返回 (content, usage)
  │   ├── _log_usage(label, usage)             → 记录 prompt/completion 与 reasoning/content 拆分
  │   └── content=None 且 attempt < max_retries → 输出 [重试] 思考过程耗尽输出额度
  │       （思考长度随采样波动，重试通常能让正文挤进额度；每次重试向 stdout 输出 [重试] 进度行）
  ├── extract_json(content)                     → raw dict
  └── normalize_and_validate_guide(raw, hero)   → 注 hero_id / synergizes_with 转 int /
      必填预检 / Pydantic 校验（双生成器共用后处理，P1-5 收口）
```

**两层重试的区别：**

| 层级 | 方法 | 触发条件 | 退避策略 | 说明 |
|------|------|----------|----------|------|
| HTTP 层 | `_call_api()` | HTTP 错误（408/429/5xx）、网络异常、连接超时 | 429：Retry-After 头钳到 3-30s，无头则 max(5*attempt, 3)（5/10/15s）；其他：2^attempt（2/4/8s） | 不可重试状态（400/401/403/404/422）立即抛错失败；重试时 stdout 输出 `[重试]` 行 |
| 额度层 | `_request_content()` | `_call_api()` 返回 None（HTTP 层重试耗尽）或 `finish_reason="length"`/content 为空（思考过程耗尽正文额度） | 2^attempt（2/4/8s） | 思考长度随采样波动，重试通常能让正文挤进额度；每次重试输出 `[重试]` 行 |

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
    result_summary.add_usage(usage)
    _report_rag_degradation()                  # RAG 降级时 stdout 输出一次 [RAG] 提示
    if generated:
        if score_threshold is None:
            working_synergies[pair_key] = _with_synergy_updated_date(generated)
        elif generated.get("score", 0) >= score_threshold:
            working_synergies[pair_key] = _with_synergy_updated_date(generated)
        else:
            working_synergies.pop(pair_key, None)   # 低于下限移除旧记录
        print(f"  [{idx}/{total}] {label_of(ha, hb)} OK - 评分: {generated.get('score', '?')}")
    else:
        result_summary.failed_items.append(fail_label_of(ha, hb))
        print(f"  [{idx}/{total}] {label_of(ha, hb)} FAIL")
```

**要点：** 每组配对开始时输出 `[i/total] ... START`，仅展示当前请求而不推进进度。AI 结果通过校验为 `OK`、校验失败为 `FAIL` 或确认已有数据为 `SKIP` 后，才输出对应终态行并推进 UI 进度条。浏览器模式的随机休息在下一组请求开始前执行，因此 N 组实际生成只休息 N-1 次，最后一组校验通过后直接保存和结束。指定配对默认跳过已有相性；当 UI 明确传入 `--update` 时才重新生成并覆盖已有相性。全量模式使用 `score_threshold` 过滤：评分低于下限的配对从 `working_synergies` 中移除旧记录。

### 3.5 任务结果与提交边界

每个编排函数返回 `GenerationResult`，其中包含 token 用量、完成数、跳过数、失败项和提交状态。CLI 只根据该结构化结果决定退出码：任一失败项都会以非零退出，API 与浏览器模式的规则一致；此前已成功的批次不回滚。

单个生成任务每累计 10 条攻略或相性校验成功，即通过临时文件 `replace()` 原子提交到正式 `guides.json` / `synergies.json`；任务结束时会提交不足一批的成功结果。任一失败项只保留原有对应记录，不回滚已成功批次。用户在进度对话框选择中止时会终止子进程，已提交批次保留，正在处理且尚未提交的数据不会写入。浏览器模式没有 token usage，不会因缺少 usage 被误判为失败，也不要求 API Key。

**写前快照（d2c5b83）**：`main()` 在加载既有数据前，`--guide` 模式对 `guide_path`、相性模式对 `synergy_path` 各 `snapshot_to_backups()` 一次——**每次运行一次而非每 10 条批量提交一次**，避免一次全量生成产生大量备份；生成期间旧数据可从 `data/backups/` 找回（AI 子进程按批全量覆盖写，落点是它启动时读入的旧快照）。

**相性日期标记：** 每次校验成功的相性结果通过 `_with_synergy_updated_date()` 写入 `last_updated` 字段（本次生成日期），用于追踪数据新鲜度。

### 3.6.1 Prompt 工程纪律（cfca666，docs/prompts/）

生成质量与解析稳定性的主要约束在 prompt 侧（`docs/prompts/synergy_score.md` / `hero_guide.md`），解析代码（`extract_json` 四段回退 + 字段预检 + pydantic 校验）零改动：

- **相性评分四层决策门**：score 重定义为**协同增量**（组队相对各自独立作战多出来的价值，非强度加总），打分前先过门定层、层内微调——①技能机制冲突（抢同一资源/节奏互斥）→ 负分 D 档；②无直接联动也无功能配合（双方技能均只对自身生效）→ −2~2（C 档）；③功能互补但无触发链 → 0~5（C/B 档）；④存在直接技能联动（能指出具体触发链与量化收益）→ 3~10（B/A/S 档）。
- **锚例校准**：分布预期段（随机配对大多应落第 2/3 层，A 档必须写出具体触发链与量化收益，S 档仅限闭环循环/多段叠加/理论无解）+ 文末三档真实锚例（S 档孟尝君+黄月英 score 9 / A 档芈八子+项梁 score 7 / C 档关羽+张华 score 1）；原"三维度平均映射 score"的膨胀条款删除（三维度是分解视角，不构成 score 充分条件）。效果：固定 10 对样本评分漂移（|Δscore|≥3）20% → 0%，A 档占比清零。
- **攻略 JSON 纪律**：正文 → 单独一行 `---` → JSON，JSON 必须是全文**最后一个 ```json 代码块**、正文部分禁止出现任何代码块或花括号结构（防干扰 `extract_json` 回退）；`description` 回填正文时换行写 `\n`、双引号写 `\"` 保证 JSON 单行合法；字数区间 600-1000 → 1200-2000。效果：固定 20 武将样本 JSON 抛错率 20% → 0%。
- **联动判定强化**：自身技能两两配对逐一判定（技能A×技能B），"一个技能的输出（牌、状态、次数、时机）恰好是另一个技能的输入"才构成机制联动，功能相似（"都能摸牌"）不算；无联动技能对一句带过，全部无联动明确写"无显著技能联动"，禁止硬凑循环。
- **牌名核对**：牌名/战法名/装备名落笔前必须在【卡牌体系参考】或 RAG 语料块中找到对应，找不到改用效果描述。

### 3.6 RAG 语料注入（攻略 / 相性）

`src/scraper/ai/rag_prompt.py` 负责把官方规则语料检索结果格式化为 prompt 区块；任何异常一律降级为空串，不影响生成链路。降级原因记录在模块级 `degraded_reason` 变量中，由生成循环通过 `take_degraded_reason()` 消费一次并在 stdout 输出 `[RAG] 语料不可用，本次已降级为经典模式（原因）`，进度窗口可见。

- **攻略 `build_rag_context(hero, max_chars=None)`**：先 `hero_blocks()` 取该武将全部语料块（hero 技能/结算 + guide 攻略 + classification），再跨类检索召回 combo 块（无 hero，按 `heroes` 列表过滤，含目标武将才保留）与规则/卡牌等；只注入目标武将相关的块。查询串 = 武将名 + 技能名 + 技能描述中命中的 `retriever.KEYWORDS` 机制词（去重、上限 20）。
- **相性 `build_synergy_rag_context(hero_a, hero_b, max_chars=None)`**：
  1. 第一段确定性召回双方武将全部语料块（`hero_blocks()`，含 guide/classification）；
  2. 第二段双 query 融合（不带武将过滤）：query1 = 双方武将名（找基础信息），query2 = 技能名 + 机制词（找联动效果），各取半数 `top_k` 去重合并；
  3. post-filter：`metadata.hero` 存在且不属于两名目标武将的块丢弃；combo 块（无 hero）按 `heroes` 列表过滤，含任一目标武将才保留（根治"text 提'类XX'"的跨武将噪声）。
- **分两段注入 `_format_rag_chunks`**：按 kind 分两段——「官方规则语料」（hero/rule/card/faq 等硬依据）与「社区实战参考」（combo/guide 启发层）；官方/社区独立预算池（`core_ratio=0.7` 给官方，剩余给社区，官方未用滚给社区），社区池内 combo 优先于 guide（组合信息对相性更直接，避免长攻略挤掉组合块）；整块丢弃不截断。含 `staleness_reason` 的块标记"过时风险"提示生成侧勿当硬依据。
- **技能行指针化省 token**：`_skill_lines(skills, hero_id, rag, indent)` 按**单个技能**判定——语料块 id `hero_{id}_skill_{技能名}` 出现在已注入的 RAG 文本中时，该技能用指针标注替代完整描述（形如 `- 技能名:（完整描述见 [hero_1_skill_XXX] 语料块）`）；RAG 关闭 / 运行时降级 / 预算挤掉该块 / 语料块格式漂移导致判定失败时，该技能自动回退完整描述并附 ` ｜结算：...` 后缀。指针化与回退逐技能独立生效，同一武将可同时出现指针技能与完整描述技能。注意：结算说明依赖上游传入的 `skills` 字典带 `settlement` 字段（UI 全量/增量链路与指定武将选择对话框曾漏传该字段导致结算说明缺失，已修复）；块内语料侧的时机/触发条件标签由语料构建脚本写入，不在本模块。
- **时间轴过时提示**：语料块带 `as_of` / `is_current` 版本戳，检索器默认只召当前版本块；仍被召回且带 `staleness_reason` 的块在 `_format_rag_chunks()` 前缀 `⚠️ 过时风险：...` 提示生成侧勿当硬依据。
- **双版本选择**：UI 层可选「RAG 语料增强 / 经典模式」，经典模式向子进程追加 `--no-rag`（等价于 `RAG_ENABLED=false`）。
- **卡牌体系防串味兜底**：RAG 开启时，卡牌类语料块因向量召不回，`build_guide_prompt`/`build_synergy_prompt` 会在 RAG 段后兜底注入 `rule_summary.load_card_system()` 提取的「卡牌体系」段（行动/战法/装备/专属牌名清单），防止 AI 用三国杀牌名串味；RAG 关闭且无语料召回时仍注入完整 `load_core_rules()`。核心规则摘要 6b21d75 起读运行时可写根 `_CORE_RULES_FILE = PROJECT_ROOT/data/rag_corpus/核心规则摘要.md`（与检索链路 `src/rag/config` 及维护脚本同根——frozen 下用户对部署副本的修改在兜底中生效；开发态两根相等无差异）。
- **配置**：`RAG_ENABLED` / `RAG_TOP_K`（12）/ `RAG_PROMPT_CHARS`（6000，攻略）/ `RAG_BROWSER_PROMPT_CHARS`（3000，浏览器模式）/ `RAG_SYNERGY_PROMPT_CHARS`（6000，相性注入预算）/ `RAG_MODEL_DIR`。

### 3.7 生成任务互斥锁（9180cf0，VUL-B4-001）

分批提交按"启动时读入旧快照 → 每 10 条校验成功即全量覆盖写盘"进行（3.5 节），两个生成进程并发写同一 `guides.json` / `synergies.json` 时，后提交者会静默抹掉对方已提交的成果。`ai/batch.py` 入口用 OS 级文件锁互斥：

- **锁文件**：`GENERATION_LOCK_PATH = PROJECT_ROOT/"logs"/"ai_generation.lock"`（运行时状态，已 gitignore），旁路 `ai_generation.lock.holder` 记录持有者 `pid\tISO 时间`。持有信息写在旁路文件而非锁文件本体，因为 Windows 字节锁会拒绝其他句柄对锁文件的常规读取；进程崩溃后 holder 可能残留旧记录，故互斥判定一律以 OS 锁为准（文案用"最近持有记录"而非"持有者"）。
- **实现**：Windows `msvcrt.locking(fd, LK_NBLCK, 1)` / POSIX `fcntl.flock(fd, LOCK_EX|LOCK_NB)` 对第 0 字节加非阻塞独占锁。持锁进程退出（含崩溃、断电、任务管理器结束）时锁由 OS 自动释放，不留下需要人工清理的残留锁。
- **拒绝语义**：`acquire_generation_lock()` 被占用返回 `None`，`main()` 打印「已有另一个生成任务正在运行…」并 `sys.exit(1)`（子进程非零退出，进度窗口按失败上报）。锁文件本身不可创建或不可锁定时返回哑锁（`fd=-1`）降级放行——保持无锁旧行为，仅记警告，不因锁机制自身故障阻断生成。
- **释放时机**：持锁跨越整个生成循环与提交阶段，`main()` 的 `finally` 中 `lock.release()`（在 `generator.close()` 之后）；`--dry-run` 在获取锁之前提前 `return`，不占用锁。

### 3.8 中断状态与取消链路（74ad9b9 / f10b477 / f1b13f8）

**中断状态（TD-A5-004）**：`_call_api()` 对 400/401/403/404/422 立即 `raise`（3.2 节），此前该异常直接冒泡出 `main()`，失败清单停留在上一次内容、本次已花掉的 token 费用完全不可见。现生成循环包在 `try/except Exception` 中：捕获后记 `fatal_error` 与 `[中断]` 行，`finally` 中照常 `generator.close()` + `lock.release()`，随后走统一收尾——打印 token 汇总、保留已提交批次、`_save_interrupted_state()` 写中断标记、`sys.exit(1)`。

中断标记与失败清单同文件（`logs/ai_last_failures.json`）不同语义：`interrupted: true` + `error: "<ExcType>: <msg>"` + `failed_items: []`。循环未跑完失败项无从得知——既不能保留旧清单（`--retry-failed` 会重跑过期项），也不能静默清除（丢失线索）。`_load_retry_items()` 检测到 `interrupted` 明确拒绝并 `sys.exit(1)`，文案指出上次中断原因并要求直接重跑完整任务。收尾判定顺序：先查 `failed_results`（跑完但有失败项 → 写失败清单），再查 `fatal_error`（未跑完 → 写中断标记），全部成功才 `unlink(missing_ok=True)` 清除旧清单。

**退出码协议**：`0` = 正常结束（全部成功、`--dry-run` 预览、`--retry-failed` 无失败记录或目标武将已下架、`--rebuild-rag-index` 建出索引）；`1` = 有失败项 / 任务中断 / 互斥锁拒绝 / 空武将数据 / 缺 API Key / 失败清单读取失败；`2` = argparse 参数错误（`parser.error`，如未指定任何生成模式）。

**取消链路健壮性**（父进程侧，`src/business/fetching/`，非本模块）：子进程侧 `AIBatchGenerator.cancel()` 仍只在重试循环开头生效（3.1 节）。三处修复保证中止不会把服务锁死（f10b477）：`cancel()` 在 QProcess 尚处 Starting 状态时不立即树杀（此时 `processId()` 为 0、Qt6 对 Starting 状态 `kill()` 会在事件循环内原生 abort），改为连接 `started` 信号后补一次树杀；清理轮询加 `_CANCEL_CLEANUP_MAX_POLLS = 100`（50ms/轮 ≈ 5 秒）上限，taskkill 被安全软件拦截或 PID 被复用时强制收尾复位 `_cancel_requested`；取消期间的非 Crashed 错误（典型为 Starting 阶段 `FailedToStart`）也调用收尾复位，避免 `_is_busy` 永久 True。看门狗从"总时长上限"改为"无进度上限"（f1b13f8）：`_dispatch_stdout_line` 识别进度行（START/OK/FAIL/SKIP/重试/休息/RAG/开始）后 `_feed_watchdog` 用 `stop()+start()` 强制重启 30 分钟计时（Qt6 对已激活的同间隔定时器 `start()` 为 no-op），避免长清单浏览器模式任务被误杀。退出时 `AppServices.shutdown_fetch_services` 依次对三个抓取服务发 `cancel` 并限时泵事件循环等进程树真正退出（1d8334a），避免 QProcess 随父对象析构不杀子进程树、Edge/node 孤儿化并锁住 `data/edge_profile`。

### 3.9 浏览器会话超时重建（74ad9b9，VUL-B2-001）

`send_and_wait()` 原为单一函数内两段等待共用同一 `deadline`：首段（等新回复气泡）耗时接近上限时次段（内容稳定）几乎无剩余时间直接判超时；超时或选择器失效后页面可能停在验证码/登录失效页，复用会让后续每个请求都等满超时并级联失败。现拆为三段方法，各段独立超时预算：

```
send_and_wait(prompt)
  ├── _ensure_browser()
  ├── page.fill(input_selector, prompt)                    [失败 -> _invalidate_page]
  ├── page.keyboard.press("Enter")                         [失败 -> _invalidate_page]
  ├── _wait_for_new_message(page, selector, before_count, timeout)   [500ms 轮询，独立 deadline]
  ├── _wait_until_stable(page, selector, timeout)                        [2000ms 轮询，独立 deadline，连续 3 轮长度不变]
  └── _extract_last_reply(page, selector)                                  [提取最后一条消息文本]
```

`_invalidate_page(reason)` 在 fill 失败、Enter 失败、未检测到新回复、内容未稳定、未找到消息、提取出错六处调用：仅置 `_started = False`（不立即 `close()`，避免连带关闭用户正在观看的窗口），并输出 `[恢复] 页面状态异常（reason），下次请求将重建浏览器上下文`。`_ensure_browser()` 检测到 `_started=False` 但 `_playwright` / `_context` 仍存活时先 `close()` 释放旧上下文再重建——否则覆盖 `_playwright`/`_context` 会泄漏 Edge 进程并继续占用 `data/edge_profile` 的 `user_data_dir` 锁，下次浏览器模式启动报 `locked`。

> 浏览器自动化访问 chat.deepseek.com 的外部行为（启动参数 `--disable-blink-features=AutomationControlled`、生成间随机休息 60-180 秒、User-Agent 标识）在 TERMS.md 第 7 节「自动化访问与访问频率披露」如实披露；`DEFAULT_BROWSER_CONFIG` 与 `_random_rest()` 处的代码注释均指向该章节（9180cf0，措辞由"避免触发风控"改为中性的"拉开请求间隔、降低访问密度"）。

---

## 四、关键代码片段

### 4.1 两层重试机制

**HTTP 层（`_call_api`）：**

```python
def _call_api(self, messages: list[dict], temperature: float = 0.7) -> dict | None:
    payload = {"model": self.model, "messages": messages,
               "temperature": temperature, "max_tokens": self.max_output_tokens}
    if self.provider == "deepseek":        # thinking 是 DeepSeek 私有参数，其他端点会 400
        payload["thinking"] = {"type": "disabled"}
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
            # 429: Retry-After 头钳到 3-30s，无头则 max(5*attempt, 3)（5/10/15s）
            # 其他: 2^attempt（2/4/8s）
        except Exception as e:
            if isinstance(e, _CONN_ERRORS):  # 连接类异常重建 client
                self._client.close()
                self._client = httpx.Client(timeout=self._build_timeout())
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

> **设计思路：** 前置限速比后端限速更可靠——API 被 429 限流后虽然可以重试，但被限流的请求已经消耗了网络资源。`_min_interval` 控制每秒最多 N 次请求，RPM 可配置。429 优先服从服务端 `Retry-After`（钳到 3-30s，尊重真实冷却窗口），无该头时按 5/10/15s 走比通用退避更保守的节奏。不可重试状态（400/401/403/404/422）立即失败避免白等退避。连接类异常（RemoteProtocolError/ReadError/ReadTimeout 等）后先 close 再重建 httpx.Client，避免复用损坏 client/连接池导致后续重试级联失败。**超时分层化（4720366）**：`_build_timeout()` 返回 `httpx.Timeout(connect=5.0, pool=5.0, write=30.0, read=http_timeout)`——连接黑洞化时 5 秒快速失败（单武将失败从最坏约 15 分钟降为约 15 秒），read 保持配置的完整读超时；构造抽成单一方法供 `__init__` 与重试重建两处共用，堵住"重建时退化为标量超时"的遗漏路径。`thinking` 是 DeepSeek 私有参数，非 DeepSeek 端点收到未知字段会返回 400，故按 `provider == "deepseek"` 条件化组装。`cancel()` 只在重试循环开头生效、不打断 in-flight 请求（靠超时退出），保证中止后面板关闭不会继续 post。思考长度随采样波动，重试通常能让正文挤进额度；每次重试都输出 `[重试]` 进度行，避免子进程长时间静默让用户以为卡死。输出额度上限默认 32768 token（`api_generator.MAX_OUTPUT_TOKENS`，1bfd65b 起单源于 `env.DEFAULT_MAX_OUTPUT_TOKENS`，键元表 `MAX_OUTPUT_TOKENS` 默认值同引该常量，此前两处硬编码靠注释同步；思考+正文共享额度，可经 config.env 按供应商上限调大以适配思考型模型），缓解长攻略正文被截断（`finish_reason=length`）。guide 温度 0.55（cfca666，由 0.7 下调）：结构化攻略的温度甜点——压格式漂移与牌名串味的采样噪声，保住"反直觉技巧"类内容多样性；相性温度 0.3 维持不变。

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

### 4.5 技能行指针化与结算兜底（prompt_utils._skill_lines，2026-08 新增）

攻略与相性两条 prompt 共用 `_skill_lines(skills, hero_id, rag, indent)` 构建技能段，按**单个技能**决定用指针还是完整描述：

```python
for sk in skills or []:
    name = sk.get("name", "")
    block_id = f"hero_{hero_id}_skill_{name}"
    if rag and block_id in rag:                        # 该技能的语料块已进 prompt
        out.append(f"{indent}- {name}:（完整描述见 [{block_id}] 语料块）")
        continue
    line = f"{indent}- {name}: {sk.get('description', '')}"
    settlement = sk.get("settlement", "")
    if settlement:
        line += f" ｜结算：{settlement}"               # 结算说明只在回退路径带出
    out.append(line)
```

> **设计思路：** RAG 语料块里的技能描述比 heroes.json 更全（含时机/触发条件标签），已注入时再重复一遍完整描述纯浪费 token，改为指向块 id 让模型自己读；但"已注入"必须按技能逐一判定——预算挤掉某个块、检索未召回某个技能、或语料块 id 格式漂移时，该技能必须回退完整描述（含结算），否则模型连这个技能的基本规则都看不到。指针判定的失败方向是良性的（多带一遍完整描述），不会漏规则。结算说明是"技能触发后如何结算"的关键机制，仅存在于回退路径的完整描述里；因此上游构建 skills 字典时必须带 `settlement` 字段（UI 全量/增量链路与指定武将选择对话框曾漏传，导致攻略与相性两条链路的技能行都缺结算说明，已修复）。

### 4.6 生成任务互斥锁（generation_lock.py，9180cf0）

```python
def acquire_generation_lock(path: str | Path) -> GenerationLock | None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT)
    except OSError as error:
        logger.warning("生成锁文件创建失败，本次不加锁继续: %s: %s", path, error)
        return GenerationLock(path, -1)      # 哑锁降级：锁机制故障不阻断生成
    try:
        _try_lock_byte(fd)                   # Windows msvcrt / POSIX fcntl 非阻塞独占锁
    except OSError:
        holder = _read_holder(_holder_path(path))
        logger.warning("生成锁已被其他任务占用（最近持有记录: %s）", holder or "未知")
        os.close(fd)
        return None                          # 已被占用：调用方拒绝启动并 exit 1
    _write_holder(path)                      # 旁路 *.holder 写 pid + 获取时间（仅占用提示）
    return GenerationLock(path, fd)
```

> **设计思路：** 生成结果按"启动时快照 + 全量覆盖"分批提交，进程内互斥挡不住跨进程的文件写——OS 文件锁是唯一不需要守护进程、崩溃后也能自愈的方案：持锁进程退出（含崩溃、断电）时锁由系统自动释放，不留下需要人工清理的残留锁。持有者信息写在旁路 `*.holder` 文件而非锁文件本体，因为 Windows 字节锁会拒绝其他句柄对锁文件的常规读取；holder 仅为占用提示，进程崩溃后可能残留旧记录，因此互斥判定一律以 OS 锁为准（文案用"最近持有记录"而非"持有者"）。锁文件不可用时返回哑锁（`fd=-1`，`release()` 中 `os.close` 被跳过）降级放行，保持无锁旧行为，避免锁机制自身故障把生成入口变成永久拒绝。

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
| `--heroes-file` | `data/heroes.json` | 武将数据文件（UI 增量/指定模式会传入临时武将文件） |
| `--guides-file` | `data/guides.json` | 攻略输出路径 |
| `--synergies-file` | `data/synergies.json` | 相性输出路径 |
| `--score-threshold` | 0 | 相性评分下限 |
| `--verbose` / `-v` | False | 详细日志 |
| `--no-rag` | False | 禁用 RAG 语料增强（默认启用） |
| `--retry-failed` | False | 定向重试（c88bf63，仅 `--guide` 模式）：读取上次失败清单 `logs/ai_last_failures.json`，heroes 过滤到失败项（按 name 或 id 匹配）并强制 update 模式，成功项字节不动；全部成功后自动清除清单；结束打印 `[定向重试] 上次失败 N 项，本次恢复 M 项，仍失败 K 项`。上次运行被中断（清单含 `interrupted: true`）时明确拒绝并 `exit 1`——循环未跑完失败项未知，避免按残留的过期项重跑错项（74ad9b9） |
| `--rebuild-rag-index` | False | 重建 RAG 向量索引后退出（建出索引 `exit 0`，否则 `exit 1`） |

### AIBatchGenerator 公开方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `generate_guide` | `(hero: dict) -> tuple[dict\|None, dict\|None]` | 为单个武将生成攻略 |
| `generate_synergy` | `(hero_a: dict, hero_b: dict) -> tuple[dict\|None, dict\|None]` | 为武将对生成相性评分 |
| `complete` | `(messages: list[dict], temperature: float = 0.7, label: str = "chat") -> dict\|None` | 公开对话补全接口（供业务层调用）；`label` 为业务方标识（如语料块 block_id）并进 token 用量日志——complete 不经 `_request_content`，须在此补记，否则该链路消耗在日志中不可见（9ccc2fc） |
| `cancel` | `() -> None` | 请求中断：重试循环将在下次循环开头退出 |
| `close` | `() -> None` | 关闭 HTTP 客户端 |

构造参数：`api_key` / `api_url` / `model` / `provider`（默认 `"deepseek"`）/ `requests_per_minute`（默认 30）/ `max_retries`（默认 3）/ `http_timeout`（默认 300 秒）/ `max_output_tokens`（默认 `MAX_OUTPUT_TOKENS` = 32768，单源 `env.DEFAULT_MAX_OUTPUT_TOKENS`）。构造期先按 `PROVIDER_PRESETS[provider].requires_key` 校验 Key，不满足直接抛 `ValueError`（ollama 等本地服务可空 Key）；`provider` 还决定请求体是否注入 `thinking` 私有参数。

### 公共函数

| 函数 | 文件 | 说明 |
|------|------|------|
| `extract_json(text)` | `json_extract.py` | 4 策略宽容提取 JSON；失败抛 `ValueError` |
| `validate_guide(raw)` | `utils.py` | Pydantic 校验攻略 |
| `validate_synergy(raw)` | `utils.py` | Pydantic 校验相性 |
| `normalize_and_validate_guide(raw, hero)` | `utils.py` | 攻略后处理管线（注 hero_id / synergizes_with 转 int / 必填预检 / Pydantic 校验），API 与浏览器双生成器共用（P1-5 收口，浏览器版经 `logger_prefix` 保留 `[攻略]` 前缀日志） |
| `normalize_and_validate_synergy(raw, hero_a, hero_b)` | `utils.py` | 相性后处理管线（注双 ID / combat_synergy→combo_ceiling 兼容 shim / 必填预检 / Pydantic 校验），双生成器共用 |
| `has_required_guide_fields(raw)` | `utils.py` | 攻略必填字段预检（key_points/description 存在、正文 ≥200 字、不含模板占位符标记「此处放入 / 放入此字段 / 保持原文不变」），命中缺失可省去一次 Pydantic 异常开销 |
| `has_required_synergy_fields(raw)` | `utils.py` | 相性必填字段预检（score/description 存在、正文 ≥200 字、同一组占位符标记） |
| `load_core_rules()` | `rule_summary.py` | 加载核心规则摘要全文（RAG 关闭兜底）；缺失返回空串。读运行时可写根 `PROJECT_ROOT/data/rag_corpus/核心规则摘要.md`（6b21d75） |
| `load_card_system()` | `rule_summary.py` | 加载核心规则摘要的「卡牌体系」段（RAG 开启时防牌名串味兜底）；缺失返回空串 |
| `estimate_cost(count, mode, model, use_rag=True)` | `prompt_utils.py` | 按模型价格表预览 Token 和费用；`use_rag=False` 为经典模式（输入更少）；未知模型不估价 |
| `estimate_item_cost(item_count, mode, model, use_rag=True)` | `prompt_utils.py` | 按实际 API 请求项数预览 Token 和费用，用于指定范围的相性生成 |
| `build_rag_context(hero, max_chars)` | `rag_prompt.py` | 检索并格式化攻略 RAG 语料区块；异常返回空串 |
| `build_synergy_rag_context(hero_a, hero_b, max_chars)` | `rag_prompt.py` | 检索并格式化相性 RAG 语料区块（目标武将块 + 过滤后的跨类块） |
| `_format_rag_chunks(core_blocks, extra_blocks, budget, core_ratio)` | `rag_prompt.py` | 官方/社区两段独立预算池拼装（整块丢弃不截断，社区池内 combo 优先）；内部使用，供两条 RAG 上下文函数共用 |
| `_skill_lines(skills, hero_id, rag, indent)` | `prompt_utils.py` | 技能段构建：语料块已注入的技能指针化省 token，其余回退完整描述并附结算后缀；攻略与相性两条 prompt 共用 |
| `is_rag_enabled()` | `rag_prompt.py` | RAG 增强开关：环境变量 `RAG_ENABLED`（`--no-rag` 覆盖）优先，其次 config.env |
| `acquire_generation_lock(path)` | `generation_lock.py` | 尝试获取生成任务互斥锁（OS 级文件锁，Windows `msvcrt` / POSIX `fcntl` 对第 0 字节非阻塞独占锁）：占用返回 `None`（调用方拒绝启动并 `exit 1`）；锁文件不可用返回哑锁（`fd=-1`）降级放行。旁路 `*.holder` 记录持有者 PID + 获取时间（9180cf0） |
| `GenerationLock.release()` | `generation_lock.py` | 关闭句柄即解锁（哑锁跳过 `os.close`）；持锁进程退出/崩溃时锁由 OS 自动释放；`main()` 在 `finally` 中调用 |
| `load_heroes(path)` | `utils.py` | 通过 `HeroManager` 完整校验武将 JSON；任一错误均拒绝部分加载 |
| `_save_json(path, data)` | `utils.py` | 原子写入 JSON（9737d52 起委托 `json_repository.atomic_write_json`，mkstemp 唯一临时名——跨进程写同一路径不再共用固定临时名） |

`ai/batch.py` 的断点加载通过 `GuideManager` / `SynergyManager` 逐条校验。发现无效 JSON、错误记录或重复键时，原文件先保留为同目录 `.corrupt-时间戳.json`，随后仅将通过校验的记录原子写回；如果备份失败，任务中止且不覆盖原文件。

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.models` / `src.data.{hero,guide,synergy}_manager` | 使用 Hero / HeroGuide / SynergyScore 模型进行 Pydantic 校验；断点加载经 HeroManager / GuideManager / SynergyManager 逐条校验，发现错误时备份损坏文件 |
| 依赖 | `src.config.env` | `resolve_api_config()` 是任务侧唯一 API 解析入口（多 API 档案 `config/api_profiles.json` 启用档案优先 → config.env 旧键 → 环境变量 → 默认值，同时只允许一个启用档案）；`PROVIDER_PRESETS` 决定供应商语义（`requires_key` 与默认端点，含 deepseek / openai / ollama / openai-compatible）；`get_runtime_params()` 提供 RPM / 最大重试 / HTTP 超时 / 输出 token 上限（`MAX_OUTPUT_TOKENS` 默认 32768，与 `api_generator.MAX_OUTPUT_TOKENS` 同源于 `DEFAULT_MAX_OUTPUT_TOKENS`，1bfd65b）；`get_model_pricing()` 提供价格表用于成本估算 |
| 依赖 | `src.rag`（config/indexer/retriever） | ChromaDB 向量检索、bge-small-zh 嵌入与关键词 RRF 混合检索；`Retriever` 含武将/牌名倒排 `_hero_index` 与 KEYWORDS 关键词倒排 `_keyword_index`；`hero_blocks()`/`_keyword_hits()` 不再线性遍历全量块；检索默认只召当前版本块（`is_current`） |
| 依赖 | `src.scraper.ai.prompt_utils` | 共享 prompt 构建函数（`load_prompt`/`build_guide_prompt`/`build_synergy_prompt`/`_skill_lines`），消除 API 与浏览器生成器之间的代码重复 |
| 依赖 | 操作系统文件锁 | `acquire_generation_lock()`（`generation_lock.py`）对 `logs/ai_generation.lock` 第 0 字节加非阻塞独占锁（Windows `msvcrt.locking` / POSIX `fcntl.flock`）实现跨进程生成互斥；旁路 `logs/ai_generation.lock.holder` 记录持有者 PID + 获取时间；两文件均在运行时状态目录、已 gitignore（9180cf0） |
| 被调用方 | `src.business.fetching.guide_fetch_service` | 通过 QProcess 启动 AI 攻略生成（`--guide` + 可选 `--heroes-file` / `--update` / `--browser` / `--no-rag`） |
| 被调用方 | `src.business.fetching.synergy_fetch_service` | 通过 QProcess 启动 AI 相性生成（`--synergy-pair` / `--synergy-single` / `--synergy-list` + 可选 `--update` / `--browser` / `--no-rag`）。指定配对入口自 2f4a966 起由 `src/ui/generation/synergy_pair_dialog.py` 按三态筛选（未生成 / 待更新 / 已有相性）+ 四策略（仅补缺失 / 补缺失并刷新落后 / 仅刷新落后 / 全部重新生成）组装精确配对清单，恒带覆盖开关经 `--synergy-list` 提交，成本估算与进度按精确对数计算 |
| 被调用方 | `src.business.fetching.base_fetch_service`（父进程侧，非本模块） | 看门狗改为"无进度上限"：识别本模块 stdout 进度行（START/OK/FAIL/SKIP/重试/休息/RAG/开始）后 `_feed_watchdog` 用 `stop()+start()` 强制重启 30 分钟计时（f1b13f8）；取消链路在 Starting 阶段延后树杀、清理轮询 100 轮上限、取消期间出错复位（f10b477）；退出经 `AppServices.shutdown_fetch_services` 限时等进程树真正退出（1d8334a） |
| 被调用方 | `src.ui.app.main_window` | 菜单「数据 → 攻略生成 / 武将相性」子菜单触发生成（经 `src.ui.generation.ai_generation_workflow`） |
| 被调用方 | `src.business.rag.refinement_service` / `src.business.maintenance.classification_suggest` / `src.scripts.propose_rule_changes` / `src.scripts.run_synergy_drift` | 复用 `AIBatchGenerator.complete()` 做通用对话补全（语料精炼建议、武将分类建议、规则变更提案、相性漂移采样） |

---

## 七、本轮文档校准（2026-10-06）

自基线 `885ea96`（2026-10-02 校准）以来的变更：

- **httpx 超时分层化**（4720366）：`_build_timeout()` 返回 `httpx.Timeout(connect=5.0, pool=5.0, write=30.0, read=http_timeout)`，`__init__` 与连接类异常重试重建两处共用——单武将失败最坏约 15 分钟 → 约 15 秒快速失败
- **输出额度上调**（cf9c927）：`MAX_OUTPUT_TOKENS` 16384 → 32768（三处同步：api_generator 常量 / env.get_runtime_params 默认 / config.env.example）
- **AI 定向重试**（c88bf63）：`--retry-failed`（仅 --guide）+ 失败清单 `logs/ai_last_failures.json` 落盘/清除，成功项字节不动
- **写前快照**（d2c5b83）：main() 加载前对 guides/synergies 各快照一次（每次运行一次，非每批一次）
- **prompt 强化**（cfca666，详见 3.6.1 节）：相性四层决策门 + 锚例校准（10 对样本漂移 20%→0%）；攻略 JSON 纪律 + 联动判定强化 + 牌名核对（20 武将样本 JSON 抛错率 20%→0%）；guide 温度 0.7 → 0.55（相性 0.3 不变）；解析代码零改动
- **原子写收敛**（9737d52）：`utils._save_json` 委托 `json_repository.atomic_write_json`

---

## 八、本轮文档校准（2026-10-11）

自基线 `0ffed36`（2026-10-06 校准）以来的变更：

- **双生成器后处理管线收口**（cd97677，纯重构，P1-5）：api/browser 双生成器逐字复制的攻略/相性后处理管线收口为 `utils.normalize_and_validate_guide(raw, hero, *, logger_prefix="")` / `normalize_and_validate_synergy(raw, hero_a, hero_b, *, logger_prefix="")` 单一实现（注 hero_id / 双武将 ID、`synergizes_with` 转 int、combat_synergy→combo_ceiling 兼容 shim 仅当 `combat_synergy` 在且 `combo_ceiling` 不在（带 info 日志）、必填预检、Pydantic 校验；失败路径 logger.warning/debug）；`api_generator` 两处约 10 行内联管线缩为一行调用（返回 `(result, usage)` 不变），`browser_generator` 传 `logger_prefix` 原样保留 `[攻略] {hero名}: ` / `[相性] {A} <-> {B}: ` 日志前缀（进度窗口解析日志行依赖，前缀语义必须不变）；`browser_generator` 模块头 ETL 图注释同步。3.2 生成流程图与公共函数表已随该 commit 同步入文档
- **管线直测**（cd97677）：新增 `tests/test_ai_postprocess.py`（84 行 10 项直测，全部走真实 Pydantic 校验：ID 注入与缺省 / int 转换失败路径 / shim 触发与共存优先 / 缺字段 / 过短正文 / 占位符正文 / 非正 ID 模型拒绝）；`test_ai_generation.py` 6 处 `validate_*` 打桩点改 `normalize_and_validate_*`
- **MAX_OUTPUT_TOKENS 单源**（1bfd65b）：`env.DEFAULT_MAX_OUTPUT_TOKENS = 32_768`（键元表 runtime 域登记），`api_generator.MAX_OUTPUT_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS`——此前 api_generator 常量与 `get_runtime_params` 默认值两处硬编码 32_768 靠注释同步；DeepSeek 请求 `max_tokens` 实际值不变
- **默认数据路径归运行时可写根**（6b21d75）：`ai/batch.py DEFAULT_DATA_DIR = PROJECT_ROOT/"data"`（heroes / guides / synergies 默认文件与失败清单随之）——frozen 下 BUNDLE_ROOT（`<exe>/_internal`）只读，原落点致抓取与生成结果 UI 不可见；开发态两根相等无差异（抓取侧 full.py / incremental.py 同批治理，见 module_scraper.md）
- **核心规则摘要兜底归根**（6b21d75）：`rule_summary._CORE_RULES_FILE = PROJECT_ROOT/"data"/"rag_corpus"/"核心规则摘要.md"`，与检索链路（`src/rag/config`）及维护脚本同根——用户对部署副本的修改在 AI 生成防串味兜底中生效（详见 3.6 节）

---

## 九、本轮文档校准（2026-10-23）

自基线 `2081b5b`（2026-10-11 校准）以来的变更，代码基线更新至 `a63bc3c`：

- **生成任务互斥锁**（9180cf0，VUL-B4-001）：新增 `ai/generation_lock.py`（104 行）——OS 级文件锁（Windows `msvcrt.locking` / POSIX `fcntl.flock`，第 0 字节非阻塞独占锁），持锁进程退出/崩溃时锁由 OS 自动释放、无残留锁；`GENERATION_LOCK_PATH = PROJECT_ROOT/"logs"/"ai_generation.lock"`，旁路 `.holder` 文件记录持有者 PID + 时间；`main()` 入口获取锁、被占用 `sys.exit(1)` 拒绝并发启动、`finally` 中 `lock.release()`；锁文件不可用返回哑锁（`fd=-1`）降级放行。见 3.7 节与 4.6 节
- **AI 批次中断状态**（74ad9b9，TD-A5-004）：`main()` 生成循环包 `try/except Exception` 捕获不可重试异常（401/403 等）记 `fatal_error`，走统一收尾（token 汇总、保留已提交成果、`_save_interrupted_state()` 写 `interrupted: true` + `failed_items: []`、`exit 1`）；`_load_retry_items()` 检测到中断标记明确拒绝 `--retry-failed`，避免按残留过期项重跑错项。见 3.8 节
- **浏览器会话超时重建**（74ad9b9，VUL-B2-001）：`DeepSeekBrowserSession.send_and_wait()` 拆为 `_wait_for_new_message` / `_wait_until_stable` / `_extract_last_reply` 三段，各段独立超时预算（原共用 deadline 致次段几乎无剩余时间）；新增 `_invalidate_page(reason)` 在六处失败点置 `_started=False`，`_ensure_browser()` 检测失效后先 `close()` 释放旧上下文再重建，避免 Edge 进程泄漏与 `user_data_dir` 锁残留。见 3.9 节
- **complete() 补记 token 用量**（9ccc2fc）：`AIBatchGenerator.complete(messages, temperature=0.7, label="chat")` 新增 `label` 参数并在返回后调 `_log_usage()`——complete 不经 `_request_content`，此前语料精炼等通用对话补全链路的 token 消耗在日志中不可见。见 五 节接口表
- **自动化访问披露措辞中性化**（9180cf0）：`_random_rest()` 注释由"避免触发风控"改为"拉开请求间隔、降低访问密度"，`DEFAULT_BROWSER_CONFIG` 与 `_random_rest()` 均指向 TERMS.md 第 7 节「自动化访问与访问频率披露」（外部行为不变）。见 3.9 节注
- **父进程侧联动（非本模块）**：看门狗改"无进度上限"（f1b13f8）、取消链路 Starting 阶段/清理轮询/取消期间出错三处修复（f10b477）、退出经 `shutdown_fetch_services` 限时等进程树真正退出（1d8334a）、相性指定获取四策略 + 三态筛选组装精确配对清单经 `--synergy-list` 提交（2f4a966）。见 3.8 节与 六 节
- **范围提示**：`f1b13f8` / `f10b477` / `1d8334a` / `2f4a966` 的主体改动在 `src/business/fetching/` 与 `src/ui/generation/`，不属本模块；本模块侧仅消费其经由 `--synergy-list` / 进度行协议 / 取消信号传入的行为，本文仅在 3.8 / 3.9 / 六 节做交叉引用，不复制实现细节
