# 名将杀 Agent — 项目细节文档

> 版本：v0.1.0  
> 项目路径：`G:\py_savepoint\test_project`  
> 远程仓库：`gitee.com:chen-xianghao920/test_project.git`  
> 文档日期：2026-06-27

---

## 目录

- [一、爬虫与数据采集模块细节](#一爬虫与数据采集模块细节)
- [二、AI 批量生成模块细节](#二ai-批量生成模块细节)
- [三、业务服务层细节](#三业务服务层细节)
- [四、数据管理层细节](#四数据管理层细节)
- [五、UI 层细节](#五ui-层细节)
- [六、QProcess 异步通信机制](#六qprocess-异步通信机制)
- [七、JSON 提取与 ETL 细节](#七json-提取与-etl-细节)
- [八、配置加载与 env 解析细节](#八配置加载与-env-解析细节)
- [九、浏览器自动化细节](#九浏览器自动化细节)
- [十、日志系统细节](#十日志系统细节)
- [十一、测试体系细节](#十一测试体系细节)

---

## 一、爬虫与数据采集模块细节

### 1.1 文件位置与层级关系

```
src/scraper/crawler.py         ← 核心：网络请求、JS 解析、数据清洗、Pydantic 校验
src/scraper/official.py        ← 全量采集 CLI
src/scraper/incremental.py     ← 增量/指定采集 CLI
```

### 1.2 crawler.py 详细说明（349 行）

#### 1.2.1 常量定义

| 常量 | 值 | 用途 |
|------|-----|------|
| `BAIKE_URL` | `https://mjs.ztgame.com/baike/` | 官网百科首页 |
| `BASE_URL` | `https://mjs.ztgame.com` | 用于拼接相对路径 |
| `TIMEOUT` | `30` (秒) | HTTP 请求超时 |
| `MAX_RETRIES` | `3` | 请求失败重试次数 |
| `RETRY_DELAY` | `2` (秒) | 重试间隔 |
| `HEADERS` | Chrome 131 User-Agent | 反爬伪装 |
| `GENDER_MAP` | `{1: "男", 2: "女"}` | 性别编码映射 |
| `SKILL_SECTION_TITLES` | 7 个中文标题 | 技能描述段落拆分依据 |

#### 1.2.2 `fetch(url, binary=False) → str | bytes`

- 使用 `urllib.request`（无第三方依赖）
- 支持 `binary=True` 返回原始 bytes（头像下载用）
- 3 次重试，间隔 2 秒，最后一次失败抛异常
- 不可用于异步环境，同步阻塞

#### 1.2.3 JS chunk 解析三件套

**`find_chunk_url(html) → str`**：
- 从百科首页 HTML 中正则匹配 `/_nuxt/mjbk.[a-f0-9]+.js`
- URL 拼接：`BASE_URL + 匹配到的路径`

**`extract_js_array(js_text) → str`**：
- 查找 `const e=[` 定位数组起点
- 括号深度计数器遍历，找到匹配的 `]` 结束
- 返回括号内的 JSON-like 文本字符串

**`js_to_json(text) → list[dict]`**：
- 三步预处理：key 加引号 → `undefined` 替换为 `null` → 移除尾部多余逗号
- 最后 `json.loads()` 解析

#### 1.2.4 数据清洗函数

**`clean_html(html_text) → str`**：
1. 正则去掉所有 `<...>` 标签
2. `html.unescape()` 解码 HTML 实体（`&amp;` → `&` 等）
3. 连续空白压缩为单个空格
4. `strip()` 去除首尾空白

**`split_skill_desc(raw_desc) → dict`**：
- 按 `<p><strong>段落标题</strong></p>` 结构拆分 HTML
- 保留「技能描述」→ `description`
- 保留「结算详情/结算详解/技能详解/技能详情」→ `settlement`
- 丢弃「技能典故」「设计思路」
- 无标题段落整体作为 description

#### 1.2.5 `transform(raw) → dict | None`

字段映射流程：

```
raw["id"]             → hero["id"]              (int, 直接取)
raw["name"]           → hero["name"]            (str, clean_html)
raw["dynasty"]        → hero["faction"]         (str, clean_html)
raw["p_positioning"]  → hero["position"]        (str, clean_html)
raw["p_blood_max"]    → hero["max_hp"]          (int, str→int, 默认4)
raw["p_card_max"]     → hero["max_hand"]        (int, str→int, 默认4)
raw["gender"]         → hero["gender"]          (str, 1→男/2→女, 默认男)
raw["icon_url"]       → hero["icon_url"]        (str, 直接取)
raw["skill"] 遍历     → hero["skills"][]        (list[dict], split_skill_desc)
                       hero["title"]             (str, 固定 "")
                       hero["difficulty"]        (int, 固定 2)
                       hero["mode_viability"]    (dict, 固定 {})
                       hero["last_updated"]      (str, date.today())
```

关键逻辑：
- `id` 和 `name` 缺失时跳过整条数据（返回 None）
- `p_blood_max` / `p_card_max` 转型失败时使用默认值 4，不跳过
- skill 遍历时，`skill_name` 为空跳过该技能，不跳过整个武将

#### 1.2.6 `validate_heroes(heroes) → list[dict]`

- 逐条调用 `Hero.model_validate(h)` 进行 Pydantic 校验
- 校验失败条目标记错误日志并跳过（不中断流程）
- 成功条目标调用 `model_dump(mode="json")` 序列化

#### 1.2.7 `fetch_all_raw() → list[dict]`

快捷组合函数：
1. `fetch(BAIKE_URL)` → 首页 HTML
2. `find_chunk_url(html)` → JS chunk URL
3. `fetch(chunk_url)` → JS 文本
4. `extract_js_array(js_text)` → JSON 文本
5. `js_to_json(...)` → 155 条原始数据

#### 1.2.8 头像下载（第 297-348 行）

**`download_hero_images(raw_list, image_dir, skip_existing) → int`**：
- 遍历 `raw_list`，取 `icon_url` 和 `name`
- `urlparse` 解析 URL 扩展名（默认 `.png`）
- 文件路径：`images/clean_name{ext}`
- `skip_existing=True` 时检查文件存在性
- 使用 `fetch(icon_url, binary=True)` 下载二进制
- 单个失败只打 warning 不影响其他武将

### 1.3 official.py 详细说明

#### 1.3.1 `crawl(dry_run, output_path, skip_images)`

5 步流程 + 统计输出：

| 步骤 | 实现 | 输出 |
|------|------|------|
| [1/5] 定位数据源 | `fetch(BAIKE_URL)` → `find_chunk_url()` | 打印 chunk URL |
| [2/5] 下载 JS | `fetch(chunk_url)` | 打印大小 |
| [3/5] 解析数据 | `js_to_json(extract_js_array())` | 打印原始条数(155) |
| [4/5] 清洗映射 | `[transform(r) for r in raw_list]` | 打印清洗后条数 + 势力分布 |
| [5/5] 校验 | `validate_heroes(transformed)` | 打印通过/失败条数 |

输出阶段：
- `dry_run=True` → 仅预览前 5 条
- 否则 → 写入 `data/heroes.json` + 下载头像

#### 1.3.2 命令行参数

```python
parser.add_argument("--dry-run", action="store_true")     # 预览
parser.add_argument("--output", "-o", type=str)            # 自定义输出
parser.add_argument("--skip-images", action="store_true")  # 跳过头像
parser.add_argument("--verbose", "-v", action="store_true") # 详细日志
```

### 1.4 incremental.py 详细说明

#### 1.4.1 三种采集模式

| 参数 | 功能 | 数据源 | 写入方式 |
|------|------|--------|----------|
| `--incremental` | 只追加本地没有的武将 | 官网全量数据 | append |
| `--hero 诸葛亮,关羽` | 按名称采集（模糊匹配） | 官网全量数据筛选 | replace（指定 ID） |
| `--hero-id 52,114` | 按 ID 采集 | 官网全量数据筛选 | replace（指定 ID） |

**增量去重逻辑**：
1. `load_existing_ids(path)` → 读取本地 JSON 的 ID 集合
2. `incremental_collect(all_raw, existing_ids)` → 差集筛选
3. 配合 `--hero` / `--hero-id` 时，先在差集中再筛选

**替换写入逻辑**：
1. `replace_ids = {r["id"] for r in target_raw}`
2. 在 `run()` 中：读取旧数据 → 过滤掉 `replace_ids` 中的 ID → 合并新数据 → 写入

#### 1.4.2 `run()` 函数（数据清洗与输出）

```python
def run(raw_list, output_path, dry_run, append=False, replace_ids=None, skip_images=False)
```

流程：
1. `[transform(r) for r in raw_list]` → 清洗
2. `validate_heroes(transformed)` → Pydantic 校验
3. `dry_run` → 预览退出
4. 确定写入策略（append / replace / 全覆盖）
5. `json.dump(merged, f, ensure_ascii=False, indent=2)`
6. `download_hero_images(raw_list)`（非 dry_run 时）

---

## 二、AI 批量生成模块细节

### 2.1 模块文件关系

```
ai_batch.py (CLI 入口, 282行)
 ├── 创建 AIBatchGenerator 或 PlaywrightGenerator
 ├── 委托给各 run_* 函数
 │   ├── ai_guide.py           → run_guide_generation()
 │   ├── ai_synergy.py         → run_synergy_generation()
 │   ├── ai_synergy_pair.py    → run_synergy_pair_generation()
 │   └── ai_synergy_single.py  → run_synergy_single_generation()
 └── _save_json() 写入结果
```

### 2.2 ai_batch.py 入口流程

#### 2.2.1 命令行参数（行 154-173）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--guide` | bool | False | 生成攻略 |
| `--synergy` | bool | False | 生成全量相性 |
| `--heroes-file` | str | `data/heroes.json` | 武将数据 |
| `--guides-file` | str | `data/guides.json` | 攻略输出路径 |
| `--synergies-file` | str | `data/synergies.json` | 相性输出路径 |
| `--dry-run` | bool | False | 预览成本 |
| `--score-threshold` | int | 0 | 相性评分下限 |
| `--synergy-pair` | str | None | 指定两武将配对 |
| `--synergy-single` | str | None | 选定武将 vs 全体 |
| `--browser` | bool | False | Playwright 浏览器模式 |
| `--verbose` | bool | False | 详细日志 |

#### 2.2.2 生成器选择逻辑（行 195-206）

```python
if args.browser:
    from src.scraper.ai_playwright import PlaywrightGenerator
    generator = PlaywrightGenerator()
else:
    _check_api_key(api_config)
    generator = AIBatchGenerator(
        api_key=api_config["api_key"],
        api_url=api_config["api_url"],
        model=api_config["model"],
        requests_per_minute=runtime_params["requests_per_minute"],
        max_retries=runtime_params["max_retries"],
        http_timeout=runtime_params["http_timeout"],
    )
```

#### 2.2.3 断点续传机制

- **攻略**：`_load_existing_guides(guide_path)` → 按 `hero_id` 索引
- **相性**：`_load_existing_synergies(synergy_path)` → 按 `sorted([a_id, b_id])` 索引
- 遍历时跳过已存在的 `hero_id` 或 `(a_id, b_id) pair`

#### 2.2.4 浏览器模式的 token 处理（行 278）

浏览器模式返回 `(result, None)`，不以 token 统计判断成败，避免误报失败。

### 2.3 AIBatchGenerator 详细说明（ai_generator.py, 356 行）

#### 2.3.1 构造函数（行 36-57）

```python
def __init__(self, api_key, api_url, model, requests_per_minute, max_retries, http_timeout)
```

- `api_key` 为空时抛 `ValueError`
- `_client = httpx.Client(timeout=http_timeout)` — 同步 HTTP 客户端
- `_min_interval = 60.0 / rpm` — 速率控制（秒/请求）
- `_last_request_time = 0.0` — 上次请求时间戳

#### 2.3.2 API 调用（行 59-99）

```python
def _call_api(self, messages, temperature=0.7) → dict | None
```

请求体：
```json
{
  "model": "deepseek-v4-pro",
  "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
  "temperature": 0.7,
  "max_tokens": 4096
}
```

重试逻辑：
1. 发送前检测距上次请求是否超过 `_min_interval`，不足则 sleep 补齐
2. HTTP 请求 → 检查 `resp.raise_for_status()`
3. 成功 → 更新 `_last_request_time`，返回 `resp.json()`
4. HTTP 错误 / 异常 → `time.sleep(2 ** attempt)` 指数退避（2s/4s/8s）
5. 3 次全部失败 → 返回 None

#### 2.3.3 JSON 提取（行 101-185）

`_extract_json(text)` 四步策略：

1. **直接全文解析**：`_try_all([text])` → `raw_decode` + `_repair_strings`
2. **代码块提取**：正则 `r"```(?:json)?..."` 后 `_try_all`
3. **--- 分隔线**：`rfind("\n---\n")` 取最后一段 → `_try_all`
4. **{...} 区间**：`find("{")` ~ `rfind("}")` → `_try_all`

`_repair_strings(s)` 状态机修复：
- 仅在 `in_string=True` 时操作
- 字面换行 `\n` → `\\n`
- `\\` 转义序列跳过原样保留

`_try_all(candidates)`：
- 逐个尝试 `raw_decode`
- 修复后重试

#### 2.3.4 Prompt 构建

**`_build_guide_prompt(hero) → str`**：
```
武将: 诸葛亮
势力: 蜀
定位: 控制
体力: 4  手牌: 4
性别: 男
难度: 2
技能:
  - 观星: 控制牌堆
```

**`_build_synergy_prompt(hero_a, hero_b) → str`**：
```
## 武将 A: 诸葛亮
  势力: 蜀
  定位: 控制
  体力/手牌: 4/4
  技能:
    - 观星: 控制牌堆

## 武将 B: 曹操
  势力: 魏
  定位: 防御
  体力/手牌: 5/4
  技能:
    - 奸雄: 获得牌
```

#### 2.3.5 Pydantic 校验（行 241-259）

```python
@staticmethod
def _validate_guide(data) → dict | None
    HeroGuide.model_validate(data) → model_dump(mode="json")

@staticmethod
def _validate_synergy(data) → dict | None
    SynergyScore.model_validate(data) → model_dump(mode="json")
```

- 延迟导入 `src.data.models`（避免循环依赖）
- 校验失败返回 None（不抛异常）
- 校验成功返回标准的 Python dict

#### 2.3.6 `generate_guide(hero) → (dict | None, dict | None)`

```
load_prompt(hero_guide.md)    → system_prompt
_build_guide_prompt(hero)     → user_prompt
_call_api([system, user])     → response JSON
_extract_json(response.text)  → raw dict
raw["hero_id"] = hero.id
_convert_ids_to_int()         → ID 字段转 int
_validate_guide(raw)          → Pydantic 校验
return (validated_dict, usage_dict)
```

#### 2.3.7 `generate_synergy(hero_a, hero_b) → (dict | None, dict | None)`

同 `generate_guide` 但：
- 使用 `synergy_score.md` prompt 模板
- 注入 `hero_a_id` + `hero_b_id`
- 兼容旧字段：`combat_synergy` → `combo_ceiling`
- 使用 `_validate_synergy` 校验

### 2.4 ai_guide.py — 攻略生成循环

```python
def run_guide_generation(heroes, generator, guide_path, existing_guides, api_config)
```

流程：
1. 遍历所有武将
2. 跳过已存在（断点续传）
3. 输出 `"[武将名] 开始..."`（触发进度条状态文字）
4. `generator.generate_guide(hero)` → `(result, usage)`
5. 累计 usage（API 模式）/ 跳过（浏览器模式）
6. 成功则追加到 `new_guides` 列表
7. 每 10 条（`GUIDE_BATCH_SAVE_INTERVAL`）批量保存
8. 结束后最终保存

### 2.5 ai_synergy.py — 全量相性生成

遍历所有 `N*(N-1)/2` 对组合：
```
for i in range(len(heroes)):
    for j in range(i+1, len(heroes)):
        key = tuple(sorted([ha["id"], hb["id"]]))
        if key in existing_synergy_keys: continue
        generator.generate_synergy(ha, hb)
        if result and result["score"] >= threshold: save
```

每 20 条（`SYNERGY_BATCH_SAVE_INTERVAL`）批量保存。

### 2.6 ai_synergy_pair.py — 指定配对

- 读入 2 个武将的 JSON 文件
- 校验恰好 2 个
- 先生成 → 成功后再删除旧数据（避免失败数据丢失）
- 立即保存

### 2.7 ai_synergy_single.py — 选定武将 x 全体

- 读入 1 个武将的 JSON 文件
- 先删除该武将所有旧相性数据
- 再逐个生成与其他武将的相性
- 结束后一次性保存

---

## 三、业务服务层细节

### 3.1 服务类一览

| 类 | 文件 | 行数 | 父类 | 信号数量 |
|--------|------|------|------|----------|
| HeroFetchService | `fetch_service.py` | ~102 | QObject | 3 |
| GuideFetchService | `guide_fetch_service.py` | ~179 | QObject | 6 |
| SynergyFetchService | `synergy_fetch_service.py` | ~104 | QObject | 3 |

### 3.2 HeroFetchService

#### 信号

```python
status_changed = Signal(str)      # 状态文字
fetch_completed = Signal(bool)    # True=成功, False=失败
error_occurred = Signal(str)      # 错误信息
```

#### 方法

| 方法 | 调用的 CLI | 参数 |
|------|-----------|------|
| `fetch_all()` | `-m src.scraper.official` | 无 |
| `fetch_incremental()` | `-m src.scraper.incremental --incremental` | 无 |
| `fetch_specific(hero_ids)` | `-m src.scraper.incremental --hero-id ...` | ID 列表（逗号拼接） |
| `cancel()` | `process.kill()` | 无 |

#### 信号连接模式

```
status_changed → self._on_fetch_status (状态栏)
fetch_completed → self._on_fetch_completed (弹窗提示)
error_occurred → self._on_fetch_error (弹窗警告)
```

### 3.3 GuideFetchService

#### 信号

```python
status_changed = Signal(str)               # 状态文字
cost_estimated = Signal(dict)              # 成本估算（API 模式）
progress_output = Signal(str)              # 子进程 stdout 行
progress_value = Signal(int, int)          # 进度条 (current, total)
fetch_completed = Signal(bool, str)        # (成功/失败, 消息)
error_occurred = Signal(str)               # 错误信息
```

#### 三个 fetch 方法统一后端参数

每个方法新增 `backend` 参数（`"api"` 或 `"browser"`），不再 emit `cost_estimated`（UI 层已通过 `BackendChooseDialog` 完成确认），直接调用 `execute_with_confirmation()`。

#### `execute_with_confirmation()` 逻辑

1. 读取 `self._context` 中的 `mode`、`heroes`、`backend`
2. 构建 `base_args = ["-m", "src.scraper.ai_batch", "--guide"]`
3. `backend == "browser"` → 追加 `--browser`
4. `mode == "specific"` → 写入临时文件，追加 `--heroes-file`
5. 启动 QProcess

#### 子进程错误日志增强

- `SeparateChannels` 模式：分别读取 stdout 和 stderr
- `readyReadStandardError` → 实时输出到日志
- `_on_finished` 非零退出 → 输出**完整 stdout + stderr**
- `_on_error` 输出错误类型名（`FailedToStart` / `Crashed` / `Timedout` / `WriteError` / `ReadError`）

### 3.4 SynergyFetchService

同 GuideFetchService 模式，但 args 不同：
- `fetch_pair` → `--synergy-pair <tmp_file>`
- `fetch_single` → `--synergy-single <tmp_file>`
- 同样支持 `backend` 参数追加 `--browser`

---

## 四、数据管理层细节

### 4.1 文件与数据量

| 数据文件 | 管理类 | 数据量 |
|----------|--------|--------|
| `data/heroes.json` | HeroManager | 155 武将 |
| `data/synergies.json` | SynergyManager | 若干相性对 |
| `data/guides.json` | GuideManager | ~42 份攻略 |
| `data/cards.json` | — | 基础卡牌 |

### 4.2 HeroManager 方法清单

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `add_hero(hero)` | Hero | None | 已存在抛 ValueError |
| `get_hero(hero_id)` | int | Hero \| None | 精确 ID 查找 |
| `get_hero_by_name(name)` | str | Hero \| None | 精确名称查找 |
| `search_heroes(keyword)` | str | list[Hero] | 模糊匹配 id/name/title/faction |
| `update_hero(hero)` | Hero | None | 覆盖式 upsert |
| `delete_hero(hero_id)` | int | None | 不存在静默退出 |
| `list_heroes()` | — | list[Hero] | 全部（已排序） |
| `list_factions()` | — | list[str] | 所有势力名称 |
| `list_heroes_by_faction(faction)` | str | list[Hero] | 势力筛选 |

### 4.3 SynergyManager 方法清单

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `add_synergy(score)` | SynergyScore | None | (A,B) 或 (B,A) 已存在抛 ValueError |
| `get_synergy(a_id, b_id)` | (int, int) | SynergyScore \| None | 自动排序 key |
| `update_synergy(score)` | SynergyScore | None | 覆盖 |
| `delete_synergy(a_id, b_id)` | (int, int) | None | 自动排序 key |
| `list_synergies()` | — | list[SynergyScore] | 全部 |
| `list_synergies_for_hero(hero_id)` | int | list[SynergyScore] | 该武将涉及的所有相性 |

**双向归一实现**：
```python
def _make_key(self, a_id: int, b_id: int) -> tuple[int, int]:
    return tuple(sorted([a_id, b_id]))
```
`(A=114, B=115)` 和 `(A=115, B=114)` 均映射到 `(114, 115)`。

### 4.4 GuideManager 方法清单

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `add_guide(guide)` | HeroGuide | None | 同一 hero_id 重复抛 ValueError |
| `get_guide(hero_id)` | int | HeroGuide \| None | 按武将 ID 查询 |
| `update_guide(guide)` | HeroGuide | None | 覆盖 |
| `delete_guide(hero_id)` | int | None | 不存在静默 |
| `list_guides()` | — | list[HeroGuide] | 全部 |

### 4.5 DataFacade 门面

```python
class DataFacade:
    heroes: HeroManager
    synergies: SynergyManager
    guides: GuideManager

    def load_all(self) → None      # 三个 load() 依次调用
    def save_all(self) → None      # 三个 save() 依次调用
    def get_stats(self) → dict     # 返回 {heroes: N, synergies: N, guides: N}
```

### 4.6 增量更新

```python
def apply_incremental_update(data_dir, update)
```

支持 `IncrementalUpdate` 模型中的三类变更：

| 变更类型 | 处理方式 |
|---------|----------|
| `added_heroes` | `hero_mgr.add_hero()`，已存在则 warning 跳过 |
| `modified_heroes` | `hero_mgr.update_hero()` |
| `removed_heroes` | 删除 hero + 关联的 synergy 和 guide |

---

## 五、UI 层细节

### 5.1 文件行数统计

| 文件 | 行数 | 组件层级 |
|------|------|----------|
| main_window.py | 512 | QMainWindow（顶层） |
| hero_browser.py | 428 | QWidget（Tab 内嵌） |
| recommendation_panel.py | 398 | QWidget（Tab 内嵌） |
| hero_select_dialog.py | 293 | QDialog（基类） |
| backend_choose_dialog.py | 105 | QDialog |
| guide_progress_dialog.py | 135 | QDialog |
| cost_confirm_dialog.py | 78 | QDialog |
| settings_dialog.py | 297 | QDialog |
| style.py | 247 | 样式表常量 |
| fetch_dialog.py | 15 | QDialog（继承基类） |
| guide_fetch_dialog.py | 15 | QDialog（继承基类） |
| synergy_pair_dialog.py | 30 | QDialog（继承基类） |
| synergy_single_dialog.py | 30 | QDialog（继承基类） |

### 5.2 主窗口信号拓扑

```
MainWindow.__init__
 ├── HeroFetchService
 │   ├── status_changed → _on_fetch_status (更新状态栏)
 │   ├── fetch_completed → _on_fetch_completed (弹窗提示)
 │   └── error_occurred → _on_fetch_error (弹窗警告)
 ├── GuideFetchService
 │   ├── status_changed → _on_fetch_status
 │   ├── cost_estimated → _on_guide_cost_estimated (弹出 CostConfirmDialog)
 │   ├── fetch_completed → _on_guide_fetch_completed (更新进度条/重新加载)
 │   ├── error_occurred → _on_guide_fetch_error (带详情的错误弹窗)
 │   ├── progress_output → _on_guide_progress (更新进度文字)
 │   └── progress_value → _on_guide_progress_value (更新进度条数值)
 └── SynergyFetchService
     ├── status_changed → _on_fetch_status
     ├── fetch_completed → _on_synergy_fetch_completed (弹窗+重新加载)
     └── error_occurred → _on_synergy_fetch_error (弹窗警告)
```

### 5.3 后端选择对话框（BackendChooseDialog）

**布局**：
```
┌──────────────────────────────────────────────────────┐
│ 标题: 选择生成方式                                     │
│ ┌────────────────┐ ┌────────────────────────────┐    │
│ │  API 方式       │ │  浏览器方式                │    │
│ ├────────────────┤ ├────────────────────────────┤    │
│ │ 模式: 全量获取  │ │ 浏览器模式：通过           │    │
│ │ 需要生成的项数  │ │ Playwright+Edge 自动化     │    │
│ │ 预估 Token     │ │ 操作 DeepSeek 网页版       │    │
│ │ 预估费用       │ │                             │    │
│ └────────────────┘ └────────────────────────────┘    │
│              [确定执行]  [取消]                       │
└──────────────────────────────────────────────────────┘
```

**Tab 切换逻辑**：
```python
def _on_accept(self):
    idx = self._tabs.currentIndex()
    self._selected_backend = "browser" if idx == 1 else "api"
    self.accept()
```

### 5.4 攻略生成进度条（GuideProgressDialog）

**UI 组成**：
- 状态文字（"正在生成 XXX 的攻略..."）
- 进度条（`current / total`）
- 详情标签（灰色，12px）
- 错误标签（红色，隐藏）
- 关闭按钮（执行中禁用，完成时启用）

**进度更新正则**：
```python
m = re.search(r"\[(\d+)/(\d+)\]\s*(.+?)\s+(?:OK|FAIL)", text)
```
匹配格式 `"[1/3] 诸葛亮 OK"`，仅在成功/失败后更新进度条，不提前跳进度。

### 5.5 武将浏览器（HeroBrowser）

由两个子组件构成：

```
HeroBrowser (QWidget)
 ├── HeroListPanel (左, 280px)
 │   ├── QLineEdit（搜索框）
 │   ├── QComboBox（势力筛选）
 │   ├── QListWidget（武将列表）
 │   └── Signal: hero_selected(int)
 └── HeroDetailPanel (右, 520px)
     ├── QTabWidget
     │   ├── Tab 1「武将信息」
     │   │   ├── QLabel (HTML 渲染基本信息)
     │   │   └── QScrollArea (技能列表)
     │   └── Tab 2「攻略指南」
     │       └── QTextBrowser (mistune 渲染 Markdown)
     └── Method: show_hero(hero_id)
```

**Markdown 渲染**：使用 `mistune.html(text)` 替代手写正则。

**名称过滤**：用户输入搜索文本 + 选择势力后立即过滤，列表实时刷新。

### 5.6 选将推荐（RecommendationPanel）

```
RecommendationPanel (QWidget)
 ├── 标题: "选将推荐"
 └── QGridLayout (4行 × 2列)
      └── HeroCardWidget × 8
           ├── 头像区 (宽 130px)
           │   ├── QPixmap (从 images/name.png 加载)
           │   ├── QGridLayout 叠加
           │   │   ├── 名称浮层 (底部, rgba(0,0,0,140))
           │   │   └── 势力标签 (左上角, 色块)
           └── 信息区 (弹性)
               ├── 势力色块 + 武将名 (粗体 15px)
               ├── 推荐指数 (★★★★☆ 98.23%)
               ├── 分隔线
               ├── 高相性组合标题
               ├── QGridLayout (2列, 搭配+评分)
               ├── 分隔线
               └── 胜率 (灰色占位)
```

**势力色表**（`FACTION_COLORS`）：
```python
FACTION_COLORS = {
    "秦": "#8B4513", "汉": "#B22222", "楚": "#2F4F4F",
    "赵": "#556B2F", "魏": "#800020", "燕": "#6A0DAD",
    "齐": "#1B7A3D", "韩": "#CD853F",
    "孙吴": "#4169E1", "蜀": "#228B22", "曹魏": "#800020",
    "群雄": "#8B0000", "晋": "#4A6741", "新朝": "#B8860B",
    # 默认: "#888"
}
```

**默认数据**：按 id 排序取前 8 个武将，自动加载已有相性数据。

**数据接口**：
```python
def update_recommendations(self, data: list[dict]) → None
```
接收格式：
```json
[
  {"index": 1, "name": "诸葛亮", "confidence": 0.9823},
  {"index": 2, "name": "司马懿", "confidence": 0.9501}
]
```

### 5.7 对话框基类体系

```
BaseHeroSelectDialog (hero_select_dialog.py, ~293行)
 ├── SelectionMode 枚举: MULTI / MULTI_LIMIT / SINGLE
 ├── ReturnFormat 枚举: IDS / HEROES_DICT
 ├── 搜索框 + 势力网格 + 复选框列表 + 已选计数 + 确认/取消
 │
 ├── HeroFetchDialog (fetch_dialog.py, ~15行)
 │   SelectionMode=MULTI, ReturnFormat=IDS
 │
 ├── GuideFetchDialog (guide_fetch_dialog.py, ~15行)
 │   SelectionMode=MULTI, ReturnFormat=HEROES_DICT
 │
 ├── SynergyPairDialog (synergy_pair_dialog.py, ~30行)
 │   SelectionMode=MULTI_LIMIT, max_selection=2
 │
 └── SynergySingleDialog (synergy_single_dialog.py, ~30行)
     SelectionMode=SINGLE
```

### 5.8 全局样式（style.py, 247 行）

**颜色方案**：

| 元素 | 颜色 |
|------|------|
| 主色调 | `#4a90d9`（天蓝） |
| 悬停 | `#357abd` |
| 按下 | `#2a6cb5` |
| 背景 | `#f0f4f8` |
| 面板底色 | `#dce6f0` |
| 文字 | `#2c3e50` |
| 次要文字 | `#4a6a8a` |
| 边框 | `#b0c4de` |

**字体**：`"Microsoft YaHei UI", "微软雅黑", "SimHei", sans-serif`，13px 基础字号。

---

## 六、QProcess 异步通信机制

### 6.1 进程通信模式

```
┌─────────┐   stdout(UTF-8)   ┌──────────────┐
│ 父进程   │ ←────────────── │ 子进程       │
│ (UI)    │   stderr(UTF-8)   │ (CLI 脚本)   │
│         │ ←────────────── │              │
│         │   finished(int)   │              │
│         │ ←────────────── │              │
└─────────┘                  └──────────────┘
```

### 6.2 通道分离

**最新改进**：三个业务服务全部使用 `SeparateChannels` 模式：

```python
self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
self._process.readyReadStandardError.connect(self._on_stderr_ready)
```

vs 旧版的 `MergedChannels`（无法区分 stdout 和 stderr）。

### 6.3 子进程编码修复

所有 CLI 脚本入口的 Windows 编码修复：

```python
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
```

确保子进程输出的中文日志在父进程正确显示。

### 6.4 完整错误输出链路

```
子进程 exit_code ≠ 0
     ↓
_on_finished()
     ├── logger.warning("进程退出码 %d")
     ├── [子进程 stdout 完整输出] → logger.warning()
     └── [子进程 stderr 完整输出] → logger.warning()

子进程 QProcess::ProcessError
     ↓
_on_error()
     ├── 错误类型映射 → "子进程启动失败" / "子进程崩溃" 等
     └── errorString() → logger.error() + traceback
```

### 6.5 临时文件管理

- 指定获取（`fetch_specific` / `fetch_pair` / `fetch_single`）将武将数据写入临时 JSON 文件
- 临时文件路径存在 `self._context["tmp_path"]` 中
- `_on_finished()` 和 `_on_error()` 都会调用 `_cleanup_tmp()` 清理
- 清理失败（`OSError`）只打 warning 不阻断流程

---

## 七、JSON 提取与 ETL 细节

### 7.1 提取流程总览

```
AI 回复文本（浏览器 inner_text 或 API response）
  │
  │ Step 1: 预处理
  │ text.strip()
  ▼
  │ Step 2: 尝试所有解析路径
  │ ├── 全文 raw_decode
  │ ├── ```json 代码块提取
  │ ├── --- 分隔线 rfind 最后一段
  │ └── { 到 } 区间截取
  │
  ▼
  │ Step 3: 字符修复
  │ _repair_strings(s)
  │ ├── 仅在字符串值内 (in_string=True)
  │ ├── 字面 \r\n → \\n
  │ └── 已转义序列 \\ → 原样保留
  │
  ▼
  │ Step 4: JSONDecoder.raw_decode 宽容解析
  │ （容忍尾部多余字符、注释等）
  │
  ▼
  Python dict
```

### 7.2 两种生成器共用统一方法

AI 回复格式（hero_guide.md 要求）：
```
## 第一部分：攻略正文
<完整分析内容>

---
## 第二部分：结构化数据

```json
{
  "hero_id": 18,
  "key_points": [...],
  ...
}
```
```

`_extract_json` 在 `ai_generator.py` 和 `ai_playwright.py` 中保持相同逻辑，差异仅在：
- `ai_generator.py` 输入为 API 返回的 JSON 中的 `content` 字段
- `ai_playwright.py` 输入为浏览器 `inner_text()` 原始文本（含字面换行符等）

### 7.3 `_repair_strings` 状态机细节

```
输入: {"description": "Line1\nLine2\nEnd", "key": "val"}
                                                    in_string?
      {                                             False
      "                                             True  ← 进入字符串
      d e s c r i p t i o n                         True
      "                                             False ← 出字符串
      :                                             False
      "                                             True  ← 进入字符串
      L i n e 1                                     True
      \n          → 转为 \\n                        True  ← 修复换行
      L i n e 2                                     True
      \n          → 转为 \\n                        True
      E n d                                         True
      "                                             False ← 出字符串
      ,                                             False
      "                                             True
      k e y                                         True
      ...
输出: {"description": "Line1\\nLine2\\nEnd", "key": "val"}
```

---

## 八、配置加载与 env 解析细节

### 8.1 env.py 函数一览

| 函数 | 说明 |
|------|------|
| `parse_env_file(path)` | 解析 .env → `dict[str, str]` |
| `load_env_config(path)` | 解析后映射为小写 key → `dict` |
| `get_api_config()` | 合并 config.env + 环境变量 + 默认值 |
| `get_runtime_params()` | 获取运行时参数 |
| `save_env_file(path, data)` | 原子写入 .env 文件 |

### 8.2 Key 映射表

```python
key_mapping = {
    "DEEPSEEK_API_KEY": "api_key",
    "DEEPSEEK_API_URL": "api_url",
    "DEEPSEEK_MODEL": "model",
    "REQUESTS_PER_MINUTE": "requests_per_minute",
    "HTTP_TIMEOUT": "http_timeout",
    "MAX_RETRIES": "max_retries",
}
```

数值类型（`requests_per_minute` / `max_retries` / `http_timeout`）尝试 `int()` 转型，失败时使用默认值并打 warning。

### 8.3 优先级链

```python
api_key = config.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
```

config.env → 环境变量 `DEEPSEEK_API_KEY` → 环境变量 `OPENAI_API_KEY` → `""`（后续由 `_check_api_key` 拦截）

### 8.4 原子保存

```python
# save_env_file
tmp_path = env_path.with_suffix(".env.tmp")
tmp_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
tmp_path.replace(env_path)
```

---

## 九、浏览器自动化细节

### 9.1 PlaywrightGenerator 类（662 行）

#### 9.1.1 生命周期

```
__init__ →（惰性）→ _ensure_browser → 生成 → close
                            │
                     sync_playwright.start()
                     chromium.launch_persistent_context(
                         channel="msedge",
                         user_data_dir="...",
                         headless=False,
                         slow_mo=50,
                         args=["--disable-blink-features=AutomationControlled"]
                     )
                     page.goto("https://chat.deepseek.com/")
                     _wait_for_login()  → 等待 textarea 出现
```

#### 9.1.2 会话复用

`_guide_system_sent` 和 `_synergy_system_sent` 控制首次发送发完整 `system_prompt + 数据`，后续只发送 `数据`（带武将 ID），让 AI 在同一会话中按已设定的规则持续生成。

#### 9.1.3 流式回复等待（`_send_and_wait`, 行 357-482）

**Phase 1 — 检测回复开始**：
- 记录发送前 `assistant_selector` 匹配的元素数量
- 每 500ms 轮询，直到数量增加
- 超时（`response_timeout`，默认 180s）则触发 `_page_diagnostics()`

**Phase 2 — 等待内容稳定**：
- 每 2 秒取最后一条 assistant 消息的 `inner_text()` 长度
- 长度连续 3 轮（约 6 秒）不变 → 认为生成完毕
- 额外等待 1 秒后返回

#### 9.1.4 页面诊断（`_page_diagnostics`）

当选择器匹配失败时，通过 `page.evaluate()` 执行 JS 采集：
- 所有 `textarea` 的 id/name/placeholder/class
- 所有 `[contenteditable]` 元素
- 所有可见 `button` 的 text/aria-label 等
- 所有 class 或 data 属性含 `message|chat|reply|ai` 的 div
- `document.body.textContent` 前 300 字符

### 9.2 默认配置

```python
DEFAULT_BROWSER_CONFIG = {
    "channel": "msedge",
    "user_data_dir": "C:/Users/.../Edge/User Data",  # 自动从 home 推导
    "headless": False,       # 不可 headless（触发反爬）
    "slow_mo": 50,           # 50ms 操作延迟
    "args": ["--disable-blink-features=AutomationControlled"],
}

DEFAULT_CHAT_CONFIG = {
    "url": "https://chat.deepseek.com/",
    "input_selector": "textarea[placeholder*='DeepSeek']",
    "assistant_selector": "div.ds-assistant-message-main-content",
    "content_class": "",
    "login_timeout": 15000,
    "response_timeout": 180000,
}
```

---

## 十、日志系统细节

### 10.1 日志配置中心 `src/config/logging_config.py`

统一管理全项目日志格式、输出目标和日志轮转策略。

### 10.2 日志文件结构

桌面应用启动时自动在 `logs/` 目录下创建以下文件：

```
logs/
├── app.log                  # 桌面应用运行时日志（UI + 数据加载）
├── scraper/
│   ├── scraper.log          # 爬虫模块日志（official / incremental）
│   └── ai_batch.log         # AI 批量生成日志（_extract_json 等 ETL 步骤）
├── business/
│   └── business.log         # QProcess 业务服务日志（启停、信号）
└── subprocess/
    ├── stdout.log           # 子进程标准输出
    └── stderr.log           # 子进程错误输出（排查崩溃的关键）
```

### 10.3 配置项

`config.env` 中可配置：

```env
LOG_LEVEL=INFO              # DEBUG / INFO / WARNING / ERROR
LOG_TO_FILE=true             # true 启用文件日志，false 仅控制台
```

### 10.4 日志轮转

- 单个日志文件最大 10MB
- 保留 5 个备份（`app.log.1` ~ `app.log.5`）
- 超过上限自动轮转

### 10.5 模块过滤

日志按 logger name 前缀自动路由到对应文件：

| logger name 前缀 | 目标文件 |
|-----------------|----------|
| `src.scraper` | `scraper/scraper.log` |
| `src.scraper.ai_` | `scraper/ai_batch.log` |
| `src.business` | `business/business.log` |
| `subprocess.stdout` | `subprocess/stdout.log` |
| `subprocess.stderr` | `subprocess/stderr.log` |
| 其他 | `app.log` |

### 10.6 子进程日志

业务服务（`GuideFetchService` / `SynergyFetchService`）在 `_start_process` 时启用 `SeparateChannels` 模式：

- stdout → `logging.getLogger("subprocess.stdout")`
- stderr → `logging.getLogger("subprocess.stderr")`

分别写入独立文件，避免与父进程日志混淆。

---

## 十一、测试体系细节

### 10.1 测试文件与用例数

| 文件 | 类 | 用例数 | 测试内容 |
|------|-----|--------|----------|
| test_models.py | TestSkill / TestHero / TestSynergyScore / TestHeroGuide / TestCard / TestIncrementalUpdate | 25 | Pydantic 模型校验 |
| test_ai_batch.py | TestLoadPrompt / TestEstimateCost / TestInternalEstimateCost / TestSaveJson / TestAIBatchGenerator / TestLoadHeroes / TestConfigLoading | 33 | AI 批量生成核心逻辑 |
| test_hero_manager.py | TestHeroManager | 13 | 武将 CRUD + 查询 |
| test_synergy_manager.py | TestSynergyManager | 13 | 相性 CRUD + 双向查询 |
| test_guide_manager.py | TestGuideManager | 11 | 攻略 CRUD |
| test_incremental_update.py | TestApplyIncrementalUpdate | 8 | 增量更新逻辑 |
| test_ui.py | TestEnvFileParsing | 4 | UI 工具函数 |

**总计：112 个测试用例。**

### 10.2 AIBatchGenerator 测试要点

| 测试 | 验证内容 |
|------|---------|
| `test_extract_json_direct` | `_extract_json` 直接解析合法 JSON |
| `test_extract_json_from_code_block` | 从 ```json 代码块提取 |
| `test_extract_json_from_separator` | 从 --- 分隔线后提取 |
| `test_validate_guide_success` | HeroGuide 完整数据校验通过 |
| `test_validate_guide_failure` | 缺少必填字段返回 None |
| `test_validate_synergy_success` | SynergyScore 完整数据校验通过 |
| `test_validate_synergy_failure` | score 超出范围返回 None |
| `test_combat_synergy_compatibility` | 旧字段兼容转换后通过 Pydantic |

### 10.3 测试约定

- 纯 pytest（不继承 `unittest.TestCase`）
- 文件 IO 使用 `tempfile` 避免影响真实数据
- Manager 测试使用 `_make_*` 辅助方法构造测试数据
- `sys.path.insert(0, "..")` 在测试文件内手动添加
