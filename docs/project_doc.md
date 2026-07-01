# 名将杀 Agent — 项目细节文档

> 版本：v0.1.0  
> 项目路径：`G:\py_savepoint\test_project`  
> 远程仓库：`gitee.com:chen-xianghao920/test_project.git`  
> 文档日期：2026-06-28

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
- [十一、屏幕采集模块细节](#十一屏幕采集模块细节)
- [十二、OCR 识别模块细节](#十二ocr-识别模块细节)
- [十三、测试体系细节](#十三测试体系细节)
- [十四、数据全流程详解](#十四数据全流程详解)

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

#### 2.2.1 命令行参数

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
| `--update` | bool | False | 更新模式（重新生成已有数据） |
| `--verbose` | bool | False | 详细日志 |

#### 2.2.2 生成器选择逻辑

```python
if args.browser:
    from src.scraper.ai_playwright import PlaywrightGenerator
    generator = PlaywrightGenerator()
else:
    _check_api_key(api_config)
    generator = AIBatchGenerator(...)
```

#### 2.2.3 断点续传 / 更新模式

**攻略**：
- `--update`（增量/指定获取）：更新模式，生成前删除旧数据，**不跳过已有**
- 无 `--update`（全量获取）：断点续传，跳过已存在的 `hero_id`

**相性**：
- `--synergy`（全量生成）：始终是更新模式，启动时清空所有旧数据重新生成
- `--synergy-single`（选定武将）：断点续传，已有的相性对跳过不重复生成
- `--synergy-pair`（指定配对）：更新模式，先删除旧数据再写入新的

#### 2.2.4 浏览器模式的 token 处理

浏览器模式返回 `(result, None)`，不以 token 统计判断成败，避免误报失败。

### 2.3 AIBatchGenerator 详细说明（ai_generator.py）

#### 2.3.1 构造函数

```python
def __init__(self, api_key, api_url, model, requests_per_minute, max_retries, http_timeout)
```

- `api_key` 为空时抛 `ValueError`
- `_client = httpx.Client(timeout=http_timeout)` — 同步 HTTP 客户端
- `_min_interval = 60.0 / rpm` — 速率控制（秒/请求）
- `_last_request_time = 0.0` — 上次请求时间戳

#### 2.3.2 API 调用

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

#### 2.3.3 `generate_guide(hero) → (dict | None, dict | None)`

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

#### 2.3.4 `generate_synergy(hero_a, hero_b) → (dict | None, dict | None)`

同 `generate_guide` 但：
- 使用 `synergy_score.md` prompt 模板
- 注入 `hero_a_id` + `hero_b_id`
- 兼容旧字段：`combat_synergy` → `combo_ceiling`
- 使用 `_validate_synergy` 校验

### 2.4 ai_guide.py — 攻略生成循环

```python
def run_guide_generation(heroes, generator, guide_path, existing_guides, api_config, update_mode=False)
```

流程：
1. 遍历所有武将
2. `update_mode=False` 时跳过已存在（断点续传）；`update_mode=True` 时先删除旧数据
3. 输出 `"[i/N] hero_name OK"`（被进度条正则匹配）
4. `generator.generate_guide(hero)` → `(result, usage)`
5. 累计 usage
6. 每 10 条（`GUIDE_BATCH_SAVE_INTERVAL`）批量保存
7. 结束后最终保存

### 2.5 ai_synergy.py — 全量相性生成

始终是更新模式，启动时清空旧数据，遍历所有 `N*(N-1)/2` 对组合重新生成。
每 20 条（`SYNERGY_BATCH_SAVE_INTERVAL`）批量保存。

### 2.6 ai_synergy_pair.py — 指定配对

- 读入 2 个武将的 JSON 文件
- 校验恰好 2 个
- 先生成 → 成功后再删除旧数据（避免失败数据丢失）
- 立即保存

### 2.7 ai_synergy_single.py — 选定武将 x 全体

支持断点续传：已有的相性对跳过不重复生成，新增完成后统一保存。

---

## 三、业务服务层细节

### 3.1 服务类一览

| 类 | 文件 | 行数 | 父类 | 信号数量 |
|--------|------|------|------|----------|
| HeroFetchService | `fetch_service.py` | ~102 | QObject | 3 |
| GuideFetchService | `guide_fetch_service.py` | ~179 | QObject | 6 |
| SynergyFetchService | `synergy_fetch_service.py` | ~104 | QObject | 3 |
| CaptureService | `capture_service.py` | ~190 | QObject | 3 |
| OcrService | `ocr_service.py` | ~127 | QObject | 3 |

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

每个方法新增 `backend` 参数（`"api"` 或 `"browser"`），UI 层通过 `BackendChooseDialog` 完成确认后调用 `execute_with_confirmation()`。

#### `execute_with_confirmation()` 逻辑

1. 读取 `self._context` 中的 `mode`、`heroes`、`backend`
2. 构建 `base_args = ["-m", "src.scraper.ai_batch", "--guide"]`
3. `backend == "browser"` → 追加 `--browser`
4. `mode` 为 `"incremental"` / `"specific"` → 追加 `--update`（更新模式）
5. `mode` 为 `"incremental"` / `"specific"` → 写入临时文件，追加 `--heroes-file`
6. 启动 QProcess

#### 子进程错误日志增强

- `SeparateChannels` 模式：分别读取 stdout 和 stderr
- `readyReadStandardError` → 实时输出到日志
- `_on_finished` 非零退出 → 输出完整 stdout + stderr
- `_on_error` 输出错误类型名

### 3.4 SynergyFetchService

同 GuideFetchService 模式，但 args 不同：
- `fetch_pair` → `--synergy-pair <tmp_file>`
- `fetch_single` → `--synergy-single <tmp_file>`
- 同样支持 `backend` 参数追加 `--browser`

### 3.5 CaptureService（截图业务服务）

```python
class CaptureService(QObject):
    status_changed = Signal(str)           # 状态消息
    capture_completed = Signal(dict)       # {image, save_path, ocr_results, ocr_matched}
    capture_failed = Signal(str)           # 错误消息
```

截图操作直接在 Python 中执行（不通过 QProcess），因为需要即时获取图像数据更新 UI。
通过 `QTimer.singleShot(0, ...)` 确保不阻塞 Qt 事件循环。

**主要方法**：

| 方法 | 说明 |
|------|------|
| `update_config(config)` | 更新配置并重建 AdbCapture（路径/端口变化时重建） |
| `do_capture(hero_names)` | 执行截图 → 可选 OCR（手动调用路径，会保存截图到 screenshots/） |
| `do_capture_from_file(file_path, hero_names)` | 从本地图片执行 OCR（不依赖 ADB） |
| `connect_emulator()` | 连接模拟器 |
| `disconnect_emulator()` | 断开模拟器 |

**手动截图全流程**：

```
do_capture()
  └─ QTimer.singleShot(0, _execute_capture)
       ├─ AdbCapture.screencap_full() → PIL Image
       ├─ 保存截图到 screenshots/ 目录（手动调用路径特有）
       ├─ 已启用 OCR？
       │   ├─ TemplateManager.match(image) → 是武将页？
       │   │   ├─ 否 → 跳过
       │   │   └─ 是 → GeneralRecognizer.recognize() → 保存 JSON
       │   └─ 返回结果
       └─ emit capture_completed({image, save_path, ocr_results, ocr_matched})
```

**注意**：轮询路径不走 `do_capture()`，轮询直接在 `_on_poll_capture()` 中调用 `screencap_full()` + `_run_ocr()`，**不保存截图文件到磁盘**，全程内存中处理。详见第十二章 12.5 节。`_run_ocr()` 只做模板匹配 → OCR → 返回结果，不含截图和保存逻辑。

### 3.6 OcrService（OCR 控制服务）

```python
class OcrService(QObject):
    status_changed = Signal(str)           # 状态消息
    template_changed = Signal(bool)        # 模板加载/已删除
    ocr_completed = Signal(list)           # 识别结果
    poll_tick = Signal()                   # 轮询触发信号（由 QTimer 驱动，连接至 MainWindow._on_poll_capture）
```

**主要方法**：

| 方法 | 说明 |
|------|------|
| `update_config(config)` | 更新配置缓存 |
| `set_hero_names(names)` | 设置武将名列表（编辑距离矫正用） |
| `create_template(image, roi)` | 制作模板 |
| `select_template(file_path)` | 从文件加载模板 |
| `is_template_loaded()` | 检查模板是否已加载 |
| `delete_template()` | 删除模板 |
| `start_poll(interval_ms)` | 启动轮询 QTimer |
| `stop_poll()` | 停止轮询并清除冷却 |
| `set_cooldown(seconds)` | 设置冷却时间（OCR 匹配成功后调用） |
| `run_ocr(image, rois)` | 对单张图片执行 OCR |

**异常处理**：所有 except 块记录 `logger.error` + `logger.debug(traceback.format_exc())`，不允许静默异常。

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
| main_window.py | 636 | QMainWindow（顶层） |
| hero_browser.py | 432 | QWidget（Tab 内嵌） |
| recommendation_panel.py | 528 | QWidget（Tab 内嵌） |
| mumu_config_dialog.py | 574 | QDialog（模拟器配置） |
| hero_select_dialog.py | 293 | QDialog（基类） |
| settings_dialog.py | 268 | QDialog（API 配置） |
| roi_selector.py | 149 | QDialog（框选模板区域） |
| backend_choose_dialog.py | 141 | QDialog |
| guide_progress_dialog.py | 135 | QDialog |
| cost_confirm_dialog.py | 77 | QDialog |
| style.py | 247 | 样式表常量 |
| fetch_dialog.py | 31 | QDialog（继承基类） |
| guide_fetch_dialog.py | 29 | QDialog（继承基类） |
| synergy_pair_dialog.py | 30 | QDialog（继承基类） |
| synergy_single_dialog.py | 30 | QDialog（继承基类） |

### 5.2 主窗口信号拓扑

```
MainWindow.__init__
 ├── HeroFetchService ─── 武将采集
 │   ├── status_changed → _on_fetch_status (状态栏)
 │   ├── fetch_completed → _on_fetch_completed (弹窗提示)
 │   └── error_occurred → _on_fetch_error (弹窗警告)
 ├── GuideFetchService ─── 攻略生成
 │   ├── status_changed → _on_fetch_status
 │   ├── cost_estimated → _on_guide_cost_estimated (CostConfirmDialog)
 │   ├── fetch_completed → _on_guide_fetch_completed (进度条/重载)
 │   ├── error_occurred → _on_guide_fetch_error (带详情弹窗)
 │   ├── progress_output → _on_guide_progress (进度文字)
 │   └── progress_value → _on_guide_progress_value (进度条数值)
 ├── SynergyFetchService ─── 相性获取
 │   ├── status_changed → _on_fetch_status
 │   ├── fetch_completed → _on_synergy_fetch_completed (弹窗+重载)
 │   └── error_occurred → _on_synergy_fetch_error (弹窗警告)
 ├── CaptureService ─── 截图
 │   ├── status_changed → _status_label.setText
 │   ├── capture_completed → _on_capture_completed / _on_capture_result (通知推荐面板)
 │   └── capture_failed → QMessageBox.warning
 └── OcrService ─── OCR + 持续轮询
     ├── status_changed → _status_label.setText
     ├── template_changed → 更新 UI 状态
     └── poll_tick → _on_poll_capture (轮询编排：截图→模板匹配→OCR→结果填入推荐面板)
```

### 5.3 模拟器配置对话框（MumuConfigDialog）

位于 配置 → 模拟器配置，与 SettingsDialog 同级菜单入口。

**功能分区**：

```
┌──────────────────────────────────────────────┐
│ ADB 连接管理                                   │
│ [ADB 路径显示]  [自动探测] [浏览...]           │
│ 设备: [下拉选择 MuMu 实例 ▼] [连接/断开] [刷新]│
│ 状态: 未连接 / 连接中 / 已连接 (127.0.0.1:...)│
│ ADB 端口: 16448                               │
├──────────────────────────────────────────────┤
│ 识别模板                                       │
│ ● 已加载: wujiang_select.png                  │
│ [🎯 制作模板] [📁 选择模板]                    │
├──────────────────────────────────────────────┤
│ 武将识别设置                                   │
│ [☐] 启用武将识别                              │
│ [☐] 持续轮询（独立运行）                       │
│ 轮询: [2 秒 ▼] 检测间隔                       │
│ 匹配阈值: [0.8 ▼]                             │
├──────────────────────────────────────────────┤
│ [保存] [取消]                                  │
└──────────────────────────────────────────────┘
```

**连接管理**：
- **自动探测**：通过注册表、环境变量 `MUMU_HOME`、常见安装路径查找 `adb.exe`
- **多设备切换**：`QComboBox` 下拉列出所有 MuMu 实例（● 运行中 / ○ 未运行）
- **一键连接/断开**：单按钮切换，通过 `QTimer.singleShot` 异步执行不阻塞 UI
- **状态监控**：灰色「未连接」→ 橙色「连接中...」→ 绿色「已连接」→ 红色「连接失败」

**模板管理**：
- **制作模板**：连接模拟器后截图 → `RoiSelectorDialog` 框选 ROI → 保存到 `templates/wujiang_select.png`
- **选择模板**：从文件选择已有模板图片，复制到 `templates/` 目录后加载

**OCR 配置**：
- **启用武将识别**：截图后自动 OCR
- **持续轮询**：独立于手动截图，定时检测模拟器画面（详见第十二章 12.6 节）
- **匹配阈值**：OpenCV 模板匹配的灵敏度（0~1，默认 0.8）
- 轮询间隔配置（1-60 秒）

### 5.4 区域框选对话框（RoiSelectorDialog）

在预览图上拖拽鼠标选择矩形区域，返回 `(x, y, w, h)` 坐标给调用方。

**交互流程**：
```
鼠标按下 → 记录 drag_start
鼠标移动 → 实时更新选框 + 坐标信息
鼠标释放 → 完成拖拽
确认 → 按 pixmap/label 缩放比例计算实际 ROI
取消 → 返回 None
```

**坐标缩放**：QLabel 显示缩放后的预览图，ROI 坐标按 `scale_x = pm_size.width() / label_size.width()` 映射回原图尺寸。

### 5.5 后端选择对话框（BackendChooseDialog）

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

### 5.6 攻略生成进度条（GuideProgressDialog）

**UI 组成**：
- 状态文字（"已生成 XXX 的攻略..."）
- 进度条（`current / total`）
- 详情标签（灰色，12px）
- 错误标签（红色，隐藏）
- 关闭按钮（执行中禁用，完成时启用）

**进度更新正则**：
```python
m = re.search(r"\[(\d+)/(\d+)\]\s*(.+?)\s+(?:OK|FAIL)", text)
```
匹配格式 `"[1/3] 诸葛亮 OK"`，仅在成功/失败后更新进度条，不提前跳进度。

### 5.7 武将浏览器（HeroBrowser）

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

### 5.8 选将推荐面板（RecommendationPanel）

```
RecommendationPanel (QWidget)
 ├── 标题行
 │   ├── "选将推荐"
 │   ├── [截图] 按钮（ADB 截图 → OCR 导入）
 │   └── [📁 从图片导入] 按钮（本地图片 → OCR 导入）
 └── QGridLayout (4行 × 2列)
      └── HeroCardWidget × 8
           ├── 头像区 (宽 130px)
           │   ├── QPixmap (从 images/name.png 加载)
           │   ├── QGridLayout 叠加
           │   │   ├── 名称浮层 (底部, rgba(0,0,0,140))
           │   │   └── 势力标签 (左上角, 色块)
           └── 信息区 (弹性)
               ├── 势力色块 + 武将名 (粗体 15px)
               ├── 推荐指数 (★★★★☆ 98.23% 或 ★★☆☆☆ --)
               ├── 分隔线
               ├── 高相性组合标题
               ├── QGridLayout (2列, 搭配+评分)
               ├── 分隔线
               └── 胜率（从 2v2胜率排行.csv 加载，前三自动标记 🥇🥈🥉 奖牌）
```

**势力色表**（`FACTION_COLORS`）：
```python
FACTION_COLORS = {
    "秦": "#8B4513", "汉": "#B22222", "楚": "#2F4F4F",
    "赵": "#556B2F", "魏": "#800020", "燕": "#6A0DAD",
    "齐": "#1B7A3D", "韩": "#CD853F",
    "孙吴": "#4169E1", "蜀": "#228B22", "曹魏": "#800020",
    "群雄": "#8B0000", "晋": "#4A6741", "新朝": "#B8860B",
}
```

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

**截图导入流程（`截图` 按钮）**：
1. 检查 ADB 是否已配置 → 未配置则弹出 MumuConfigDialog
2. 点击后按钮变为「正在截图...」
3. CaptureService.do_capture() → 截图 → OCR → capture_completed 信号
4. `_on_capture_result` 回调 → 调用 `load_from_ocr()` → 填入 8 个槽位

**`load_from_ocr(ocr_results)`**：
- 接收 OCR 识别结果 `[{index, name, confidence}, ...]`
- 将 name 匹配 HeroManager 中的 Hero 对象
- 加载 `images/<name>.png` 头像
- 推荐指数固定为 0.5（两星，表示来自截图识别，不直接使用 OCR 置信度）
- 根据武将名从 `synergies.json` 加载高相性组合数据
- 根据武将名从 `2v2胜率排行.csv` 加载胜率，随即对 8 个槽位按胜率降序排名，前三自动标记 🥇🥈🥉 奖牌
- 未匹配到 HeroManager 的武将名仍显示名称文字供人工判断

### 5.9 对话框基类体系

```
BaseHeroSelectDialog (hero_select_dialog.py, ~293行)
 ├── SelectionMode 枚举: MULTI / MULTI_LIMIT / SINGLE
 ├── ReturnFormat 枚举: IDS / HEROES_DICT
 ├── 搜索框 + 势力网格 + 复选框列表 + 已选计数 + 确认/取消
 │
 ├── HeroFetchDialog (fetch_dialog.py, ~31行)
 │   SelectionMode=MULTI, ReturnFormat=IDS
 │
 ├── GuideFetchDialog (guide_fetch_dialog.py, ~29行)
 │   SelectionMode=MULTI, ReturnFormat=HEROES_DICT
 │
 ├── SynergyPairDialog (synergy_pair_dialog.py, ~30行)
 │   SelectionMode=MULTI_LIMIT, max_selection=2
 │
 └── SynergySingleDialog (synergy_single_dialog.py, ~30行)
     SelectionMode=SINGLE
```

### 5.10 全局样式（style.py, 247 行）

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

三个业务服务全部使用 `SeparateChannels` 模式：

```python
self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
```

### 6.3 子进程编码修复

所有 CLI 脚本入口的 Windows 编码修复：

```python
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
```

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

### 7.2 `_repair_strings` 状态机细节

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
| `get_mumu_config()` | 获取模拟器（MuMu）ADB/OCR 配置 |
| `save_env_file(path, data)` | 原子写入 .env 文件 |

### 8.2 Key 映射表

```python
key_mapping = {
    # API
    "DEEPSEEK_API_KEY": "api_key",
    "DEEPSEEK_API_URL": "api_url",
    "DEEPSEEK_MODEL": "model",
    "REQUESTS_PER_MINUTE": "requests_per_minute",
    "HTTP_TIMEOUT": "http_timeout",
    "MAX_RETRIES": "max_retries",
    # 日志
    "LOG_LEVEL": "log_level",
    "LOG_TO_FILE": "log_to_file",
    # 模拟器 (MuMu)
    "MUMU_ADB_PATH": "mumu_adb_path",
    "MUMU_ADB_PORT": "mumu_adb_port",
    "MUMU_OCR_ENABLED": "mumu_ocr_enabled",
    "MUMU_OCR_POLL_MODE": "mumu_ocr_poll_mode",
    "MUMU_OCR_POLL_INTERVAL": "mumu_ocr_poll_interval",
    "MUMU_OCR_MATCH_THRESHOLD": "mumu_ocr_match_threshold",
}
```

数值类型配置项自动转型（`int` / `float` / `bool`），失败时使用默认值并打 warning。

### 8.3 优先级链

```python
api_key = config.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
```

config.env → 环境变量 → `""`（后续由 `_check_api_key` 拦截）

### 8.4 config.env 配置示例

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
LOG_LEVEL=INFO
LOG_TO_FILE=true
MUMU_ADB_PATH=D:\模拟器\MuMu Player 12\nx_main\adb.exe
MUMU_ADB_PORT=16448
MUMU_OCR_ENABLED=true
MUMU_OCR_POLL_MODE=false
MUMU_OCR_POLL_INTERVAL=2
MUMU_OCR_MATCH_THRESHOLD=0.8
```

### 8.5 原子保存

```python
tmp_path = env_path.with_suffix(".env.tmp")
tmp_path.write_text("...", encoding="utf-8")
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

`_guide_system_sent` 和 `_synergy_system_sent` 控制首次发送完整 `system_prompt + 数据`，后续只发送 `数据`（带武将 ID），让 AI 在同一会话中按已设定的规则持续生成。

**随机休息**：后续调用（非首次）每次生成完成后随机休息 60-180 秒，避免触发风控。

#### 9.1.3 流式回复等待（`_send_and_wait`）

**Phase 1 — 检测回复开始**：
- 记录发送前 `assistant_selector` 匹配的元素数量
- 每 500ms 轮询，直到数量增加
- 超时（默认 180s）则触发 `_page_diagnostics()`

**Phase 2 — 等待内容稳定**：
- 每 2 秒取最后一条 assistant 消息的 `inner_text()` 长度
- 长度连续 3 轮（约 6 秒）不变 → 生成完毕 + 额外等待 1 秒

#### 9.1.4 默认配置

```python
DEFAULT_BROWSER_CONFIG = {
    "channel": "msedge",
    "user_data_dir": "...Edge/User Data",  # 自动推导
    "headless": False,
    "slow_mo": 50,
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

```
logs/
├── app.log                  # 桌面应用运行时日志（UI + 数据加载）
├── scraper/
│   ├── scraper.log          # 爬虫模块日志（official / incremental / capture / ocr）
│   └── ai_batch.log         # AI 批量生成日志（_extract_json 等 ETL 步骤）
├── business/
│   └── business.log         # QProcess 业务服务日志（guide/synergy/capture/ocr 服务）
└── subprocess/
    ├── stdout.log           # 子进程标准输出
    └── stderr.log           # 子进程错误输出（排查崩溃的关键）
```

### 10.3 模块过滤表

| logger name 前缀 | 目标文件 |
|-----------------|----------|
| `src.scraper` | `scraper/scraper.log` |
| `src.scraper.ai_` | `scraper/ai_batch.log` |
| `src.business` | `business/business.log` |
| `src.capture` | `scraper/scraper.log` |
| `src.ocr` | `scraper/scraper.log` |
| `subprocess.stdout` | `subprocess/stdout.log` |
| `subprocess.stderr` | `subprocess/stderr.log` |
| 其他（含 `src.ui.*`） | `app.log` |

新增模块的日志路由：
- `src.capture.*` → `scraper/scraper.log`
- `src.ocr.*` → `scraper/scraper.log`
- `src.business.capture_service` → `business/business.log`
- `src.business.ocr_service` → `business/business.log`
- `src.ui.mumu_config_dialog` → `app.log`
- `src.ui.roi_selector` → `app.log`

### 10.4 日志轮转

- 单个日志文件最大 10MB
- 保留 5 个备份（`app.log.1` ~ `app.log.5`）
- 超过上限自动轮转

### 10.5 异常处理规范

所有 except 块遵循以下规范：
- 使用 `logger.error("描述: %s", e)` 记录错误
- 使用 `logger.debug(traceback.format_exc())` 在 DEBUG 级别输出堆栈
- 不允许 `except: pass` 或空 except 块

---

## 十一、屏幕采集模块细节

### 11.1 模块文件结构

```
src/capture/
 ├── __init__.py              # 空 init
 ├── adb_screen.py           # AdbCapture — ADB 连接与截图（265 行）
 ├── prober.py               # MuMu 设备探测（函数式，~180 行）
 └── image_utils.py          # 图像工具函数（~70 行）
```

### 11.2 ADB 设备探测（prober.py）

函数式设计，无内部状态。完全参考 mumu_screen 原项目实现。

#### 核心函数

| 函数 | 返回 | 说明 |
|------|------|------|
| `probe_mumu_adb()` | `str` | 查找 adb.exe（PATH → 注册表 → 安装路径） |
| `probe_mumu_port()` | `int` | 通过 MuMuManager 获取运行中实例的 ADB 端口 |
| `probe_all_devices()` | `list[MuMuDeviceInfo]` | 列出所有 MuMu 实例信息 |
| `test_adb_path(path)` | `(bool, str)` | 验证 ADB 可执行文件是否有效 |

#### 数据类

```python
@dataclass
class MuMuDeviceInfo:
    index: str          # MuMuManager 中的索引
    name: str           # 实例名称
    adb_port: int       # ADB 端口
    is_running: bool    # 是否正在运行
    is_main: bool       # 是否为主实例
```

#### 路径探测优先级

`probe_mumu_adb()` 查找顺序：
1. **系统 PATH** — `shutil.which("adb")`
2. **MuMu 安装目录** — 注册表 `HKLM\SOFTWARE\Netease\MuMuPlayer12` 或 `MUMU_HOME` 环境变量
3. **常见安装路径** — `D:/模拟器/MuMu Player 12` 等，在 `nx_main/adb.exe` 和 `emulator/nemu/EmulatorShell/adb.exe` 中查找

#### 实例探测

`probe_all_devices()` 流程：
1. 定位 MuMu 安装根目录（含 `nx_main` 目录）
2. 调用 `MuMuManager.exe info --vmindex all`
3. 解析 JSON 返回（格式：`{index_str: {name, adb_port, is_android_started, is_main}}`）

### 11.3 ADB 连接与截图（adb_screen.py）

```python
class AdbCapture:
    def __init__(self, adb_path: str, adb_port: int = 7555)
```

**连接管理**：

| 方法 | 返回 | 说明 |
|------|------|------|
| `connect()` | `(bool, str)` | ADB connect + 设备检测 |
| `disconnect()` | `(bool, str)` | 断开 |
| `reconnect()` | `(bool, str)` | 强制重连 |
| `check_device()` | `(bool, str)` | 设备在线检查 |
| `screencap_full()` | `(bool, Image|str)` | ADB exec-out screencap 全屏截图 |

**属性**：
- `device_serial`：可读写，切换目标设备（如 `127.0.0.1:16448`）
- `connected`：只读，连接状态

**安全设计**：
- 命令注入防护：`_run_adb(*args)` 使用列表参数
- 设备序列号格式校验：`_check_device_serial_safe()` 校验 IP:端口 格式
- 超时保护：所有 `subprocess.run` 设置 `timeout`

### 11.4 图像工具（image_utils.py）

| 函数 | 说明 |
|------|------|
| `pil_to_qpixmap(image)` | PIL Image → QPixmap |
| `copy_image_to_clipboard(image)` | 复制图像到系统剪贴板 |
| `save_image(image, path)` | 保存为 PNG，返回 `(bool, str)` |

### 11.5 截图业务服务（capture_service.py）

见[第三章第 3.5 节](#35-captureservice截图业务服务)。

### 11.6 OCR 控制服务（ocr_service.py）

见[第三章第 3.6 节](#36-ocrserviceocr-控制服务)。

---

## 十二、OCR 识别模块细节

### 12.1 模块文件结构

```
src/ocr/
 ├── __init__.py              # 包 init
 ├── template_manager.py     # TemplateManager — OpenCV 模板匹配（~180 行）
 ├── recognizer.py           # GeneralRecognizer — PaddleOCR + 编辑距离矫正（~280 行）
 └── ocr_loader.py           # 单例延迟加载（~47 行）
```

### 12.2 模板管理器（template_manager.py）

负责武将选择页面的模板截图的保存、加载、OpenCV 模板匹配。

```python
class TemplateManager:
    def __init__(self, template_path=None)  # 默认 templates/wujiang_select.png
    # 属性
    template_path → Path
    is_loaded → bool

    # 加载
    reload()                                # 从磁盘重新加载
    set_template(image, roi)                # 从全图截取 ROI 保存为模板
    match(image, threshold=0.8) → (bool, float)  # 模板匹配
    delete_template()                       # 删除模板文件
```

**模板匹配流程**：
```
match(image, threshold=0.8)
  ├── 模板未加载 → (False, 0.0)
  ├── 输入转灰度（PIL → BGR → Gray）
  ├── 截图分辨率 < 模板分辨率 → (False, 0.0)
  └── cv2.matchTemplate(gray, template, TM_CCOEFF_NORMED)
       └── cv2.minMaxLoc() → max_val ≥ threshold → (True, confidence)
```

**匹配算法**：`cv2.TM_CCOEFF_NORMED`（归一化相关系数匹配），输出 0~1 的置信度。

**模板制作流程**：
```
用户框选 ROI (x, y, w, h)
  ├── 验证 ROI 尺寸（w≥10 且 h≥10）
  ├── 验证 ROI 不超出画面边界
  └── cv2.imwrite(template_path, roi_crop) → templates/wujiang_select.png
```

### 12.3 武将名称识别器（recognizer.py）

使用 PaddleOCR 对 8 个武将名称区域进行 OCR 识别。

#### 两段式识别策略

```
第一段：PaddleOCR 全量字典（ch）识别
  ROI 裁剪 → 放大 3× → CLAHE → 锐化 → 灰度
  → PaddleOCR → 文字 + 置信度

第二段：武将名库编辑距离矫正
  ⓐ 极高置信度（≥99.5%）且 OCR 结果不在武将库 → 信任 OCR，保护新增武将
  ⓑ 否则 → 用 155 武将名称列表做编辑距离匹配（阈值 ≤ 1）
     唯一候选 → 直接采纳
     多候选 → 多维汉字特征评分决胜（详见下文）
```

#### 多维汉字特征评分算法（2026-06-30 新增）

当编辑距离筛选出多个候选时，通过逐字符比较 + 加权评分决胜。

**公式**（以 `"王剪" → ["王异", "王翦"]` 为例）：

```
score = 0
for tc, cc in zip(text, candidate):
    if tc == cc:
        score += 1.0                    # 相同字符满分
    else:
        score += _multi_dim_similarity  # 加权多维评分
                                      # 四角×0.4 + 仓颉×0.4 + 部首×0.2
score -= 0.5 * length_diff * 2         # 长度惩罚
```

**数值对比**：

| 对比 | 四角(×0.4) | 仓颉(×0.4) | 部首(×0.2) | 字符分 | 总分 |
|------|-----------|-----------|-----------|-------|------|
| 王剪→王翦 | 0.80×0.4=0.32 | 0.75×0.4=0.30 | 0×0.2=0.0 | +1.0(王) | **1.62** |
| 王剪→王异 | 0.20×0.4=0.08 | 0.29×0.4=0.12 | 0×0.2=0.0 | +1.0(王) | **1.19** |

→ 王翦胜出，差距 36%，不再有平局问题。

**平局兜底**：拼音相似度（同音 1/ 不同 0）→ 笔画数差（升序）。

#### 汉字特征数据来源

| 维度 | 来源 | 存储位置 | 加载方式 |
|------|------|---------|----------|
| 四角号码 | unihan-etl（UNIHAN `kFourCornerCode`） | `src/data/char_info_cache.json` | 启动时 ~10ms |
| 仓颉码 | unihan-etl（UNIHAN `kCangjie`） | 同上 | 同上 |
| 部首 | cnradical | 同上 | JSON 缓存 / 运行时补齐 |
| 拼音 | pypinyin | 同上 | JSON 缓存 / warmup 预加载 |
| 笔画数 | UNIHAN `kTotalStrokes`（从 `Unihan_IRGSources.txt` 懒加载） | `recognizer.py` 内联路径 | 运行时解析文本文件，首次约 355ms |

数据文件 `src/data/char_info_cache.json` 包含 223 个高频汉字（武将名 + 常见 OCR 误识字）。
缓存缺失的汉字在运行时由原始库动态补齐并写入进程内存。

#### 类结构

```python
class GeneralRecognizer:
    def __init__(self, rois=None, hero_names=None)
    recognize(image) → list[dict]           # 对 8 个 ROI 逐一识别
    _recognize_single(roi, slot) → (str, float)
    _preprocess_roi(roi) → np.ndarray       # 图像预处理
    _extract_text(ocr_result) → (str, float) # 解析 PaddleOCR 返回
    save_results(results, json_path, image_path)  # 静态方法
```

#### 关键常量

```python
_HIGH_CONFIDENCE = 0.995         # 极高置信度——跳过矫正，保护新武将
_EDIT_DISTANCE_THRESHOLD = 1      # 编辑距离最大允许差异
```

#### 图像预处理流程

```
ROI 裁剪 (40×100 原始区域)
  │
  ├── 1. 放大 3× (cv2.resize, INTER_CUBIC)
  │     原因：PaddleOCR 对过小的文字区域识别率低
  │
  ├── 2. CLAHE 自适应直方图均衡 (LAB 色彩空间)
  │     clipLimit=2.0, tileGridSize=(8,8)
  │     原因：增强局部对比度
  │
  ├── 3. 锐化 (3×3 核)
  │     原因：强化文字边缘
  │
  ├── 4. 灰度化 (BGR → GRAY)
  │     原因：PaddleOCR 接受灰度图
  │
  └── 送 PaddleOCR 识别
```

#### PaddleOCR 调用

```python
@property
def _engine(self):
    if self._ocr is None:
        self._ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
    return self._ocr
```

- `use_angle_cls=False`：不启用文字方向分类，节省推理时间
- `show_log=False`：不输出 PaddleOCR 的调试日志
- 首次调用加载模型（约 2-3 秒），后续识别约 0.5 秒/图

#### 编辑距离矫正详解

`_correct_with_hero_list("曹不", hero_names)` 流程：

1. 遍历所有武将名，计算编辑距离
2. 找到最优匹配（距离最小的候选）
3. 距离 ≤ `_EDIT_DISTANCE_THRESHOLD(1)` 时采纳
4. 多个候选 → `_pick_visually_similar()` 视觉相似度决胜

**评分算法**（以 `"王剪" → ["王异", "王翦"]` 为例）：

多维汉字特征评分通过逐字符比较，对相同字符加满分，不同字符用四角号码、仓颉码、部首加权评分替代：

```
四角(×0.4) + 仓颉(×0.4) + 部首(×0.2)

示例：
  剪 vs 翦: 四角0.80×0.4 + 仓颉0.75×0.4 + 部首0×0.2 = 0.620
  剪 vs 异: 四角0.20×0.4 + 仓颉0.29×0.4 + 部首0×0.2 = 0.194
```

### 12.4 单例加载器（ocr_loader.py）

集中管理两个全局单例的延迟加载：

- `get_template_manager()` → `TemplateManager` 单例
- `get_recognizer(rois, hero_names)` → `GeneralRecognizer` 单例

ROI 或 `hero_names` 变更时自动重建 `GeneralRecognizer` 实例，避免静默忽略新配置。

### 12.5 业务集成流程

#### 手动截图识别

```
用户点击选将推荐面板「截图」或「📁 从图片导入」
  │
  ├── ADB 未配置？→ 弹出 MumuConfigDialog 配置
  │
  ├── CaptureService.do_capture()
  │   ├── AdbCapture 连接（未连接时自动连接）
  │   ├── screencap_full() → PIL Image（内存中，不写磁盘）
  │   └── OCR enabled？
  │       ├── TemplateManager.match() → 武将选择页？
  │       │   ├── 否 → ocr_matched=False
  │       │   └── 是 → GeneralRecognizer.recognize() → 保存 JSON
  │       └── 返回结果
  │
  └── capture_completed 信号
       └── RecommendationPanel._on_capture_result()
            ├── ocr_matched=False → 跳过
            └── ocr_matched=True → load_from_ocr() → 填入 8 槽
                 ├── 匹配 Hero 对象（通过 HeroManager）
                 ├── 加载 images/<name>.png 头像
                 ├── 推荐指数固定 0.5（两星，区分于 AI 推荐）
                 └── 加载相性数据（synergies.json）+ 胜率（2v2胜率排行.csv）
                       └── 按胜率降序排名，前三自动标记 🥇🥈🥉 奖牌
```

#### 持续轮询识别

OcrService 提供 QTimer 驱动，MainWindow 编排的轮询流程：

```

用户勾选「持续轮询」→ 保存配置
  │
  └─ MainWindow._open_mumu_config() → start_poll(interval_ms)
       │
       ▼ 每隔 N 秒触发
  OcrService.poll_tick signal
       │
       ▼
  MainWindow._on_poll_capture()
       │
       ├── 冷却期内？→ return（匹配成功后 180 秒冷却）
       ├── ADB 未配置/未连接？→ return（不自杀，下次继续）
       │
       ├── ① screencap_full() → PIL Image（全在内存，不写磁盘）
       │
       ├── ② TemplateManager.match(image, threshold)
       │     ├── 模板未加载 → return
       │     └── 不匹配 → return（静默跳过，不是武将页）
       │
       ├── ③ CaptureService._run_ocr(image) → PaddleOCR
       │     ├── GeneralRecognizer.recognize() → 8 个武将名
       │     └── 保存 latest.json
       │
       ├── ④ RecommendationPanel.load_from_ocr()
       │     └── 填充 8 个推荐槽位（头像/相性/胜率）
       │
       └── ⑤ OcrService.set_cooldown(180)
             └── 3 分钟内不再截图 + 匹配 + OCR
```

**关键设计**：
- 轮询路径全程无磁盘 I/O：ADB 截图 → BytesIO → PIL Image → OpenCV ndarray → PaddleOCR，数据一直驻留内存
- 模板匹配是前置快速过滤器（<50ms），匹配成功后才执行 PaddleOCR（0.5-3 秒）
- 轮询独立于「启用武将识别」复选框，勾选轮询即可独立运行
- 轮询定时器永不自杀：条件不满足时 return 等待下一次 tick

#### 模板匹配的作用

模板匹配是整个 OCR 流程的**前置过滤**。只有匹配到武将选择页面（置信度 ≥ 阈值），才会执行 PaddleOCR 识别。阈值越高匹配越严格，避免对无关画面执行 OCR。

---

## 十三、测试体系细节

### 13.1 测试文件与用例数

| 文件 | 类 | 用例数 | 测试内容 |
|------|-----|--------|----------|
| test_models.py | TestSkill / TestHero / TestSynergyScore / TestHeroGuide / TestCard / TestIncrementalUpdate | 25 | Pydantic 模型校验 |
| test_ai_batch.py | TestLoadPrompt / TestEstimateCost / TestInternalEstimateCost / TestSaveJson / TestAIBatchGenerator / TestLoadHeroes / TestConfigLoading | 33 | AI 批量生成核心逻辑 |
| test_hero_manager.py | TestHeroManager | 13 | 武将 CRUD + 查询 |
| test_synergy_manager.py | TestSynergyManager | 13 | 相性 CRUD + 双向查询 |
| test_guide_manager.py | TestGuideManager | 11 | 攻略 CRUD |
| test_incremental_update.py | TestApplyIncrementalUpdate | 8 | 增量更新逻辑 |
| test_ui.py | TestEnvFileParsing | 4 | UI 工具函数 |

**总计：112 个测试用例，全部通过。**

### 13.2 AIBatchGenerator 测试要点

| 测试 | 验证内容 |
|------|---------|
| `test_extract_json_direct` | 直接解析合法 JSON |
| `test_extract_json_from_code_block` | 从 ```json 代码块提取 |
| `test_extract_json_from_separator` | 从 --- 分隔线后提取 |
| `test_validate_guide_success` | HeroGuide 完整数据校验通过 |
| `test_validate_guide_failure` | 缺少必填字段返回 None |
| `test_validate_synergy_success` | SynergyScore 完整数据校验通过 |
| `test_validate_synergy_failure` | score 超出范围返回 None |
| `test_combat_synergy_compatibility` | 旧字段兼容转换后通过 Pydantic |


### 13.3 测试约定

- 纯 pytest（不继承 `unittest.TestCase`）
- 文件 IO 使用 `tempfile` 避免影响真实数据
- Manager 测试使用 `_make_*` 辅助方法构造测试数据
- `sys.path.insert(0, "..")` 在测试文件内手动添加

---

## 十四、数据全流程详解

### 14.1 核心概念与分层

攻略/相性数据从生成到持久化的全流程横跨四层：

| 层级 | 文件 | 职责 |
|------|------|------|
| UI 层 | `src/ui/` | 用户操作触发、进度展示、后端选择 |
| 业务服务层 | `src/business/` | QProcess 子进程管理、参数构建、stdout/stderr 转发 |
| 子进程（采集层） | `src/scraper/` | 数据获取（API 或浏览器）、JSON 提取、校验、写入 |
| 数据管理层 | `src/data/` | JSON 文件加载、对象缓存、CRUD 接口 |

---

### 14.2 攻略数据全流程总图

```
┌──────────────────────────────────────────────────────────────────────────┐
│  UI 层（MainWindow）                                                     │
│  1. 用户选择生成模式（全量/增量/指定）                                      │
│  2. BackendChooseDialog 选择后端（API / 浏览器）                          │
│  3. GuideFetchService 构建子进程参数 → QProcess.start()                   │
└──────────────────────┬───────────────────────────────────────────────────┘
                       │ 子进程 stdout → UI 进度条
                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  GuideFetchService（主进程，QProcess 管理）                                │
│  参数: python -m src.scraper.ai_batch --guide [--update] [--browser]     │
│  增量/指定模式 → 写入临时 JSON → --heroes-file 传入子进程                  │
│  实时读取 stdout → 正则解析 [i/N] → 更新进度条                            │
│  finished 信号 → 检查 exit_code → 弹出完成/失败提示                        │
└──────────────────────┬───────────────────────────────────────────────────┘
                       │ 启动子进程
                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  ai_batch.py（子进程入口 CLI）                                             │
│  ① 加载武将数据 load_heroes()                                            │
│  ② 断点续传 _load_existing_guides() → 已有攻略 {hero_id: guide}          │
│  ③ 选择生成器：AIBatchGenerator / PlaywrightGenerator                     │
│  ④ 委托 run_guide_generation()                                          │
└──────────────────────┬───────────────────────────────────────────────────┘
                       │ 逐个武将
                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  run_guide_generation()（循环编排）                                       │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │ 循环体（每武将 1 次）：                                             │    │
│  │  ① 跳过/删除已有攻略                                              │    │
│  │  ② generator.generate_guide(hero)  ─────────→  二选一            │    │
│  │     ├── AIBatchGenerator （API 方式）                              │    │
│  │     └── PlaywrightGenerator（浏览器方式）                          │    │
│  │  ③ 成功: new_guides.append(result)                                │    │
│  │     stdout → "[i/N] 诸葛亮 OK"                                    │    │
│  │  ④ 每 10 条(GUIDE_BATCH_SAVE_INTERVAL) → _save_json()             │    │
│  │  ⑤ 循环结束 → 最终 _save_json()                                   │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
┌──────────────────┐   ┌────────────────────────┐
│ API 方式          │   │ 浏览器方式               │
│ AIBatchGenerator │   │ PlaywrightGenerator    │
└────────┬─────────┘   └────────────┬───────────┘
         │                          │
         ▼                          ▼
   HTTP POST ───────────→    Edge 浏览器 ──────────→  DeepSeek 网页版
   api.deepseek.com         chat.deepseek.com
         │                          │
         ▼                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        共同下游（同一份代码）                                │
│                                                                          │
│  Step 1: extract_json(content) ← AI 回复原始文本                          │
│     ├── 提取策略（4 种依次尝试）：全文 → ```json 代码块 → --- 分隔线后 → {} │
│     └── _repair_strings() 状态机修复字面换行符                              │
│                                                                          │
│  Step 2: 数据补充 & 类型转换                                              │
│     ├── raw["hero_id"] = hero.id          ← 注入武将 ID                   │
│     └── convert_ids_to_int(counters, synergizes_with)  ← 元素转 int      │
│                                                                          │
│  Step 3: Pydantic 校验                                                    │
│     └── validate_guide(raw) → HeroGuide.model_validate() → model_dump()  │
│                                                                          │
│  Step 4: _save_json(guide_path, all_guides) → data/guides.json           │
│     └── 原子写入：先写 .tmp → rename 覆盖                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

### 14.3 API 方式详细流程（AIBatchGenerator）

#### 14.3.1 调用链

```
AIBatchGenerator.__init__(api_key, api_url, model, rpm, ...)
  │
  ├── 内部创建 httpx.Client(timeout=300)
  ├── 限速器: _min_interval = 60.0 / rpm, _last_request_time = 0.0
  │
  ├── generate_guide(hero)
  │    ├── load_prompt("docs/prompts/hero_guide.md")        → system_prompt
  │    ├── build_guide_prompt(hero)                          → user_prompt
  │    │     字段: ID / 名称 / 势力 / 定位 / 体力 / 手牌 / 性别 / 技能
  │    ├── _call_api(messages=[system, user], temperature=0.7)
  │    │    ├── 检查距上次请求间隔（不够则 sleep 补齐）
  │    │    ├── POST {model, messages, temperature, max_tokens=8192}
  │    │    ├── 成功: 更新 _last_request_time, 返回 resp.json()
  │    │    └── 失败: 指数退避重试（2s/4s/8s, 最多 3 次）
  │    └── 返回 (result_dict, usage_dict)
  │
  └── close() → httpx.Client.close()
```

#### 14.3.2 API 原始报文

**请求报文**（由 `_call_api` 发出的 HTTP POST）：

```json
POST https://api.deepseek.com/v1/chat/completions
Authorization: Bearer sk-xxx
Content-Type: application/json

{
  "model": "deepseek-v4-pro",
  "messages": [
    {
      "role": "system",
      "content": "你是名将杀的攻略专家，请按指定 JSON 格式输出武将攻略..."
    },
    {
      "role": "user",
      "content": "武将ID: 52\n武将: 诸葛亮\n势力: 蜀\n定位: 辅助/控制\n体力: 4  手牌: 4\n性别: 男\n难度: 2\n\n技能:\n  - 观星: 摸牌阶段...\n  - 空城: 锁定技，你没有手牌时..."
    }
  ],
  "temperature": 0.7,
  "max_tokens": 8192
}
```

**响应报文**（DeepSeek API 原样返回）：

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "以下是对诸葛亮的攻略分析：\n\n---\n\n```json\n{\n  \"hero_id\": 52,\n  \"key_points\": [\n    \"观星是诸葛亮的核心技能，可以在摸牌阶段前控制牌堆顶牌序，判定阶段前控制判定牌\",\n    \"空城状态下免疫杀和决斗，但惧怕AOE伤害\"\n  ],\n  \"counters\": [114, 36],\n  \"synergizes_with\": [15, 42],\n  \"description\": \"诸葛亮是典型的控场型武将，利用观星调节牌序...\",\n  \"tips_for_beginners\": \"新手使用诸葛亮时，优先保证空城状态...\"\n}\n```\n\n### 总结\n诸葛亮在不同模式下皆有不错的出场率..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 1850,
    "completion_tokens": 420,
    "total_tokens": 2270
  }
}
```

#### 14.3.3 JSON 提取细节（`extract_json`）

从 `response.choices[0].message.content` 这段**自然语言文本**中提取 JSON 的 4 种策略：

| 优先级 | 策略 | 说明 | 适用场景 |
|--------|------|------|----------|
| 1 | 全文 `raw_decode` | 直接 `json.JSONDecoder().raw_decode()` 解析全文 | AI 纯 JSON 输出 |
| 2 | ```json 代码块 | 正则 ````(?:json)?\s*\n?(.*?)``` ```` | AI 用 Markdown 包裹 JSON |
| 3 | --- 分隔线后 | `rfind("\n---")` 取最后一段 | AI 先分析再输出 JSON |
| 4 | { 到 } 区间 | `find("{")` ~ `rfind("}")` 截取 | 兜底 |

每步先尝试直接解析，失败则走 `_repair_strings()` 修复（字符串值内的字面 `\r\n` → `\\n`），再重试。全部失败抛 `ValueError`。

---

### 14.4 浏览器方式详细流程（PlaywrightGenerator）

#### 14.4.1 调用链

```
PlaywrightGenerator.__init__()
  │
  ├── _ensure_browser()    ← 惰性启动，首次发送前初始化
  │    ├── sync_playwright.start()
  │    ├── chromium.launch_persistent_context(
  │    │     channel="msedge",
  │    │     user_data_dir="...Edge/User Data",
  │    │     headless=False, slow_mo=50
  │    │   )
  │    ├── page.goto("https://chat.deepseek.com/")
  │    └── _wait_for_login() → 等待 textarea 出现（15s 超时）
  │
  ├── generate_guide(hero)
  │    ├── 首次调用: system_prompt + user_prompt 拼接 → 一次性发送
  │    │            _guide_system_sent = True
  │    ├── 后续调用: 只发 user_prompt（携带武将 ID，会话复用）
  │    ├── _send_and_wait(prompt)
  │    │    ├── page.fill(textarea, prompt)
  │    │    ├── page.keyboard.press("Enter")
  │    │    ├── Phase 1: 轮询 assistant 消息数增加（每 500ms）
  │    │    ├── Phase 2: inner_text 长度连续 3 轮不变（每 2s）
  │    │    └── 返回最后一条 assistant 的 inner_text
  │    ├── extract_json(reply) → 与 API 方式同一函数
  │    ├── convert_ids_to_int + inject hero_id
  │    ├── validate_guide(raw) → 与 API 方式同一函数
  │    └── 后续调用: _random_rest() → 随机休息 60-180 秒
  │
  └── close() → context.close() → playwright.stop()
```

#### 14.4.2 浏览器原始报文

来自 DeepSeek 网页版 `div.ds-assistant-message-main-content` 的 `inner_text()`，纯文本格式：

```
以下是对诸葛亮的攻略分析：

诸葛亮在游戏中属于高操作上限的控场型武将，
观星让他在摸牌阶段前就能预判牌序...

---

{
  "hero_id": 52,
  "key_points": [
    "观星是诸葛亮的核心技能..."
  ],
  "counters": [114, 36],
  "synergizes_with": [15, 42],
  "description": "...",
  "tips_for_beginners": "..."
}

### 总结
诸葛亮在不同模式下皆有不错的出场率...
```

> 浏览器拿到的就是用户能在 DeepSeek 网页上看到的文本 — JSON 可能被自然语言分析文字包围，也可能直接以纯 JSON 输出。格式不稳定，这正是 `extract_json()` 设计 4 种回退策略的原因。

#### 14.4.3 会话复用机制

| 调用 | 发送内容 | 说明 |
|------|----------|------|
| 第 1 次 | `system_prompt + \n\n---\n\n + user_prompt` | 注入完整规则 |
| 第 2 次起 | `user_prompt`（带武将 ID） | AI 在上下文中记住规则 |

**意义**：避免每次重发数千字符的 system prompt，节省浏览器对话上下文长度，也减少风控触发频率。

#### 14.4.4 风控应对

- 后续每次生成后 `time.sleep(random.randint(60, 180))` — 随机休息 1~3 分钟
- 浏览器 headless=False — 可见窗口运行，降低被识别为自动化脚本的概率
- `--disable-blink-features=AutomationControlled` — 隐藏自动化特征

---

### 14.5 两条链路对比

| 环节 | API 方式 | 浏览器方式 |
|------|----------|------------|
| **生成器类** | `AIBatchGenerator` | `PlaywrightGenerator` |
| **数据源** | DeepSeek API（HTTPS） | DeepSeek 网页版（浏览器自动化） |
| **请求载体** | httpx.Client POST JSON | Playwright page.fill + Enter |
| **原始数据形式** | API 响应的 `choices[0].message.content`（JSON 字符串） | `div.inner_text()`（纯文本） |
| **system prompt 传递** | 每次独立请求都带完整 messages | 首次拼接发送，后续仅发数据（会话复用） |
| **获取回复机制** | 同步 HTTP 响应 body | Phase 1 + Phase 2 两阶段轮询等待 |
| **JSON 提取** | `extract_json()` | `extract_json()`（完全同一份代码） |
| **Pydantic 校验** | `validate_guide()` | `validate_guide()`（完全同一份代码） |
| **写入 JSON** | `_save_json()` 原子写入 | `_save_json()` 原子写入 |
| **Token 统计返回** | `usage` 字段（prompt/completion tokens） | `None`（不支持） |
| **断点续传** | ✅ 通过 `_load_existing_guides()` | ✅ 通过 `_load_existing_guides()` |
| **成本估算** | ✅ 支持 dry-run 显示 | ❌ 无 |
| **必备条件** | 有效的 API Key + 网络 | Edge 浏览器 + DeepSeek 已登录 |
| **风控对策** | 限速 + 指数退避重试 | 随机休息 60-180s |
| **速度** | 快（30 req/min 限速） | 慢（含休息时间） |

---

### 14.6 数据唯一出口：JSON 文件存储

无论哪种方式，最终写入 `data/guides.json` 的文件结构完全一致：

```json
[
  {
    "hero_id": 52,
    "key_points": [
      "观星是诸葛亮的核心技能...",
      "空城状态下免疫杀和决斗..."
    ],
    "counters": [114, 36],
    "synergizes_with": [15, 42],
    "description": "诸葛亮是典型的控场型武将...",
    "tips_for_beginners": "新手使用诸葛亮时，优先保证空城状态..."
  },
  {
    "hero_id": 1,
    "key_points": [...],
    "counters": [...],
    "synergizes_with": [...],
    "description": "...",
    "tips_for_beginners": "..."
  }
]
```

**写入策略**：
- 全量/增量生成：循环中每 10 条批量 `_save_json()`，循环结束最终保存
- 原子写入：`文件.tmp` → `json.dump()` → `tmp_path.replace(正式路径)`
- 断点续传：启动时加载已有文件建立 `{hero_id: guide}` 索引，新数据追加合并后覆盖写入

---

### 14.7 相性评分链路的差异

攻略和相性的数据链路几乎完全一致，仅以下环节不同：

| 环节 | 攻略 | 相性 |
|------|------|------|
| CLI 参数 | `--guide` | `--synergy` / `--synergy-pair` / `--synergy-single` |
| Prompt 模板 | `docs/prompts/hero_guide.md` | `docs/prompts/synergy_score.md` |
| 循环函数 | `run_guide_generation()` | `run_synergy_generation()` / `run_synergy_pair_generation()` / `run_synergy_single_generation()` |
| 生成器方法 | `generate_guide(hero)` | `generate_synergy(hero_a, hero_b)` |
| user_prompt 构建 | `build_guide_prompt(hero)` | `build_synergy_prompt(hero_a, hero_b)` |
| 注入字段 | `hero_id` | `hero_a_id` + `hero_b_id` |
| 旧字段兼容 | 无 | `combat_synergy` → `combo_ceiling` |
| 校验函数 | `validate_guide()` → `HeroGuide` | `validate_synergy()` → `SynergyScore` |
| 批量保存间隔 | 10 条 | 20 条 |
| 输出文件 | `data/guides.json` | `data/synergies.json` |

**相性原始数据格式**（AI 回复中的 JSON）：

```json
{
  "hero_a_id": 52,
  "hero_b_id": 114,
  "score": 8,
  "synergy_rating": "A",
  "combo_ceiling": 7,
  "combo_stability": 6,
  "adaptability": 8,
  "description": "诸葛亮与司马懿有很好的配合..."
}
```
