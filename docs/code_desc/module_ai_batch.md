# 模块：AI 批量生成

> 对应目录：`src/scraper/ai_*.py`
> 职责：通过 AI（DeepSeek API 或浏览器自动化）批量生成武将攻略和相性评分

---

## 一、模块职责

本模块是项目的**智能内容生成引擎**：接收武将数据，调用 AI 模型生成攻略或相性评分，从 AI 回复中提取 JSON，经 Pydantic 校验后持久化到数据文件。

核心能力：
- **双模式生成** — API 模式（httpx 直连 DeepSeek）和浏览器模式（Playwright + Edge 自动化操作 DeepSeek 网页版）
- **四种生成模式** — 全量攻略、全量相性、指定配对（2~8 武将 × itertools.combinations）、选定武将 × 全体
- **JSON 提取** — 从 AI 的不规范回复中宽容提取 JSON，支持 4 种回退策略
- **分批原子提交** — 每累计 10 条攻略或相性通过校验的结果即原子写入正式 JSON；失败项保留对应旧数据

---

## 二、文件结构

```
src/scraper/
├── ai_batch.py              # CLI 入口（参数解析 → 配置加载 → 委托子模块）
├── ai_generator.py          # API 调用核心（限速/重试/JSON 提取/Pydantic 校验）
├── ai_playwright.py         # 浏览器自动化生成器（Playwright + Edge）
├── ai_generation.py         # 生成编排函数（run_guide_generation / run_synergy_generation / run_synergy_pair_generation / run_synergy_single_generation）
└── ai_utils.py              # 共享工具（estimate_cost / load_heroes / _save_json）
```

### 模块间调用关系

```
ai_batch.py (CLI 入口)
 ├── 选择生成器: AIBatchGenerator (api) / PlaywrightGenerator (browser)
 └── ai_generation.py → 根据参数分发到:
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
| 成本估算 | ✅ 支持 dry-run | ❌ 不支持 |
| 必备条件 | API Key | 已登录的 Edge 浏览器 |

### 3.2 AIBatchGenerator（API 模式）

```
构造函数 → 创建 httpx.Client() + 初始化限速器
  │
generate_guide(hero)
  ├── load_prompt("docs/prompts/hero_guide.md") → system_prompt
  ├── build_guide_prompt(hero)                  → user_prompt
  ├── _call_api(messages=[system, user])
  │   ├── 限速检查（距上次请求不足 60/RPM 秒则 sleep）
  │   ├── POST /v1/chat/completions
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

`ai_generation.py` 中的 `run_synergy_pair_generation()` 支持选择 2~8 个武将，用 `itertools.combinations` 遍历所有 C(N,2) 组合：

```python
for idx, (ha, hb) in enumerate(itertools.combinations(pair_heroes, 2), start=1):
    result, usage = generator.generate_synergy(ha, hb)
    if result:
        # 保存
    else:
        print(f"FAIL")  # 单对失败不阻断
```

**要点：** 单对失败只打印 FAIL 信息，不影响其他配对继续生成。进度输出 `[i/total]` 与实际配对数同步，UI 层通过正则匹配更新进度条。

### 3.5 任务结果与提交边界

每个编排函数返回 `GenerationResult`，其中包含 token 用量、完成数、跳过数、失败项和提交状态。CLI 只根据该结构化结果决定退出码：任一失败项都会以非零退出，API 与浏览器模式的规则一致；此前已成功的批次不回滚。

单个生成任务每累计 10 条攻略或相性校验成功，即通过临时文件 `replace()` 原子提交到正式 `guides.json` / `synergies.json`；任务结束时会提交不足一批的成功结果。任一失败项只保留原有对应记录，不回滚已成功批次。浏览器模式没有 token usage，不会因缺少 usage 被误判为失败，也不要求 API Key。

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

### 公共函数

| 函数 | 文件 | 说明 |
|------|------|------|
| `_extract_json(text)` | `ai_generator.py` | 4 策略宽容提取 JSON |
| `_validate_guide(raw)` | `ai_generator.py` | Pydantic 校验攻略 |
| `_validate_synergy(raw)` | `ai_generator.py` | Pydantic 校验相性 |
| `estimate_cost(count, mode, model)` | `prompt_utils.py` | 按模型价格表预览 Token 和费用；未知模型不估价 |
| `load_heroes(path)` | `ai_utils.py` | 从 JSON 加载武将数据 |
| `_save_json(path, data)` | `ai_utils.py` | 原子写入 JSON |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.models` | 使用 Hero / HeroGuide / SynergyScore 模型进行 Pydantic 校验 |
| 依赖 | `src.config.env` | 读取 API Key/URL/Model 等配置 |
| 被调用方 | `src.business.guide_fetch_service` | 通过 QProcess 启动 AI 攻略生成 |
| 被调用方 | `src.business.synergy_fetch_service` | 通过 QProcess 启动 AI 相性生成 |
| 被调用方 | `src.ui.main_window` | 菜单"数据 → 攻略/相性"触发生成 |
