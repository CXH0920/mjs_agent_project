# 名将杀 Agent — 项目细节文档

> 代码基线：2026-07-22
> 项目路径：`G:\py_savepoint\test_project`  
> 远程仓库：`gitee.com:chen-xianghao920/test_project.git`  
> 文档日期：2026-07-22
> 事件归档：[PaddleOCR 优化事件归档](ocr_optimization_event.md)

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

## 当前代码基线与业务不变量（2026-07-31）

本节优先于后续历史性描述，用于维护时快速确认当前代码的边界和主调用链。项目是 PySide6 桌面辅助工具：UI 负责交互与信号编排，`src/business/` 按 `fetching`、`emulator`、`recognition`、`analysis`、`maintenance` 分隔 QProcess、ADB、OCR、分析和维护工作流，`src/scraper/` 负责官网与 AI 数据生成，`src/data/` 提供 JSON 持久化和内存模型。

### 核心功能调用总览

| 功能 | 主入口 | 关键调用顺序 | 结果 |
|------|--------|--------------|------|
| 应用启动 | `src.main.main()` | `get_runtime_params()` -> `setup_logging()` -> `QApplication()` -> `install_chinese_qt_translator()` -> `MainWindow.__init__()` -> `DataFacade.load_all()` -> `app.exec()` | 初始化日志和 Qt 标准控件中文翻译，加载数据、创建主窗口并进入事件循环；OCR 识别器按首次任务延迟初始化 |
| 武将采集 | `MainWindow._request_fetch_*()` | `HeroFetchService.fetch_*()` -> QProcess -> `official` / `incremental` CLI -> `crawler` | 更新英雄 JSON 与头像，完成后全量重载数据 |
| AI 攻略/相性 | `MainWindow._request_guide_*()` / `_request_synergy_*()` | `AiGenerationWorkflow.request_*()` -> 选择后端/进度 -> FetchService -> QProcess -> `ai_batch.main()` -> `run_*_generation()` | 每 10 条校验成功结果原子提交；任务结束后重载 Manager 并通知主窗口刷新 |
| 数据管理 | `MainWindow._open_data_management()` | `DataManagementDialog` -> 输入“清空”确认 -> `DataManagementService` 备份 -> 批量保存/失败恢复 | 清空攻略和/或相性，保留时间戳备份并刷新关联页面 |
| 官方榜单导入 | `MainWindow._open_official_data_import()` | 暂停自动轮询 -> `OfficialDataImportDialog` 有序多选 -> `CaptureService.submit_official_import()` -> `OcrWorker` -> `OfficialDataImportService.import_pages()` -> `official_board_parser` 新旧版式识别/行分割/数字模板 + 排名顺序校验 + 名称兜底 | 整批任务独占唯一 OCR worker；2v2 各页左右表分别合并到胜率、出场排行 CSV，放逐榜按页内左右顺序合并，全部校验后覆盖并生成带来源页的待复核数据 |
| 截图与 OCR | 推荐页操作或 `OcrService.poll_tick` | `PollCoordinator` -> `CaptureService` -> `AdbCapture.screencap_full()` -> `OcrWorker` -> 模板匹配 -> `GeneralRecognizer` | 将识别结果分发到推荐页或对局攻略页 |
| 数据浏览与编辑 | `HeroBrowser` | `HeroListPanel` -> `HeroDetailPanel` -> `DataMutationService` -> Manager 保存 | 创建备份后写入对应 JSON，并在失败时恢复 |

### 数据完整性与只读恢复

`DataFacade.load_all()` 的职责不仅是读取三个 JSON，还包括返回 `LoadReport` 并保存至 `last_load_report`：

```
DataFacade.load_all()
  -> HeroManager.load() / SynergyManager.load() / GuideManager.load()
  -> 每条记录 model_validate()，坏记录和重复键记录为 DataIssue 后跳过
  -> _validate_references(report)
    -> 仅记录悬空相性、攻略归属和攻略关联 ID 的问题
  -> return LoadReport
```

加载过程不会调用 `save()`，原始 JSON 和内存数据均保持不变。主窗口会向用户展示 `missing_reference` 问题，并仅在用户确认后通过 `DataMutationService` 创建备份、修复失效关联并保存；拒绝修复时保留原始数据。

### 进程与任务提交边界

`BaseFetchService` 的 QProcess 生命周期为 `_start_process()` -> `readyReadStandardOutput` / `readyReadStandardError` -> `_on_finished()` 或 `_on_error()`。stdout 先进入字节缓冲，只对完整换行行解码并交给子类解析，进程结束时再 flush 最后一行，避免 Qt 分块读取造成进度丢失或中文乱码。取消只调用 `kill()`，由 `finished` 信号统一清理上下文和发送状态，GUI 线程不做同步等待。成功以 CLI 退出码判定，AI CLI 有失败项会 `exit(1)`，不依赖 `RESULT: FAIL=` 文本协议。AI 生成每批校验成功结果原子提交到 `guides.json`、`synergies.json`，失败项保留对应旧数据。

OCR 工作由一个 `OcrWorker` 串行队列执行。`OcrService` 管理轮询、冷却、退避与模板生命周期，`PollCoordinator` 负责轮询任务的后台编排、过期结果过滤和状态提交；`CaptureService` 通过单一后台执行器串行执行 ADB 连接和截图，手动截图与轮询不会并发访问同一会话。`match_guide` 由 `hero_selection` 命中一次性解锁，识别成功后停用，直到下次选将命中才重新激活；每次选将命中都会重置对局攻略页的自动跳转边沿，因此每局首次命中均可跳转。

---

## 一、爬虫与数据采集模块细节

### 1.1 文件位置与层级关系

```
src/scraper/official.py                    ← 全量采集兼容 CLI
src/scraper/incremental.py                 ← 增量/指定采集兼容 CLI
src/scraper/official_source/adapter.py     ← 官网 HTML/JS chunk 格式适配
src/scraper/official_source/crawler.py     ← 网络请求、数据清洗、校验与头像下载
src/scraper/official_source/full.py        ← 全量采集实现
src/scraper/official_source/incremental.py ← 增量/指定采集实现
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

#### 1.2.3 `adapter.py`：JS chunk 解析适配器

官网 HTML 与 JS chunk 的格式假设集中在 `src/scraper/official_source/adapter.py`；`crawler.py` 仅负责请求编排和数据清洗。

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
4. `parse_heroes_chunk(js_text)` → 165 条原始数据

#### 1.2.8 头像下载（第 297-348 行）

**`download_hero_images(raw_list, image_dir, skip_existing) → int`**：
- 遍历 `raw_list`，取 `icon_url` 和 `name`
- 角色名经白名单校验后，文件路径固定为 `images/{武将名}.png`
- 仅允许 HTTPS 官方图片域名，重定向目标逐跳复验
- 以 64 KiB 分块写入临时文件，响应最大 5 MiB；Pillow 验证 PNG 格式与最大 4,000,000 像素
- 本地 OCR/ROI 输入仅接受实际 PNG/JPEG，ADB 内存截图仅接受实际 PNG；所有入口均将解压炸弹警告视为错误
- 验证成功后原子替换正式头像；失败时删除临时文件并保留已有头像
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
| [3/5] 解析数据 | `js_to_json(extract_js_array())` | 打印原始条数(165) |
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

### 2.0 RAG 语料增强（攻略 / 相性，2026-08 更新）

攻略与相性生成（API/浏览器双模式）默认启用 RAG 官方规则语料注入；UI 在「生成方式确认」对话框提供 **RAG 语料增强（推荐）/ 经典模式（无 RAG 注入）** 单选，默认 RAG 增强，经典模式向子进程追加 `--no-rag`（等价于 `RAG_ENABLED=false`），输出与旧版一致。

- **攻略注入** `rag_prompt.py::build_rag_context(hero)`：`Retriever.hero_blocks()` 取该武将全部语料块，再以 `heroes=[武将名]` 元数据硬过滤补充检索；只注入目标武将自己的块。
- **相性注入** `rag_prompt.py::build_synergy_rag_context(hero_a, hero_b)`：
  1. 第一段确定性召回双方武将全部语料块（总览 + 技能/结算 + 其分类块）；
  2. 第二段跨类检索（不带武将过滤）：查询串 = 双方武将名 + 技能名 + 技能描述中命中的 `retriever.KEYWORDS` 机制词（去重、上限 20），让规则/FAQ/卡牌/装备等跨类块进入；
  3. 过滤：`metadata.hero` 存在且不属于两名目标武将的块一律丢弃，防止其他武将语料混入 prompt。
- **降级**：检索/注入异常时返回空串并记录 `rag_prompt.degraded_reason`；生成循环消费一次，在 stdout 输出 `[RAG] 语料不可用，本次已降级为经典模式（原因）`（进度窗口可见），任务不中断。
- **语料与索引**：`data/rag_corpus/`、`data/rag_index/chroma/`（随仓库入库）；嵌入模型缓存不入库，默认共享 `mjs_rag_project/rag/.cache/modelscope`（`config.env` 的 `RAG_MODEL_DIR` 可覆盖）。
- **配置**：`RAG_ENABLED`（true）、`RAG_TOP_K`（12）、`RAG_PROMPT_CHARS`（6000）、`RAG_BROWSER_PROMPT_CHARS`（3000）、`RAG_SYNERGY_PROMPT_CHARS`（6000）、`RAG_MODEL_DIR`。
- **CLI**：`--no-rag` 禁用增强；`--rebuild-rag-index` 重建向量索引后退出；dry-run 分别展示 RAG 增强与经典模式两套成本。
- **维护**：`python scripts/maintain_rag.py --force --build-index` 或应用内「知识库维护」页面。
- **T0 元规则文档增量维护（2026-08-15，工作台 2026-08 落地）**：`docs/元规则整理-完整版.md` 为规则专家知识库 T0 权威文档，只增不删语义。完整工作流：① 官方更新先跑 `scripts/diff_source_data.py` 对比 `data/backups` 生成变更清单（含“是否新机制”启发式标记）；② `scripts/audit_rule_doc.py` 机器校验（解析回声/表格结构/块 ID 唯一/ID 稳定性/FAQ 编号/确认状态一致性/交叉引用/已定稿块指纹/章节结构指纹，`--strict` 可进 CI，快照 `scripts/.rule_doc_snapshot.json`）；③ `scripts/sync_rule_stats.py` 把 `data/*.json` 统计同步到文档数据快照段（0.1/0.2/3.1/3.2/3.5/5.2，full 全自动、candidate 半自动、checkpoint 校验点）；④ 新机制走提案-确认（`scripts/propose_rule_changes.py` 用 DeepSeek 起草结构化提案，模板 `docs/templates/元规则提案单.md`，归档 `docs/archive/proposals/`；人工把条目置 approved/revised/rejected）；⑤ `scripts/apply_rule_proposal.py` 合入（faq_new/faq_revise/term_new/row_revise/section_new）→ audit --strict（失败回滚）→ 重建元规则语料 → 写 `docs/changelog/元规则changelog.md` → 提案归档；⑥ 疑难先登记 `docs/rule_doc_pending.json`，可一键转 FAQ 提案；⑦ `scripts/eval_rule_faqs.py` 做 FAQ 裁定回归评估（向量检索命中率，零 LLM 成本，评估集 `data/rag_evals/rule_faq_eval.json`）。`maintain_rag.py` 的「元规则/术语/FAQ」任务改为 dynamic（按快照块数只增校验，任务成功自动刷新快照）。以上全部能力集成在「知识库维护 → 元规则维护」页签（`rule_doc_panel.py` + `rule_doc_service.py`），完整流程见 `docs/元规则T0文档维护方案.md`。
- **T0 源数据与可视化维护（2026-08 迁移）**：RAG 源数据已从 xlsx 拆分为 JSON——`data/card_points.json`（162 张牌花色点数，72 组合 × 数量 + 12 条牌名级判定规则）、`data/equip_attrs.json`（26 件装备属性）、`data/special_cards.json`（专属牌/专属战法牌并入并回填花色/点数/攻击范围/结算详情，当前 83 条）；xlsx 归档 `data/archive/`，「知识库维护」页提供语料状态 / 元规则维护 / 专属牌 / 卡牌点数 / 装备属性 / 武将分类六个页签，保存后自动标记待重建；`scripts/migrate_excel_to_json.py` 保留“从 xlsx 导入”应急通道。

### 2.1 模块文件关系

```
ai_batch.py (兼容 CLI) -> ai/batch.py (实际入口)
 ├── 创建 AIBatchGenerator 或 PlaywrightGenerator
 ├── 委托给各 run_* 函数
 └── ai/generation.py → run_guide_generation() / run_synergy_generation()
                         / run_synergy_pair_generation() / run_synergy_single_generation()
                         _save_json() 写入结果
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
| `--no-rag` | bool | False | 禁用 RAG 语料增强（默认启用） |
| `--rebuild-rag-index` | bool | False | 重建 RAG 向量索引后退出 |

#### 2.2.2 生成器选择逻辑

```python
if args.browser:
    from src.scraper.ai.browser_generator import PlaywrightGenerator
    generator = PlaywrightGenerator()
else:
    _check_api_key(api_config)
    generator = AIBatchGenerator(...)
```

#### 2.2.3 断点续传 / 更新模式

**攻略**：
- `--update`（增量/指定获取）：更新模式，重新生成已有项，**不跳过已有**
- 无 `--update`（全量获取）：断点续传，跳过已存在的 `hero_id`

**相性**：
- `--synergy`（全量生成）：始终重新生成所有组合；成功配对分批覆盖，失败配对保留旧数据
- `--synergy-single`（选定武将）：断点续传，已有的相性对跳过不重复生成
- `--synergy-pair`（指定配对）：更新模式，支持 2~8 武将，用 itertools.combinations 遍历 C(N,2) 配对；成功配对按批提交

武将输入先经 `HeroManager` 完整校验，任一错误都会终止生成。攻略或相性断点文件存在错误时，原文件保留为 `.corrupt-时间戳.json`，当前路径只写回对应 Manager 已验证的记录；备份失败时不覆盖原文件。

#### 2.2.4 浏览器模式的 token 处理

浏览器模式返回 `(result, None)`，不以 token 统计判断成败，避免误报失败。

### 2.3 AIBatchGenerator 详细说明（api_generator.py）

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

### 2.4 generation.py — 四种生成循环

```python
def run_guide_generation(heroes, generator, guide_path, existing_guides, api_config, update_mode=False)
```

流程：
1. 遍历所有武将
2. `update_mode=False` 时跳过已存在；`update_mode=True` 时在工作副本中覆盖旧数据
3. 输出 `"[i/N] hero_name OK"`（被进度条正则匹配）
4. `generator.generate_guide(hero)` → `(result, usage)`
5. 累计 usage
6. 每 10 条（`GUIDE_BATCH_SAVE_INTERVAL`）校验成功后原子提交正式文件
7. 任务结束时提交尾批；失败项保留原有数据

### 2.5 run_synergy_generation() — 全量相性生成

始终重新生成所有 `N*(N-1)/2` 对组合，失败配对保留旧正式数据。
每 10 条（`SYNERGY_BATCH_SAVE_INTERVAL`）校验成功后原子提交，结束时提交尾批。
每条成功结果写入 `SynergyScore.last_updated` 当日日期；低于评分阈值而移除的结果不写入正式相性文件。

### 2.6 run_synergy_pair_generation() — 指定配对（支持 2~8 武将）

- 读取包含 2~8 个武将的 JSON 文件
- 用 `itertools.combinations(pair_heroes, 2)` 遍历所有 C(N,2) 组合
- 输出进度 `[i/total]` 与实际配对数同步
- 每 10 对校验成功结果原子提交一次，结束时提交尾批
- 任一失败时仅保留该配对旧数据

### 2.7 run_synergy_single_generation() — 选定武将 x 全体

支持断点续传：已有的相性对跳过不重复生成；新增成功项按批提交，失败项不改变旧数据。

---

## 三、业务服务层细节

### 3.1 服务类一览

| 类 | 文件 | 行数 | 父类 | 信号数量 |
|--------|------|------|------|----------|
| BaseFetchService | `base_fetch_service.py` | ~70 | QObject | 3 |
| HeroFetchService | `fetch_service.py` | ~102 | BaseFetchService | 3 |
| GuideFetchService | `guide_fetch_service.py` | ~179 | BaseFetchService | 6 |
| SynergyFetchService | `synergy_fetch_service.py` | ~104 | BaseFetchService | 3 |
| CaptureService | `capture_service.py` | ~426 | QObject | 4 |
| EmulatorOperationService | `emulator_operation_service.py` | ~111 | QObject | 8 |
| MumuConfigCoordinator | `mumu_config_coordinator.py` | ~220 | QObject | 10 |
| OcrService | `ocr_service.py` | ~355 | QObject | 3 |
| OfficialDataImportService / Worker | `official_data_import_service.py` | ~610 | 普通类 / QThread | 3（Worker）；版式解析委托 `official_board_parser.py` |

> `BaseFetchService` 提供 QProcess 管理的通用方法（`_is_busy`、`_start_process`、`_on_stdout_ready`、`_on_finished`、`_on_error`、`cancel`），三个子类继承后各自实现 `fetch_*` 方法和信号定义。

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
progress_output = Signal(str)              # 子进程 stdout 行
progress_value = Signal(int, int)          # 进度条 (current, total)
fetch_completed = Signal(bool, str)        # (成功/失败, 消息)
error_occurred = Signal(str)               # 错误信息
```

#### 三个 fetch 方法统一后端参数

每个方法新增 `backend` 参数（`"api"` 或 `"browser"`）与 `use_rag` 参数（默认 True），UI 层通过 `BackendChooseDialog` 完成确认后调用 `execute_with_confirmation()`。

#### `execute_with_confirmation()` 逻辑

1. 读取 `self._context` 中的 `mode`、`heroes`、`backend`、`use_rag`
2. 构建 `base_args = ["-m", "src.scraper.ai_batch", "--guide"]`
3. `use_rag=False`（经典模式）→ 追加 `--no-rag`
4. `backend == "browser"` → 追加 `--browser`
5. `mode` 为 `"incremental"` / `"specific"` → 追加 `--update`（更新模式）
6. `mode` 为 `"incremental"` / `"specific"` → 写入临时文件，追加 `--heroes-file`
7. 启动 QProcess

#### 子进程错误日志增强

- `SeparateChannels` 模式：分别读取 stdout 和 stderr
- `readyReadStandardError` → 实时输出到日志
- `_on_finished` 非零退出 → 输出完整 stdout + stderr
- `_on_error` 输出错误类型名

### 3.4 SynergyFetchService

同 GuideFetchService 模式，但 args 不同：
- `fetch_pair(heroes, backend, overwrite=False, use_rag=True)` → `--synergy-pair <tmp_file>`
- `fetch_single(hero, all_heroes, backend, use_rag=True)` → `--synergy-single <tmp_file>`
- 同样支持 `backend` 参数追加 `--browser`
- `use_rag=False`（经典模式）→ 追加 `--no-rag`

### 3.5 CaptureService（截图业务服务）

```python
class CaptureService(QObject):
    status_changed = Signal(str)           # 状态消息
    capture_completed = Signal(dict)       # {image, save_path, ocr_results, ocr_matched}
    capture_failed = Signal(str)           # 错误消息
```

截图操作直接在 Python 中执行（不通过 QProcess），因为需要即时获取图像数据更新 UI。
模板匹配与 PaddleOCR 识别提交到唯一的 `OcrWorker` 后台队列；结果通过信号回到 GUI 线程。`QTimer.singleShot(0, ...)` 仅延后回调，并不提供异步执行。

**主要方法**：

| 方法 | 说明 |
|------|------|
| `update_config(config)` | 更新配置并重建 AdbCapture（路径/端口变化时重建） |
| `do_capture(hero_names, perform_ocr)` | 执行截图；选将推荐和对局攻略的截图按钮均传入 `perform_ocr=False`，仅保存到 `screenshots/` |
| `do_capture_from_file(file_path, hero_names)` | 从本地图片执行 OCR（不依赖 ADB） |
| `connect_emulator()` | 连接模拟器 |
| `disconnect_emulator()` | 断开模拟器 |
| `capture_screenshot()` | 通过共享会话获取截图，不写文件、不执行 OCR；供模板制作后台任务使用 |

**手动截图全流程**：

```
do_capture()
  └─ QTimer.singleShot(0, _execute_capture)
       ├─ AdbCapture.screencap_full() → PIL Image
       ├─ 保存截图到 screenshots/ 目录（手动调用路径特有）
       └─ emit capture_completed({image, save_path, ocr_results, ocr_matched})

do_capture_from_file()
  └─ QTimer.singleShot(0, _execute_file_ocr)
       ├─ PIL.Image.open(file_path)
       └─ _queue_capture_ocr()
            └─ submit_ocr_task() → OcrWorker.submit(OcrTask)
                 └─ OcrWorker._execute() → 模板匹配 → GeneralRecognizer.recognize()
                      └─ _on_ocr_task_completed() → capture_completed
```

**注意**：轮询路径不走 `do_capture()`，轮询在后台执行 `screencap_full()`，随后将模板匹配与 OCR 提交给同一个 `OcrWorker`，**不保存截图文件到磁盘**，全程内存中处理。这样轮询与手动导入仍按任务顺序共用一个识别器。

### 3.6 OcrService（OCR 控制服务）

选将推荐自动跳转由 `MainWindow._on_poll_result()` 负责。模板首次匹配成功时，如果当前尚未处于选将页面，则切换到“选将推荐”Tab；冷却期间再次匹配只更新 OCR 推荐内容，不重复切换。只有 `healthy_no_match` 明确表示模板未匹配时，才清除页面状态。截图失败、连接重试和 OCR 重试不会清除该状态。

```python
class OcrService(QObject):
    status_changed = Signal(str)           # 状态消息
    template_changed = Signal(bool)        # 模板加载/已删除
    ocr_completed = Signal(list)           # 识别结果
    poll_tick = Signal()                   # 轮询触发信号（由 QTimer 驱动，连接至 PollCoordinator._on_poll_tick）
```

**主要方法**：

| 方法 | 说明 |
|------|------|
| `update_config(config)` | 更新配置缓存 |
| `set_hero_names(names)` | 设置武将候选词表（名称门禁与候选解析用） |
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

### 3.7 OfficialDataImportService（官方榜单导入）

该服务处理本地官方图片，独立于 ADB、页面模板匹配和 `GeneralRecognizer`，但由通用 `OcrWorker` 在同一线程中调用。图片读取、旧版长图/新版分页版式识别、面板切分、数据行恢复、单元格切分和胜率数字模板算法由 `src.ocr.official_board_parser` 提供；服务保留多页聚合、排名顺序校验、姓名纠错、复核、进度与 CSV 输出，并使用 worker 注入的 PaddleOCR 引擎。模型预热、常规识别和官方整批导入按 FIFO 串行，避免多个 Paddle native 线程池并发初始化或推理。

```
OfficialDataImportDialog._start_import()
  -> CaptureService.submit_official_import(paths)
    -> OcrWorker.submit(OfficialImportTask)
      -> emit official_progress(status, 0, 0)              # 排队、读取、版式识别与行检测阶段
      -> OfficialDataImportService.import_pages(key, paths, callback)
      -> read_image() -> detect_layout() -> extract_panels()
      -> find_data_boundaries() -> 旧版横线/新版排名行锚点 -> restore_missing_boundaries()
      -> prepare_rate_templates()（仅 2v2 胜率表）
      -> _recognize_row() -> 名称/胜率识别（名称歧义按需受限繁体兜底） -> _review_reasons()
      -> _validate_panel_rank_sequence()                   # 有充分 OCR 证据时阻止页面错序
      -> _resolve_batch_names() -> 榜单内部唯一性补全未决名称
      -> _validate_output_names() -> 未知名/重复名/集合不一致时阻止正式覆盖
      -> 全部校验通过 -> _write_csv() -> Path.replace() 原子覆盖
      -> [胜率] clear_win_rate_cache()
      -> CaptureService emit official_import_completed(summaries)
```

**版式、输出与进度：**

| 图片 | 表格 | 输出 | 工作量 |
|---|---|---|---|
| 2v2 | 左“胜率最高” | `2v2胜率排行.csv` | 胜率模板准备 + 每行识别 |
| 2v2 | 右“出场最多” | `2v2出场排行.csv` | 每行识别 |
| 武将放逐 | 左 1-80、右 81-160 | `武将放逐.csv` | 两栏视觉行序合并后逐行识别 |

Worker 先发出 `progress_changed(status, 0, 0)`，UI 显示不定进度；检测到横线后以总工作单元切换为 `current / total`。相邻横线间距超过中位行高 1.5 倍时，服务按常规行高补插边界，并将补插边界后的行写入待复核，避免横线漏检导致后续排名前移。2v2 出场榜和放逐榜的排名/武将分界为面板宽度的 45%，避免排名数字进入名称 ROI；胜率格向左扩展 4px，避免首位数字贴线时被截断。

**名称降级策略：**

1. `_recognize_cell_candidates()` 保留原图放大及增强锐化的全部 OCR 文本。候选去除非汉字后精确命中 `heroes.json` 时优先使用完整候选；若两路精确结果指向不同武将，则不按置信度强选。
2. 最高候选为单字时，`_recognize_name_glyphs()` 用亮色列切分 2-4 个字形，保留原始背景与留白逐字 OCR；拼接结果经 `CharacterSimilarityService.correct_hero_name()` 校正后必须命中词表。
3. 逐字补识别失败时，只有 OCR 原文作为词表前缀的候选唯一才补全；`夏侯`、`司马`等公共前缀对应多个武将时禁止自动补全。
4. 多个候选共享至少两个汉字前缀时，不使用编辑距离或微小视觉分差强行决胜；服务按需加载 `chinese_cht` 繁体模型继续确认。繁体原文及其编辑距离纠正结果必须仍属于简体 OCR 产生的候选白名单，禁止从“卫青/卫玠”等候选跳转到无关武将。
5. 繁体模型仍不能确认时保留 OCR 原文。整榜识别结束后，从该行候选中排除榜单里已经确认的武将；只有剩余一个候选且没有其他未决行竞争该名称时才自动补全，并记录补全依据。

每个正式 CSV 都有对应的 `*_待复核.csv`。异常记录含 OCR 原文、置信度、原因、原图坐标及 `screenshot_data/official_import/` 下的行截图；通过榜单唯一性补全的行也保留复核记录。若最终存在未确认名称、重复名称，或同规模的 2v2 胜率/出场榜武将集合不一致，服务只更新待复核证据并报错，原正式 CSV 和推荐指数状态保持不变。名称完整性通过后，其他低置信度、排名 OCR 不一致或胜率模板异常仍按视觉行序写入正式 CSV 并留待复核。

**已知限制：** 当前字体中的罕见字或偏旁结构可能被 OCR 漏掉。候选受限繁体兜底和榜单唯一性可以恢复“卫玠”这类同前缀候选中只剩一个未占用名称的情况；若多个候选均未出现，或 OCR 直接误识别成另一个不重复的合法完整姓名，服务会因无法证明唯一映射而保留待复核或依赖同规模榜单集合校验，不做无依据改绑。

为提升兜底能力（2026-08-14）：新增混淆字对校正（`候↔侯`、`怀↔惇`，变体唯一命中词表才采用）、未知名字字形回退、跨榜单一致性消歧；校验失败时将完整批次写入 `data/official_import_pending.json`，导入对话框可打开“待复核修正”逐行选择词表内武将后重新写入，不重新 OCR；放逐榜导入支持页末右栏不满（左栏满栏且右栏不超过左栏）。


---

## 四、数据管理层细节

### 4.1 DataManager 泛型基类

`DataManager[V_co]`（位于 `manager.py`）定义了所有 Manager 共用的通用操作：

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `load()` | — | list[DataIssue] | 逐条校验并返回该文件的问题列表 |
| `save()` | — | None | 原子写入 JSON（tmp → rename） |
| `get(key)` | 泛型 | V_co \| None | 通用字典键查询 |
| `list_all()` | — | list[V_co] | 全部数据 |
| `add(item)` | V_co | None | 新增（重复抛 ValueError） |
| `update(item)` | V_co | None | 覆盖式 upsert |
| `delete(key)` | 泛型 | None | 删除（不存在静默） |

三个子类 Manager 提供领域键并复用 `_parse_models()`：坏记录和重复键仅跳过该项并记录 `DataIssue`，不会阻断同文件中的其他合法记录。`DataFacade.load_all()` 汇总为 `LoadReport`，再执行英雄、相性和攻略之间的引用校验。

### 4.2 文件与数据量

| 数据文件 | 管理类 | 数据量 |
|----------|--------|--------|
| `data/heroes.json` | HeroManager(DataManager[Hero]) | 165 武将 |
| `data/synergies.json` | SynergyManager(DataManager[SynergyScore]) | 55 条相性（当前数据） |
| `data/guides.json` | GuideManager(DataManager[HeroGuide]) | 162 份攻略（当前数据） |
| `data/cards.json` | — | 基础卡牌 |
| `config/faction_colors.json` | —（直接读取） | 势力配色，支持在设置中新增势力 |
| `data/card_points.json` | CardPointsRepository | 162 张牌花色点数（72 组合）+ 12 条判定规则 |
| `data/equip_attrs.json` | EquipAttrsRepository | 26 件装备属性（细分/攻击范围/距离修正） |
| `data/special_cards.json` | SpecialCardRepository | 专属牌/专属战法牌/特殊牌区/状态·标记/概念（83 条） |
| `data/hero_classification.json` | HeroClassificationRepository | 武将分类/克制链/武将归类 |

### 4.3 HeroManager 方法清单

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

### 4.4 SynergyManager 方法清单

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `add_synergy(score)` | SynergyScore | None | (A,B) 或 (B,A) 已存在抛 ValueError |
| `get_synergy(a_id, b_id)` | (int, int) | SynergyScore \| None | 自动排序 key |
| `update_synergy(score)` | SynergyScore | None | 覆盖 |
| `delete_synergy(a_id, b_id)` | (int, int) | None | 自动排序 key |
| `list_synergies()` | — | list[SynergyScore] | 全部 |
| `list_synergies_for_hero(hero_id)` | int | list[SynergyScore] | 该武将涉及的所有相性 |

`SynergyScore.last_updated` 记录最后成功生成相性评分的日期，格式为 `YYYY-MM-DD`。手工编辑相性内容时保留该日期，不把编辑操作误记为重新生成。

**双向归一实现**：
```python
def _make_key(self, a_id: int, b_id: int) -> tuple[int, int]:
    return tuple(sorted([a_id, b_id]))
```
`(A=114, B=115)` 和 `(A=115, B=114)` 均映射到 `(114, 115)`。

### 4.5 GuideManager 方法清单

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `add_guide(guide)` | HeroGuide | None | 同一 hero_id 重复抛 ValueError |
| `get_guide(hero_id)` | int | HeroGuide \| None | 按武将 ID 查询 |
| `update_guide(guide)` | HeroGuide | None | 覆盖 |
| `delete_guide(hero_id)` | int | None | 不存在静默 |
| `list_guides()` | — | list[HeroGuide] | 全部 |

### 4.6 DataFacade 门面

```python
class DataFacade:
    heroes: HeroManager
    synergies: SynergyManager
    guides: GuideManager

    def load_all(self) → LoadReport # 三个 load()、引用校验和问题汇总
    def save_all(self) → None      # 三个 save() 依次调用
    def get_stats(self) → dict     # 返回 {heroes: N, synergies: N, guides: N}
```

### 4.7 增量更新

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
| main_window.py | 870 | QMainWindow（顶层装配、菜单、应用外壳与界面绑定） |
| shell_widgets.py | 247 | QWidget（左侧 NavigationRail 与顶部 ContextHeader） |
| poll_coordinator.py | 263 | QObject（轮询编排、后台任务与结果状态迁移） |
| ai_generation_workflow.py | 246 | QObject（攻略与相性 UI 工作流） |
| hero_browser.py | 586 | QWidget（资料左栏、身份头部、上下文操作和详情协调） |
| hero_detail_views.py | 469 | QWidget（信息、攻略摘要和相性 Tab 渲染） |
| card_management_panel.py | 842 | QWidget（卡牌浏览、版本调整与字段配置） |
| hero_edit_dialog.py | 87 | QDialog（武将编辑） |
| guide_edit_dialog.py | 120 | QDialog（攻略编辑） |
| hero_relation_select_dialog.py | 129 | QDialog（攻略关系武将多选） |
| synergy_edit_dialog.py | 96 | QDialog（相性编辑） |
| recommendation_panel.py | 903 | QWidget（Tab 内嵌） |
| mumu_config_dialog.py | 750 | QDialog（模拟器配置状态与操作协调） |
| mumu_config_sections.py | 297 | QGroupBox（设备、模板和 OCR 参数视图） |
| hero_select_dialog.py | 267 | QDialog（基类） |
| settings_dialog.py | 305 | QDialog（API 配置） |
| roi_selector.py | 149 | QDialog（框选模板区域） |
| backend_choose_dialog.py | 141 | QDialog |
| guide_progress_dialog.py | 135 | QDialog |
| official_data_import_dialog.py | ~100 | QDialog（榜单图片选择、进度条、完成/失败提示） |
| style.py | 247 | 样式表常量 |
| fetch_dialog.py | 31 | QDialog（继承基类） |
| guide_fetch_dialog.py | 29 | QDialog（继承基类） |
| synergy_pair_dialog.py | 49 | QDialog（继承基类，覆盖 _on_accept 允许 2~8 武将） |
| synergy_single_dialog.py | 30 | QDialog（继承基类） |

### 5.2 主窗口外壳与信号拓扑

阶段三应用外壳由左侧 `NavigationRail`、顶部 `ContextHeader`、工作区内容和底部状态栏组成。左侧导航固定承载资料库、选将推荐、对局攻略三个长期工作区；顶部显示当前工作区标题、说明、资料库操作及全局设置。主内容继续复用原 `QTabWidget` 和三个页面实例，仅隐藏主 `TabBar`；资料库内部仍使用可见的“武将资料 / 卡牌图鉴”二级页签，因此搜索、滚动、识别结果和二级页签状态不会因切换工作区而丢失。

左侧导航请求通过主 Tab 容器切换，Tab 的 `currentChanged` 再同步导航选中态和 `ContextHeader`。OCR 自动跳转保持原调用边界，只执行 `setCurrentWidget()`，无需直接访问外壳控件。窗口宽度小于 1040px 时导航强制折叠；回到宽屏后恢复用户本次会话中的展开/折叠选择。

兼容菜单栏和顶部入口共享 `MainWindow._actions` 创建的 `QAction`，不重复绑定业务回调，`Ctrl+Q` 与 `F5` 保持有效。底部状态栏左侧显示数据统计及采集、生成、截图、OCR 预热等当前任务消息；右侧模拟器状态常驻显示 ADB 连接；OCR 状态常驻显示轮询运行状态。任务消息不会覆盖后两者，点击常驻状态可打开模拟器配置。

```
MainWindow.__init__
 ├── HeroFetchService ─── 武将采集
 │   ├── status_changed → _on_fetch_status (状态栏)
 │   ├── fetch_completed → _on_fetch_completed (弹窗提示)
 │   └── error_occurred → _on_fetch_error (弹窗警告)
 ├── AiGenerationWorkflow ─── 攻略与相性 UI 编排
 │   ├── GuideFetchService
 │   │   ├── status_changed → workflow.status_changed → _on_fetch_status
 │   │   ├── progress_output/value → GuideProgressDialog
 │   │   ├── fetch_completed → GuideManager.load() → guides_changed
 │   │   └── error_occurred → 详细错误弹窗
 │   ├── SynergyFetchService
 │   │   ├── status_changed → workflow.status_changed → _on_fetch_status
 │   │   ├── progress_output/value → GuideProgressDialog
 │   │   ├── fetch_completed → SynergyManager.load() → synergies_changed
 │   │   └── error_occurred → 警告弹窗
 │   ├── guides_changed → _on_guides_generated → 更新统计状态栏
 │   └── synergies_changed → _on_synergies_generated → 刷新浏览器/推荐页和统计
 ├── CaptureService ─── 截图
 │   ├── status_changed → _status_label.setText
 │   ├── capture_completed → _on_capture_completed / _on_capture_result (通知推荐面板)
 │   └── capture_failed → QMessageBox.warning
 └── OcrService ─── OCR + 持续轮询
     ├── status_changed → _status_label.setText
     ├── template_changed → 更新 UI 状态
     └── poll_tick → _on_poll_capture (轮询编排：截图→模板匹配→OCR→结果填入推荐面板)
```

### 5.3 对局攻略页面（MatchGuidePanel）

对局攻略与资料库、选将推荐同属左侧导航的一级工作区。页面内部使用 `PageActionBar` 展示识别状态、唯一主要识别操作和“更多”菜单，不重复外壳标题。结果区采用不可折叠的 42/58 水平分割：左栏固定阵容确认区，并在独立纵向滚动区展示四张 176～250px 宽的紧凑卡片；右栏展示总览、我方打法、对抗敌方和单将详情。两侧禁止横向滚动，长文本自动换行。

页面可识别已连接的 MuMu 画面或从本地图片导入，结果态将图片导入、保存截图和清空阵容收纳到“更多”。两种识别入口均通过 `template_name="match_guide"` 和 `force_ocr=True` 更新卡片；保存截图不触发 OCR。卡片分别展示识别状态与敌我席位状态，并用互斥“我方 / 敌方 / 未定”分段控件调整；【楚军】/【汉军】标签只用于校验席位结果。`LineupState` 继续负责槽位、敌我人数限制、主将和显式确认；新 OCR 或人工调整会清除旧分析并要求重新确认。

### 5.4 模拟器配置对话框（MumuConfigDialog）

位于 配置 → 模拟器配置，与 SettingsDialog 同级菜单入口。

`MumuConfigCoordinator` 持有配置草稿、设备列表、共享会话状态，以及模板和 ROI 布局截图生命周期，集中调用 `CaptureService`、`EmulatorOperationService` 与 `OcrService`。`MumuDeviceSection`、`MumuTemplateSection` 和 `MumuOcrPollingSection` 分别构建设备页、识别任务面板和自动化控件并发出操作信号；`MumuConfigDialog` 负责双页导航组装、状态协调、文件选择、ROI 框选及用户提示。关闭对话框时由协调器停止后台操作，避免迟到回调更新已销毁的控件。

**功能分区**：

```
┌────────────────────────────────────────────────────────┐
│ 模拟器配置                              ● ADB 会话状态 │
├──────────────┬─────────────────────────────────────────┤
│ 设备与连接   │ 当前设备 [MuMu 实例 ▼] [刷新]           │
│              │ 端口 / 实例运行状态                      │
│ 识别与自动化 │ ADB 路径 [只读] [浏览] [自动探测]       │
│              │                      [连接] [测试连接]    │
│              │                                         │
│              │ 识别页：OCR/轮询开关                    │
│              │ [武将选择：模板/阈值/冷却/ROI]          │
│              │ [对局攻略：模板/阈值/ROI]               │
├──────────────┴─────────────────────────────────────────┤
│                                       [取消] [保存]     │
└────────────────────────────────────────────────────────┘
```

顶部 ADB 状态与底部操作栏固定，两个内容页独立纵向滚动。识别任务在宽窗口中双列展示，在最低 760px 宽度下自动切换为上下排列，不产生横向滚动。

**连接管理**：
- **自动探测**：通过注册表、环境变量 `MUMU_HOME`、常见安装路径查找 `adb.exe`
- **多设备切换**：`QComboBox` 下拉列出所有 MuMu 实例（● 运行中 / ○ 未运行）
- **一键连接/断开**：单按钮切换，委托 `EmulatorOperationService` 在后台调用 `CaptureService` 的共享 ADB 会话，并通过信号同步状态
- **状态监控**：实例「运行中」使用绿色文字，未运行、未探测和刷新失败使用灰色；ADB 状态按灰色「未连接」→ 橙色「连接中...」→ 绿色「已连接」→ 红色「连接失败」显示

**模板管理**：
- **即时操作**：选择或制作模板后立即加载，不需要点击底部保存
- **武将识别模板**：后台获取共享会话截图 → `RoiSelectorDialog` 框选 ROI → `OcrService.create_template()` 保存到 `templates/wujiang_select.png`
- **对局攻略模板**：独立保存到 `templates/match_guide/template.png`，不会覆盖武将识别模板

**OCR 配置**：
- **启用 OCR 识别**：供显式 OCR 调用路径使用；选将推荐的截图按钮不会自动 OCR
- **持续轮询**：独立于手动截图，定时检测模拟器画面（详见第十二章 12.6 节）
- **匹配阈值**：武将选择和对局攻略分别配置；对局攻略在每次选将模板命中后只触发一次
- **轮询间隔**：1-60 秒；未勾选持续轮询时禁用
- **恢复轮询**：仅持续轮询已勾选且服务处于暂停状态时显示并可用
- **识别区域编辑**：选将推荐编辑 8 个名称区域；对局攻略编辑 5 组名称和阵营区域。可从共享 ADB 截图或本地图片打开编辑器，保存后下一次识别立即使用新布局；恢复默认只清除当前页面的本地覆盖
- **保存反馈**：保存识别参数时固定底栏进入 busy 状态，成功后显示短暂 Toast 并关闭

### 5.5 区域框选对话框（RoiSelectorDialog）

在预览图上拖拽鼠标选择矩形区域，返回 `(x, y, w, h)` 坐标给调用方。

**交互流程**：
```
鼠标按下 → 记录 drag_start
鼠标移动 → 实时更新选框 + 坐标信息
鼠标释放 → 完成拖拽
确认 → 按 pixmap/label 缩放比例计算实际 ROI
取消 → 返回 None
```

**坐标缩放**：QLabel 显示缩放后的预览图时，坐标按图片实际绘制区域映射回原图尺寸，并排除等比缩放产生的黑边。

`RoiLayoutEditorDialog` 在同一预览中覆盖全部 ROI，通过下拉选择席位后拖拽编辑；保存时以当前图片尺寸建立新的 `reference_size`。

### 5.6 后端选择对话框（BackendChooseDialog）

**布局**：
```
┌──────────────────────────────────────────────────────┐
│ 标题: 选择生成方式                                     │
│ 语料增强: (•) RAG 语料增强（推荐） ( ) 经典模式     │
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

**语料增强选择**：
- 顶部单选组「RAG 语料增强（推荐）/ 经典模式（无 RAG 注入）」，默认 RAG 增强，对 API/浏览器两种后端均生效
- `get_selected_rag() -> bool` 返回选择；`AiGenerationWorkflow._choose_backend()` 返回 `(backend, use_rag)` 元组并透传获取服务
- API Tab 成本估算随选择实时重算：`estimate_item_cost(items, estimate_kind, model, use_rag=...)`（经典模式输入 token 更少）；`estimate_kind`（"guide"/"synergy"）由工作流写入 estimation
```

### 5.7 攻略生成进度条（GuideProgressDialog）

**UI 组成**：
- 状态文字（"已生成 XXX 的攻略..."）
- 进度条（`current / total`）
- 详情标签（灰色，12px）
- 错误标签（红色，隐藏）
- 关闭按钮（执行中禁用，完成时启用）

**进度更新正则**（OK/FAIL 分开匹配）：
```python
# OK 匹配 — 更新进度条
m = re.search(r"\[(\d+)/(\d+)\]\s*(.+?)\s+OK", text)
# FAIL 匹配 — 更新状态文字但不推进进度条
m = re.search(r"\[(\d+)/(\d+)\]\s*(.+?)\s+FAIL", text)
```
匹配格式 `"[1/3] 诸葛亮 OK"` 时更新进度条；`"[2/3] 司马懿 FAIL"` 时仅更新状态文字为"生成失败"，不推进进度条位置。

> 失败由 CLI 的非零退出码统一表达；父进程只解析 stdout 中的进度行，不再依赖失败文本协议。

### 5.8 资料库内容页

#### 5.8.1 武将资料（HeroBrowser）

由两个子组件构成：

```
HeroBrowser (QWidget)
 ├── HeroListPanel（左, 240–360px，默认 280px）
 │   ├── QLineEdit（搜索框）
 │   ├── QComboBox（势力筛选）
 │   ├── QLabel（当前筛选结果计数）
 │   ├── QListWidget（武将列表）
 │   ├── Signal: hero_selected(int)
 └── HeroDetailPanel（右，占据剩余宽度）
     ├── 身份头部（名称、势力、定位、体力、手牌摘要）
     │   ├── 当前内容编辑按钮
     │   └── 更多菜单 → 当前内容删除
     ├── Tab 1「武将信息」→ HeroInfoView
     │   ├── QLabel (HTML 渲染基本信息与资料更新时间)
     │   └── QScrollArea (技能列表)
     ├── Tab 2「攻略指南」→ HeroGuideSummaryView
         ├── QScrollArea（统一滚动容器）
         ├── 核心建议（核心要点与应对）
         ├── 新手提醒与流式关系标签
         └── [阅读完整攻略] → GuideDetailDialog
     └── Tab 3「武将相性」→ HeroSynergyView
         ├── 搭档名称/评级筛选与相性表格
         ├── 双击说明列打开 Markdown 预览
         └── 修改/删除写入 SynergyManager 并通知推荐页刷新
```

**模块边界与编辑保存链路：**

```
HeroDetailPanel._on_info_edit()
  -> HeroEditDialog.get_hero()
  -> DataMutationService.update_hero() -> 创建备份 -> HeroManager.save()

HeroDetailPanel._on_guide_edit()
  -> GuideEditDialog._open_relation_selector()
     -> HeroRelationSelectDialog.exec() -> selected_ids
  -> GuideEditDialog.get_guide()
  -> DataMutationService.update_guide() -> 创建备份 -> GuideManager.save()

HeroDetailPanel._on_synergy_edit()
  -> SynergyEditDialog.get_synergy()
  -> DataMutationService.update_synergy() -> 创建备份 -> SynergyManager.save()
```

`hero_browser.py` 保留列表、当前详情状态、编辑对话框、局部刷新和 `data_changed` 通知；三个 Tab 的控件构造与只读渲染位于 `hero_detail_views.py`。编辑器从表单构造并重新校验模型副本，所有编辑、删除写入均交给 `DataMutationService` 统一创建快照、备份与保存；写入失败时重新显示同一编辑实例，因此原模型不被提前修改且输入仍可继续调整。四个对话框分别位于 `hero_edit_dialog.py`、`guide_edit_dialog.py`、`hero_relation_select_dialog.py`、`synergy_edit_dialog.py`。

相性 Tab 的刷新顺序为 `HeroDetailPanel.show_hero()` -> `HeroSynergyView.show_hero()` -> `refresh()` -> `SynergyManager.list_synergies_for_hero()`。双击非说明列或点击修改会打开 `SynergyEditDialog`；保存时通过 `DataMutationService.update_synergy()` 写入，随后触发 `synergies_changed`，由 `MainWindow._on_synergies_changed()` 刷新选将推荐数据。说明列双击使用 `PageHeader + QTextBrowser + DialogFooter` 阅读 Markdown，不修改数据。

**当前内容上下文操作**：
- 身份头部始终只显示一个直接编辑按钮；武将信息、攻略指南、武将相性分别映射为“编辑武将”“编辑攻略”“编辑相性”
- 对应的“删除武将”“删除攻略”“删除相性”收纳在相邻省略号“更多”菜单中，并继续弹出原二次确认
- 无当前武将、无攻略或未选中相性记录时，编辑和删除入口同步禁用
- 页签切换只更新操作文字、处理方法和可用状态，不改变原对话框、`DataMutationService` 或持久化契约
- 编辑保存后触发 `data_changed` 信号刷新左侧列表，选中项保持为当前武将并显示 Toast；写入失败时保留输入，删除完成使用模态结果反馈
- `GuideEditDialog` 中的劣势/优势对局类型和对抗建议通过文本输入编辑；“搭配推荐”通过 `HeroRelationSelectDialog` 选择，支持搜索、势力筛选、预选回填、全选当前筛选和清空选择；确认时按英雄 ID 的稳定顺序写回 `HeroGuide`
- 关系展示标签采用自适应流式可跳转布局；势力筛选改为复用选将推荐配色、带可删除标签、搜索、全选和反选的多选下拉框，超过 5 个势力时显示前 5 个及剩余数量
- 数据栏的武将获取、攻略获取、武将相性三个指定获取对话框统一复用 `CheckableComboBox`，保持相同的势力标签和浅蓝色复选列表交互；右侧上下箭头会明确显示筛选下拉框当前是展开还是收起

**Markdown 渲染**：统一通过 `src.ui.shared.markdown_renderer.render_markdown()` 调用 Mistune，并转义原始 HTML；攻略正文超过 20,000 字时不进入解析。`Skill`、`SynergyScore` 与 `HeroGuide` 的 AI 文本和列表字段均由 Pydantic 限制长度及项目数。

**攻略视觉与交互：**
- 主浏览页保留列表与详情摘要，方便快速切换武将。
- 左侧检索栏限制为 240–360px，搜索和势力筛选下方显示当前结果数量；分隔区两侧均不可完全折叠。
- 右侧身份头部展示当前武将，名称和元数据可换行；内容 Tab 使用弱化的下划线样式，避免与资料库内容页内的二级资料切换器竞争。
- 首屏“核心建议”优先展示核心要点和面对该武将的应对；新手提醒、关系标签和完整攻略入口按需呈现。
- 点击“阅读完整攻略”打开 `GuideDetailDialog`，其中保留 Markdown 正文预览。
- 正文标题明确标注“攻略正文（双击查看完整内容）”；双击预览后打开 `GuideMarkdownDialog` 阅读完整正文。
- 克制/搭配关系使用自适应流式标签，点击后通过 `HeroDetailPanel.hero_requested` 切换到对应武将。
- 武将信息、攻略和相性详情均关闭横向滚动，只允许内容区纵向滚动或文本换行。
- 武将浏览器完成列表信号连接后会主动同步首个默认选中武将，确保启动后右侧详情不会停留在“请选择一个武将”。

#### 5.8.2 卡牌图鉴（CardManagementPanel）

卡牌图鉴与武将资料共享资料库二级导航。顶部工具栏提供搜索、卡牌类型、调整状态、重置和省略号“更多”；左栏限制为 240–360px，显示结果计数、类型分组和卡牌摘要。右侧只用一个基础资料表面承载卡牌身份、官方只读标识、卡牌简述和规则详解，版本调整作为后续内容区，不在基础表面内嵌套卡片。详情区关闭横向滚动，切换卡牌后回到顶部。

“编辑版本调整”只打开当前卡牌的 `CardAnnotationEditDialog`，不编辑 `cards.json`；追加字段的“字段配置”仍位于顶部“更多”菜单并打开 `CardFieldSchemaDialog`。效果记录继续只允许 `active`、`pending`、`expired` 三种状态，界面分别显示“生效中”“待核实”“已失效”，并组合使用绿色、警示色、中性灰与左侧强调线。`active` 置顶，其他记录按状态和修改时间排序；创建时间和修改时间不在前端展示。

### 5.9 选将推荐面板（RecommendationPanel）

```
RecommendationPanel (QWidget)
 ├── PageActionBar：识别状态 + [识别当前阵容] + [更多]
 ├── NoticeBanner：指数过期或可恢复错误
 └── QScrollArea → QGridLayout (2列 × 4行，仅纵向滚动)
      └── HeroCardWidget × 8
           ├── 头像区 (100×129px)
           │   ├── QPixmap (从 images/name.png 加载)
           │   ├── QGridLayout 叠加
           │   │   ├── 名称浮层 (底部, rgba(0,0,0,140))
           │   │   └── 势力标签 (左上角, 色块)
           └── 信息区 (弹性)
               ├── 定位 · 推荐指数 + [技能] [查看攻略] 按钮
               ├── 定位 · 推荐指数（“辅助 · 推荐指数：92 / S”格式；悬停或点击指数查看明细，右侧口径图标始终保留，数据缺失时显示“推荐指数：-- / 数据不足”）
               ├── 分隔线
               ├── 高相性组合标题
               ├── QGridLayout (2列, 搭配+评分)
               ├── 分隔线
               └── 历史单将胜率（前三使用固定 TOP 1/2/3 徽章）
```

**势力配色**：
从 `config/faction_colors.json` 配置文件加载，启动后缓存到全局变量。文件不存在时使用内建兜底配色：

```json
{
  "秦": "#8B4513", "汉": "#B22222", "楚": "#2F4F4F",
  "赵": "#556B2F", "魏": "#800020", "燕": "#6A0DAD",
  "齐": "#1B7A3D", "韩": "#CD853F",
  "孙吴": "#4169E1", "蜀": "#228B22", "曹魏": "#800020",
  "群雄": "#8B0000", "晋": "#4A6741", "新朝": "#B8860B"
}
```

配色通过公开共享模块 `src/ui/shared/faction_colors.py` 管理：`load_faction_colors()` 负责读取和校验 JSON，`get_faction_colors()` 提供带内建兜底色的缓存，`reload_faction_colors()` 在配置保存后清空缓存并重新加载。势力配色对话框允许新增势力并在点击“保存”后写入该文件，但不允许删除或改名。推荐面板、对局攻略、武将浏览器和可勾选组合控件只依赖这些公开函数，未知势力使用灰色 `#888` 兜底。

**共享 UI 与胜率数据访问**：
- `src/ui/shared/widgets.py` 提供 `DoubleClickLabel`，统一头像双击信号，推荐卡片和对局攻略卡片复用同一控件。
- `src/ui/shared/hero_dialogs.py` 提供 `HeroSkillDialog`，技能描述和结算详情弹窗不再由业务页面私有实现。
- `src/data/win_rate_repository.py` 的 `load_win_rates()` 读取 `data/2v2胜率排行.csv`，默认路径结果缓存；推荐面板和对局攻略页面通过该仓库查询胜率，避免重复实现 CSV 解析。
- `src/data/recommendation_index_repository.py` 读取胜率、出场和放逐三份官方榜单，以 `heroes.json` 的 ID 作为稳定次级排序，生成 `data/武将推荐指数.csv`。低胜率英雄仍显示推荐分，但在自动推荐排序中降级；缺失、越界或重复排名的数据不参与计算。该快照仅由选将推荐页的“重建指数”按钮手动覆盖，其他页面行为只读取已有文件。

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

**识别与截图流程**：
1. “识别当前阵容”调用 `CaptureService.do_capture(hero_names, force_ocr=True)`，完成后填入 8 个槽位。
2. “更多 > 从图片导入”提交本地图片到 `OcrWorker`。
3. “更多 > 保存截图”调用 `do_capture(perform_ocr=False)`，不更新推荐结果。
4. `_pending_capture_source` 防止重复提交并隔离共享服务回调；空结果和失败通过页内 `NoticeBanner` 提供恢复提示。

**`load_from_ocr(ocr_results)`**：
- 接收 OCR 结构化结果 `[{index, raw_name, name, candidates, resolution, confidence, evidence}, ...]`，并兼容旧的 `{index, name, confidence}`
- `name` 为空的待确认槽位只显示 OCR 原文与候选，不加载武将资料、推荐指数、胜率或相性；候选确认只影响当前页面
- 将 name 匹配 HeroManager 中的 Hero 对象
- 加载 `images/<name>.png` 头像
- 刷新当前版本推荐指数快照，卡片以“推荐指数：星级 + 评级”显示；悬停或点击星级查看胜率、出场排名、禁用排名及自动推荐排序，右侧口径图标在数据有效和不足时均保留
- 根据武将名从 `synergies.json` 加载高相性组合数据
- 高相性组合在 OCR 模式下**仅显示当前 8 个武将之间的相性**，不显示数据库中其他武将的相性（通过 `_current_hero_ids` 集合和 `_ocr_mode` 标志控制过滤）
- 根据武将名从 `2v2胜率排行.csv` 加载历史单将胜率，随即对 8 个槽位按胜率降序排名，前三标记固定尺寸的 TOP 1/2/3 徽章
- 未匹配到 HeroManager 的武将名仍显示名称文字供人工判断

**攻略按钮（`HeroCardWidget`）**：
每个 `HeroCardWidget` 信息区头部提供蓝色强调的 [查看攻略] 次要按钮：
- 按钮尺寸 76×26，使用 `PRIMARY_SOFT` 背景、`PRIMARY` 边框和加粗文字，悬停时反转为蓝底白字
- 点击时通过 `guide_clicked = Signal(int)` 信号发射武将 ID
- `RecommendationPanel._show_guide_popup(hero_id)` 接收信号，通过 `GuideManager.get_guide()` 获取攻略
- 弹出 `GuideDetailDialog`（QDialog，默认 720×680，最大高度 760），以外层滚动区展示摘要与正文预览；正文标题标注双击打开方式，双击后由 `GuideMarkdownDialog` 展示完整 Markdown 正文
- 无攻略数据时弹窗显示"暂无攻略数据"

推荐页面已按职责拆分：`recommendation_panel.py` 负责 8 个槽位的数据刷新、相性/胜率查询及截图/OCR 信号协调；`hero_card_widget.py` 负责卡片的展示和交互信号；`guide_detail_dialog.py` 负责攻略详情渲染与关系跳转。原面板模块继续导入并暴露两个类名，旧调用方无需改动。

### 5.10 对话框基类体系

```
BaseHeroSelectDialog (hero_select_dialog.py, ~293行)
 ├── SelectionMode 枚举: MULTI / MULTI_LIMIT / SINGLE
 ├── ReturnFormat 枚举: IDS / HEROES_DICT
 ├── 搜索框 + 势力网格 + 复选框列表 + 已选计数 + 确认/取消
 │
 ├── HeroFetchDialog (fetch_dialog.py, ~31行)
 │   SelectionMode=MULTI, ReturnFormat=IDS
 │
 ├── GuideFetchDialog (guide_fetch_dialog.py, ~90行)
 │   SelectionMode=MULTI, ReturnFormat=HEROES_DICT
 │   依赖 HeroManager + GuideManager，按未生成/待更新/已有攻略筛选
 │
 ├── SynergyPairDialog (synergy_pair_dialog.py, ~49行)
 │   SelectionMode=MULTI_LIMIT, max_selection=8，覆盖 _on_accept 允许选择 2~8 个武将
 │
 └── SynergySingleDialog (synergy_single_dialog.py, ~30行)
     SelectionMode=SINGLE
```

选将推荐使用独立的非继承基类弹窗：

| 类 | 文件 | 用途 |
|----|------|------|
| GuideDetailDialog | `guide_detail_dialog.py` | 选将推荐卡片按钮触发的攻略详情弹窗（默认 720×680，最大高度 760），单列分区并支持滚动 |

### 5.11 官方数据导入对话框（OfficialDataImportDialog）

该对话框为 2v2 与武将放逐各提供一个有序图片列表，可单独或同时导入。添加图片时按文件名自然排序，用户可移除或上移、下移；旧版长图使用单项列表，新版分页按列表显示顺序合并。导入期间 `DialogFooter` 进入 busy 状态并禁用列表、导入、取消和关闭操作；进度先显示当前分析/识别页，收到 Worker 的总工作量后显示该类榜单的 `current / total`。完成后通过 Toast 显示每类榜单的图片数、导入条数和待复核条数；失败时恢复控件并隐藏进度条。

```
_start_import()
  -> CaptureService.submit_official_import(paths)
  -> official_import_progress -> _on_progress_changed()
  -> official_import_completed -> _on_completed() -> accept()
  -> official_import_failed -> _on_failed()
```

该窗口不读取图片、不进行 OCR、不写 CSV；这些操作全部委托给业务服务层。主窗口在弹窗打开期间停止活跃轮询，并在弹窗退出后按连接状态恢复；已入队的常规任务和官方整批任务由唯一 `OcrWorker` 顺序执行。

### 5.12 UI 设计系统（shared/style.py + shared/widgets.py）

`src.ui.shared.style` 统一提供视觉 Token、全局 QSS 和动态语义属性；`src.ui.shared.widgets` 提供页面标题、空状态、状态标签、通知条、标准弹窗底栏和非模态 Toast。`DialogFooter.set_busy()` 统一防重复提交，`show_toast()` 在同一父窗口复用反馈控件并重置隐藏计时器。配置、管理、编辑、选择、导入、进度、ROI 与详情弹窗统一使用标题区、内容区和固定底栏，不改变业务信号和数据流。

**核心颜色 Token**：

| 元素 | 颜色 |
|------|------|
| 主操作/我方 | `#2f6ea5` |
| 成功/已连接 | `#26734d` |
| 警告/待核实 | `#a15c00` |
| 危险/敌方 | `#b23a3a` |
| 页面背景 | `#f4f6f8` |
| 内容表面 | `#ffffff` |
| 主要文字 | `#1f2933` |
| 次要文字 | `#66717e` |
| 边框 | `#d7dee7` |

按钮通过 `uiRole` 使用 `primary`、`secondary`、`ghost`、`danger`；状态展示通过 `tone` 使用 `neutral`、`info`、`success`、`warning`、`danger`。完整规则见 `docs/spec/spec_ui_design_system.md` 和 `docs/spec/spec_ui_navigation.md`，三档窗口截图位于 `docs/ui_baseline/`；阶段三应用外壳使用 `after-shell-`，阶段四资料库使用 `after-library-`，阶段五、六识别工作台使用 `after-workspaces-` 前缀。

### 5.13 知识库维护工作台（RagMaintenancePanel）

主导航第 4 页，本地维护 RAG 语料与源数据，包含 6 个页签：

| 页签 | 数据源 | 能力 |
|------|--------|------|
| 语料状态 | `scripts/maintain_rag.py` + `rag_audit.py` | 8 个语料任务状态表（最新/待重建/缺源 + 块数）、结构化人工维护审计横幅（可跳转定位）、一键重建（QProcess 实时日志） |
| 元规则维护 | `docs/元规则整理-完整版.md` | 文档状态（audit）/ 数据段差异（sync）/ 提案工作台（propose+apply）/ 疑难登记（pending）四个子页签 |
| 专属牌维护 | `data/special_cards.json` | 5 类条目 CRUD，专属牌/战法牌含花色/点数/攻击范围/结算详情字段 |
| 卡牌点数维护 | `data/card_points.json` | 72 花色点数组合 × 数量与 12 条判定规则增删改；「从 xlsx 导入」应急通道 |
| 装备属性维护 | `data/equip_attrs.json` | 26 件装备细分/攻击范围/距离修正表格编辑 + 保存校验 |
| 武将分类维护 | `data/hero_classification.json` | 分类 CRUD / 克制链 / 武将归类 |

联动机制：任一子面板保存 → `data_changed` 信号 → `refresh()` 重算任务状态并标记“待重建” → 用户点击“重建全部语料 / 重建语料+索引”调用 `maintain_rag.py`。审计由 `AuditIssue`（kind/message/severity/target_tab/target）结构化驱动，覆盖：未归类武将、special_cards 引用未知武将、卡牌点数花色/张数=162、装备件数/字段、专属牌结算回填率（死士豁免）、索引字段待精化（排第一位）；每条可点「跳转」定位到对应维护页签（`HeroClassificationPanel.focus_unclassified()` / `SpecialCardsPanel.focus_item()` / 直接打开索引精化）。

语料层「索引精化」对话框（`IndexRefinementDialog`，1160×720）2026-08 重设计：对卡牌/武将语料中无 curated 且索引字段为空的块补 `timing / trigger_condition / keywords / related` 四个字段；顶部总览条（进度 + 全部/卡牌/武将筛选）、清单区（搜索 + 4 列表格，行状态 ○/◉/✎）、工作区（左原文卡片占满高度 + 右字段状态卡片 empty/llm/manual 着色）、底部操作条（跳过/保存当前/保存全部）；LLM 建议（DeepSeek）全部模式用 QTimer 队列逐块处理不冻结窗口，保存全部以已生成建议为 baseline，切回条目还原建议内容，重建不覆盖。入口按钮带待精化数量角标。

#### 5.13.1 元规则维护页签（RuleDocPanel）

维护对象为规则知识库 T0 母本（只增不删、机器校验），四个子页签与建议流程：① 刷新检查（`audit_rule_doc.py`）→ ② 应用数据段差异（`sync_rule_stats.py --json` 预览、勾选 + 确认值后 `--apply-json`）→ ③ 生成/合入提案（`propose_rule_changes.py --no-llm` / `apply_rule_proposal.py --proposal`）→ ④ 登记疑难（`docs/rule_doc_pending.json`，可转 FAQ 提案）；改动完成后回「语料状态」重建语料+索引。`rule_doc_service.py` 提供 audit 输出解析、差异解析、提案读写（含原子更新）、疑难登记等纯函数；所有脚本经 QProcess 执行，输出统一汇入底部可折叠日志区，顶部状态栏按 ERROR/全自动差异/WARN/候选/校验点/待确认提案/待消化疑难给出下一步建议。

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

### 6.4 来源感知的错误输出链路

```
子进程 exit_code ≠ 0
     ↓
_on_finished()
     ├── stdout/stderr 已按行写入 subprocess.official.* 或 subprocess.ai.*
     ├── 业务日志仅记录服务名、退出码和归一化原因
     └── error_occurred → UI 显示明确原因

子进程 QProcess::ProcessError
     ↓
_on_error()
     ├── 错误类型映射 → "子进程启动失败" / "子进程崩溃" 等
     └── errorString() → logger.error()
```

失败时不再把完整 stdout/stderr 复制进业务日志；缓冲区只用于识别“思考过程耗尽输出额度”等明确原因。AI 进度信号仅放行 START、OK、FAIL、SKIP、开始和冷却状态，其他子进程日志不会显示在进度界面。

### 6.5 临时文件管理

- 指定获取（`fetch_specific` / `fetch_pair` / `fetch_single`）将武将数据写入临时 JSON 文件
- 临时文件路径存在 `self._context["tmp_path"]` 中
- `_on_finished()` 和 `_on_error()` 都会调用 `_cleanup_tmp()` 清理
- 清理失败（`OSError`）只打 warning 不阻断流程

### 6.6 结构化任务结果与分批提交

`generation.py` 的四种编排函数返回 `GenerationResult`。它统一记录 token、完成/跳过数、失败项与提交状态；`ai_batch.py` 据此决定退出码，任一失败项都会返回非零。

每累计 10 条攻略或相性校验成功，生成器即通过临时文件原子替换正式 JSON；任务结束时再提交不足一批的结果。失败项不改写其原有记录，已提交批次不会回滚。父进程仅解析 `[i/N]` 进度行，依据退出码通知 UI 成败，不再解析 `RESULT` 文本协议。

### 6.7 进度对话框 OK/FAIL 分开匹配

`GuideProgressDialog.update_status()` 不再使用 `(?:OK|FAIL)` 匹配：

```python
# 分开匹配，FAIL 不推动进度条
m_ok = re.search(r"\[(\d+)/(\d+)\]\s*(.+?)\s+OK", text)
if m_ok:
    self._status_label.setText(f"已生成 {m_ok.group(3)} 的攻略...")
    self.update_progress(int(m_ok.group(1)), int(m_ok.group(2)))
    return
m_fail = re.search(r"\[(\d+)/(\d+)\]\s*(.+?)\s+FAIL", text)
if m_fail:
    self._status_label.setText(f"生成失败: {m_fail.group(3)}")
    return  # 不更新进度条
```

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
  │ ├── 字面 \n → \\n
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
| `get_runtime_params()` | 获取运行时参数（含 `log_to_file: bool`） |
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
RAG_ENABLED=true
RAG_TOP_K=12
RAG_PROMPT_CHARS=6000
RAG_BROWSER_PROMPT_CHARS=3000
RAG_SYNERGY_PROMPT_CHARS=6000
```

### 8.5 原子保存

```python
tmp_path = env_path.with_suffix(".env.tmp")
tmp_path.write_text("...", encoding="utf-8")
tmp_path.replace(env_path)
```

---

## 九、浏览器自动化细节

### 9.1 PlaywrightGenerator 与 DeepSeekBrowserSession

#### 9.1.1 生命周期

```
PlaywrightGenerator.__init__ → DeepSeekBrowserSession
  ├── generate_guide()/generate_synergy() → 提示词、JSON 提取和模型校验
  └── _send_and_wait() → DeepSeekBrowserSession.send_and_wait()
                            │
                            └──（惰性）_ensure_browser → close
                                 ├── sync_playwright.start()
                                 ├── chromium.launch_persistent_context(...)
                                 ├── page.goto("https://chat.deepseek.com/")
                                 └── _wait_for_login() → 等待 textarea 出现
```

#### 9.1.2 会话复用

`_guide_system_sent` 和 `_synergy_system_sent` 控制首次发送完整 `system_prompt + 数据`，后续只发送 `数据`（带武将 ID），让 AI 在同一会话中按已设定的规则持续生成。

**随机休息**：每次成功生成后，下一次请求发送前随机休息 60-180 秒，避免触发风控；最后一项完成后不额外等待。

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
│   ├── official.log         # 官网采集及父进程接管的采集输出
│   └── ai_generation.log    # AI 生成及父进程接管的生成输出
├── business/
│   ├── fetching.log         # QProcess 编排
│   ├── emulator.log         # 截图、ADB 与 MuMu 协调
│   ├── recognition.log      # OCR 调度与官方榜单导入
│   └── business.log         # 分析、维护及其他业务日志
├── data/
│   └── data.log
├── ocr/
│   └── ocr.log
├── capture/
│   └── capture.log
└── subprocess/
    └── unclassified.log     # 未声明工作流的子进程输出
```

### 10.3 模块过滤表

| logger name 前缀 | 目标文件 |
|-----------------|----------|
| `src.scraper` / `subprocess.official` | `scraper/official.log` |
| `src.scraper.ai` / `subprocess.ai` | `scraper/ai_generation.log` |
| `src.business.fetching` | `business/fetching.log` |
| `src.business.emulator` | `business/emulator.log` |
| `src.business.recognition` | `business/recognition.log` |
| `src.business.analysis` / `src.business.maintenance` | `business/business.log` |
| `src.data` | `data/data.log` |
| `src.capture` | `capture/capture.log` |
| `src.ocr` | `ocr/ocr.log` |
| 其他 `subprocess.*` | `subprocess/unclassified.log` |
| 其他（含 `src.ui.*`） | `app.log` |

QProcess 子进程设置 `MJS_QPROCESS_CHILD=1` 后不直接打开文件 Handler。武将采集由父进程以 `subprocess.official.*` 接管，攻略和相性生成以 `subprocess.ai.*` 接管；两者分别进入官网和 AI 日志。每条记录只进入一个目标文件，历史的 `scraper.log`、`ai_batch.log`、`stdout.log` 和 `stderr.log` 不自动删除或迁移。

### 10.4 日志轮转

- 单个日志文件最大 10MB
- 保留 5 个备份（`app.log.1` ~ `app.log.5`）
- 超过上限自动轮转

### 10.5 异常处理规范

所有 except 块遵循以下规范：
- 使用 `logger.error("描述: %s", e)` 记录错误
- 使用 `logger.debug(traceback.format_exc())` 在 DEBUG 级别输出堆栈
- 不允许 `except: pass` 或空 except 块

AI 链路只记录任务、长度、字段名、用量、耗时和错误摘要；不得记录 Prompt、回复正文、解析后正文、页面正文、认证信息或 `reasoning_content`。进度界面只接收明确的生成进度和冷却状态行。

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
| `probe_all_devices_with_status()` | `(list[MuMuDeviceInfo], str)` | 列出实例；MuMuManager 失败时重试一次并返回错误原因 |
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
4. 配置页通过 `probe_all_devices_with_status()` 调用：异常退出或超时会重试一次；失败时保留当前设备选择而不将其误显示为“未探测到设备”

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
 ├── image_preprocessor.py  # ImagePreprocessor — 纯图像预处理
 ├── official_board_parser.py # 官方榜单新旧版式、数据行锚点、单元格与数字模板算法
 ├── character_feature_repository.py # CharacterFeatureRepository — 特征缓存
 ├── character_similarity.py # CharacterSimilarityService — 名称纠错
 ├── recognizer.py           # GeneralRecognizer — ROI、PaddleOCR 与组件编排
 ├── paddle_loader.py        # PaddleOCR 统一构造及 Windows 首次加载闪窗抑制
 └── ocr_loader.py           # 模板管理器单例
```

### 12.2 模板管理器（template_manager.py）

负责武将选择页面的模板截图的保存、加载、OpenCV 模板匹配。

```python
class TemplateManager:
    def __init__(self, template_path=None)  # 默认 templates/wujiang_select.png
    # 属性
    template_path → Path
    is_loaded → bool
    reference_size → (width, height)  # 制作模板时的截图尺寸

    # 加载
    reload()                                # 从磁盘重新加载
    set_template(image, roi)                # 从全图截取 ROI 保存为模板
    match(image, threshold=0.8) → (bool, float)  # 多尺度模板匹配
    delete_template()                       # 删除模板文件
```

**模板匹配流程**：
```
match(image, threshold=0.8)
  ├── 模板未加载 → (False, 0.0)
  ├── 输入转灰度（PIL → BGR → Gray）
  ├── 根据参考截图尺寸计算基础缩放比例
  ├── 尝试基础比例 × 0.85、0.925、1.0、1.075、1.15
  ├── 每个比例执行 cv2.matchTemplate(gray, resized_template, TM_CCOEFF_NORMED)
  ├── 选择最高置信度
  └── max_val ≥ threshold → (True, confidence)
```

模板保存时会额外写入 `templates/wujiang_select.json`，记录制作模板时的截图宽高。
旧模板没有元数据时兼容使用 2560×1440；外部替换模板会清理旧元数据，避免沿用上一份模板的参考尺寸。

**匹配算法**：`cv2.TM_CCOEFF_NORMED`（归一化相关系数匹配），输出 0~1 的置信度。

**模板制作流程**：
```
用户框选 ROI (x, y, w, h)
  ├── 验证 ROI 尺寸（w≥10 且 h≥10）
  ├── 验证 ROI 不超出画面边界
  ├── cv2.imwrite(template_path, roi_crop) → templates/wujiang_select.png
  └── 写入参考截图宽高 → templates/wujiang_select.json
```

### 12.3 武将名称识别组件

`GeneralRecognizer` 使用 PaddleOCR 对配置布局中的名称区域进行 OCR 识别；对局攻略还读取同一布局中的阵营区域。它负责 ROI 裁剪、引擎调用、多路证据汇总、候选状态和页面唯一性消歧。图像增强由 `ImagePreprocessor` 承担，单字字形安全门槛由 `CharacterSimilarityService` 承担，汉字特征缓存由 `CharacterFeatureRepository` 承担。

#### 多路证据识别策略

```
第一段：PaddleOCR 全量字典（ch）识别
  ROI 裁剪 → 放大 3× → CLAHE → 锐化 → 灰度
  → PaddleOCR → 文字 + 置信度

第二段：仅对缺失、多候选、冲突或低于 0.8 的槽位复识别
  当前增强图逐槽 OCR + 仅放大原图逐槽 OCR

第三段：候选确认
  精确命中 → exact
  严格前缀（缺字）只保留前缀候选；唯一前缀至少识别出 2 字才确认
  等长且仅错一字：唯一候选字形分 ≥ 0.55 → unique_similarity
  等长多候选：置信度 ≥ 0.7、最高字形分 ≥ 0.35、领先 ≥ 0.15，
  且 enhanced/plain 两个独立证据族支持同一结果 → multi_similarity
  同时命中长名严格前缀与等长候选 → 合并候选，length_mode=uncertain
  其他增删字、字形不足或证据不足 → 保持未确认

第四段：多路候选闭包
  所有非空候选集合取交集
  交集为空 → conflict；精确或纠正结果也不得跨候选白名单覆盖

第五段：页面约束
  仅对原候选数大于 1 且 length_mode 为 missing/complete 的未确认槽位消歧
  不提升 uncertain，也不把未过安全门槛的单候选自动提升
  重复名称按 exact > unique_prefix > unique_similarity/multi_similarity > slot_unique 回退弱证据
  同等级重复全部标记 conflict
```

结果格式为 `{index, raw_name, name, candidates, resolution, length_mode, confidence, evidence}`；只有 `name` 非空才表示名称已经确认。`length_mode` 为 `complete/missing/uncertain/unknown`。缺字结果不参与字形评分，多个“夏侯”候选不会按字典顺序决胜，`卫` 与白名单外的 `周瑜` 证据会形成 `conflict`，`正瑜` 也不会仅凭编辑距离或页面唯一性自动绑定为周瑜。名称 ROI 的卡框和底部定位字会干扰像素字符分割，因此当前不以视觉字符数作为硬门禁。势力关联仅作为后续可选证据记录：只能过滤已有候选，不能扩展候选，本次未实现。

#### 候选内单字字形评分算法（2026-06-30 新增，2026-08-01 收紧）

常规截图仅在 OCR 原文与候选等长、且恰有一个字符不同时计算该错字的加权相似度。缺字前缀和其他增删字结果只建立候选白名单，不参与评分。

**公式**：

```
if len(text) == len(candidate) and mismatch_count == 1:
    score = four_corner * 0.4 + cangjie * 0.4 + radical * 0.2
else:
    score = None
```

其中 `four_corner` 为四个有效主码的同位置匹配率；`cangjie` 为 `1 - Levenshtein / 较长仓颉码长度`；`radical` 仅在部首相同且双方笔画有效时取 `较少笔画数 / 较多笔画数`，否则为 0。

唯一候选要求 `score >= 0.55`。多候选要求参与证据的 OCR 置信度 `>= 0.7`、第一名 `score >= 0.35`、第一名领先第二名 `>= 0.15`，且 `enhanced` 与 `plain` 两个独立证据族都选出同一第一名。`batch_enhanced` 与 `single_enhanced` 属于同一证据族，不能重复计票。这样字形评分只在候选闭包内负责排序，不会将“正瑜”跨白名单改成“周瑜”。

某一维度的特征任一侧缺失时，该维度记 0 分；四角码不足四位不补零，同部首但任一侧笔画无效时部首维度也记 0 分。

#### 汉字特征数据来源

| 维度 | 来源 | 存储位置 | 加载方式 |
|------|------|---------|----------|
| 四角号码 | unihan-etl（UNIHAN `kFourCornerCode`） | `src/data/char_info_cache.json` | 首次 OCR/纠错时按需加载 |
| 仓颉码 | unihan-etl（UNIHAN `kCangjie`） | 同上 | 首次 OCR/纠错时按需加载 |
| 部首 | cnradical；与总笔画数组合评分 | 同上 | JSON 缓存 / 运行时补齐 |
| 拼音 | pypinyin | 同上 | JSON 缓存 / 纠错时按需加载 |
| 笔画数 | UNIHAN `kTotalStrokes`（从 `Unihan_IRGSources.txt` 懒加载） | `CharacterFeatureRepository` | 通过 `unihan_etl.Options().work_dir` 解析文本文件 |

数据文件 `src/data/char_info_cache.json` 包含 314 个高频汉字（武将名 + 已知 OCR 误识字）。
`CharacterFeatureRepository` 可注入缓存路径；缓存缺失的汉字在运行时由原始库动态补齐并写入进程内存，显式 `save()` 时以 UTF-8/LF 原子写入。UNIHAN 导出 CSV 已存在时直接读取，只有目标文件不存在时才执行 `Packager.export()`，避免因重复覆盖默认 AppData 文件而使动态补齐整体降级。pypinyin 预热或查询失败时记录一次 warning 并将拼音源标记为不可用，后续查询直接降级为空值；cnradical 的单字查询失败会记录字符和异常，但不禁用整个部首源。

#### 类结构

```python
class GeneralRecognizer:
    def __init__(self, rois=None, hero_names=None, reference_size=None,
                 page_type="hero_selection", preprocessor=None, similarity_service=None,
                 layout=None)
    recognize(image) → list[dict]           # 同类 ROI 拼图识别并汇总多路证据
    _resolve_name_evidence(index, evidence) → dict
    _resolve_multi_candidate_similarity(...) → str
    _resolve_page_names(results) → list[dict]
    _extract_text(ocr_result) → (str, float) # 解析 PaddleOCR 返回
    save_results(results, json_path, image_path)  # 静态方法

class ImagePreprocessor:
    preprocess_roi(roi) → np.ndarray

class CharacterSimilarityService:
    correct_hero_name(text, hero_names) → str
    single_substitution_similarity(text, candidate) → float | None
    rank_single_substitution_candidates(text, candidates) → list[tuple[str, float]]

class CharacterFeatureRepository:
    load() / get_feature(char) / save()
```

ROI 坐标由 `OcrRoiConfig` 从默认文件和本地覆盖文件加载，并以独立参考分辨率保存。`recognize()` 读取当前截图尺寸后分别计算宽高比例，
再将每个 ROI 的 `x/y/w/h` 换算到当前截图坐标，因此页面比例基本不变时可以适应不同分辨率：

```
scale_x = current_width / reference_width
scale_y = current_height / reference_height
当前 ROI = (x*scale_x, y*scale_y, w*scale_x, h*scale_y)
```

该换算发生在 PaddleOCR 之前，不改变现有的放大、CLAHE、锐化、灰度化和名称候选解析流程。

#### 识别调用链

```
GeneralRecognizer.recognize(image)
  ├── 根据 reference_size 计算 scale_x / scale_y
  ├── 换算、裁剪并预处理当前页面的同类 ROI
  ├── _recognize_prepared_batch(slots, "name")
  │    ├── _build_batch_canvas(slots)
  │    ├── _engine.ocr(canvas)
  │    └── 检测框映射回槽位，记录 batch_enhanced 证据
  ├── _resolve_name_evidence(index, evidence)
  ├── [缺失/未决/冲突/置信度 < 0.8]
  │    └── _append_single_name_evidence(...)
  │         ├── single_enhanced
  │         └── single_plain
  ├── _resolve_name_evidence(index, evidence)
  └── _resolve_page_names(results)
```

#### 关键常量

```python
_NAME_RECHECK_CONFIDENCE = 0.8
_UNIQUE_PREFIX_MIN_LENGTH = 2
_MULTI_CANDIDATE_MIN_CONFIDENCE = 0.7
_MULTI_CANDIDATE_MIN_SIMILARITY = 0.35
_MULTI_CANDIDATE_MIN_MARGIN = 0.15
_MULTI_CANDIDATE_MIN_EVIDENCE_FAMILIES = 2
CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD = 1
CharacterSimilarityService.SAFE_CHARACTER_SIMILARITY = 0.55
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
        self._ocr = create_paddle_ocr(use_angle_cls=False, lang="ch", show_log=False)
    return self._ocr
```

- `use_angle_cls=False`：不启用文字方向分类，节省推理时间
- `show_log=False`：不输出 PaddleOCR 的调试日志
- 应用启动时由唯一 `OcrWorker` 预热模型和代表性拼图推理；预热失败或未执行时，首次实际调用才承担加载成本
- Windows 首次导入期间，统一加载入口为 Paddle 的系统与 CUDA 探测短命令设置 `CREATE_NO_WINDOW`，加载完成后恢复标准 `Popen` 行为

#### 兼容纠正服务边界

常规截图主链路不调用 `correct_hero_name()` 做全局强选，而是使用前述字数门禁、候选交集和 `single_substitution_similarity()`。`CharacterSimilarityService.correct_hero_name("曹不", hero_names)` 仍供官方榜单受控兜底及兼容单槽接口使用，其流程为：

1. 遍历所有武将名，计算编辑距离
2. 找到最优匹配（距离最小的候选）
3. 距离 ≤ `EDIT_DISTANCE_THRESHOLD(1)` 时采纳
4. 多个候选 → 服务内的视觉相似度评分决胜；调用方仍需负责自己的候选白名单约束

**兼容服务评分算法**（以 `"王剪" → ["王异", "王翦"]` 为例）：

多维汉字特征评分通过逐字符比较，对相同字符加满分，不同字符用四角号码、仓颉码、部首加权评分替代：

```
四角(×0.4) + 仓颉(×0.4) + 部首(×0.2)

示例：
  剪 vs 翦: 四角0.75×0.4 + 仓颉0.75×0.4 + 部首0×0.2 = 0.600
  剪 vs 异: 四角0×0.4 + 仓颉0×0.4 + 部首0×0.2 = 0
```

### 12.4 单例加载器（ocr_loader.py）

`ocr_loader.py` 仅延迟管理配置页所需的模板管理器；识别器（`GeneralRecognizer` / PaddleOCR）由 `OcrWorker` 在 worker 线程内独占创建，不再经过本模块。

- `get_template_manager()` → `TemplateManager` 单例
- `OcrWorker` 在专用线程中按 ROI、武将列表和参考尺寸缓存 `GeneralRecognizer`

任务配置变化时 worker 会在下一项任务开始前重建识别器；所有识别任务串行执行，避免 UI 路径与轮询路径直接共享全局实例。

### 12.5 业务集成流程

#### 手动截图识别

```
用户点击选将推荐面板「识别当前阵容」或「更多 > 从图片导入」
  │
  ├── ADB 未配置？→ 弹出 MumuConfigDialog 配置
  │
  ├── CaptureService.do_capture(hero_names, force_ocr=True)   [当前模拟器画面识别]
  │   ├── AdbCapture 连接（未连接时自动连接）
  │   ├── screencap_full() → PIL Image
  │   ├── 保存 screenshots/ 下的 PNG
  │   └── capture_completed → load_from_ocr()
  │
  └── CaptureService.do_capture_from_file()                    [「从图片导入」]
       └── _queue_capture_ocr() → OcrWorker（串行）
            └── capture_completed 信号
                 └── RecommendationPanel._on_capture_result()
                      └── load_from_ocr() → 填入 8 槽
                           ├── 匹配 Hero 对象（通过 HeroManager）
                           ├── 加载 images/<name>.png 头像
                           ├── 刷新推荐指数快照并显示“推荐指数：分数 / 评级”或数据不足状态
                           └── 加载相性数据（synergies.json）+ 胜率（2v2胜率排行.csv）
                                 └── 按历史单将胜率降序排名，前三标记固定 TOP 徽章

“更多 > 保存截图”单独调用 `do_capture(perform_ocr=False)`，完成后不更新当前推荐结果。
```

#### 持续轮询识别

OcrService 提供 QTimer 驱动，PollCoordinator 编排轮询流程：

```

用户勾选「持续轮询」→ 保存配置
  │
  └─ MainWindow._open_mumu_config() → 仅记录配置
       │
       └─ ADB 连接状态变为 connected → start_poll(interval_ms)
       │
       ▼ 每隔 N 秒触发
  OcrService.poll_tick signal
       │
       ▼
  PollCoordinator._on_poll_tick()
       │
       ├── begin_poll() → due_poll_tasks()（对局攻略仅由选将命中解锁）
       ├── ADB 未配置/未连接？→ 返回前置条件结果（服务暂停或退避）
       │
       ├── ① 后台线程 screencap_full() → PIL Image（全在内存，不写磁盘）
       │
       ├── ② 每个到期页面提交 CaptureService.submit_ocr_task()
       │     └── OcrWorker._execute() → TemplateManager.match()
       │          └── 命中且需要识别 → GeneralRecognizer.recognize() → latest.json
       │
       ├── ③ PollCoordinator 接收结果并提交轮询状态
       │     ├── complete_poll(generation, outcome)
       │     ├── poll_result_ready → MainWindow._on_poll_result()
       │     ├── hero_selection 命中 → 解锁一次 match_guide → RecommendationPanel.load_from_ocr()
       │     └── match_guide 命中 → 停用任务 → MatchGuidePanel.update_block()
       │
       ├── ④ RecommendationPanel.load_from_ocr()
       │     └── 填充 8 个推荐槽位（头像/相性/胜率）
       │
       └── ⑤ hero_selection 进入计时冷却；下一次选将命中会重置 match_guide 任务和对局攻略页跳转边沿
```

**关键设计**：
- 轮询路径全程无磁盘 I/O：ADB 截图 → BytesIO → PIL Image → OpenCV ndarray → PaddleOCR，数据一直驻留内存
- 模板匹配是前置快速过滤器（<50ms），匹配成功后才执行 PaddleOCR（0.5-3 秒）
- 轮询独立于「启用 OCR 识别」复选框，勾选轮询即可独立运行
- 轮询定时器永不自杀：条件不满足时 return 等待下一次 tick

#### 模板匹配的作用

模板匹配是整个 OCR 流程的**前置过滤**。只有匹配到武将选择页面（置信度 ≥ 阈值），才会执行 PaddleOCR 识别。阈值越高匹配越严格，避免对无关画面执行 OCR。

---

## 十三、测试体系细节

### 13.1 运行与统计

测试覆盖 AI 生成、数据管理、OCR、模拟器交互、对局攻略和桌面 UI。用例数量由 pytest 收集结果作为唯一来源，不再维护容易漂移的按文件静态计数。

```bash
python -m ruff check src tests
python -m pytest --collect-only -q
python -m pytest tests/ -v
```

开发环境与 CI 统一使用 Ruff 0.12.0。当前以 `pytest --collect-only -q` 收集 **498** 项测试。定向修改默认只运行受影响测试文件；完整套件是否通过应以实际执行结果为准。

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
│  UI 层（MainWindow + AiGenerationWorkflow）                              │
│  1. MainWindow 菜单入口委托 request_guide_*()                              │
│  2. 工作流读取武将、选择后端并显示 GuideProgressDialog                     │
│  3. GuideFetchService 构建子进程参数 → QProcess.start()                   │
└──────────────────────┬───────────────────────────────────────────────────┘
                       │ 子进程 stdout → UI 进度条
                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  GuideFetchService（主进程，QProcess 管理）                                │
│  参数: python -m src.scraper.ai_batch --guide [--update] [--browser] [--no-rag] │
│  增量/指定模式 → 写入临时 JSON → --heroes-file 传入子进程                  │
│  实时读取 stdout → 正则解析 [i/N] → 更新进度条                            │
│  finished 信号 → 检查 exit_code → 弹出完成/失败提示                        │
└──────────────────────┬───────────────────────────────────────────────────┘
                       │ 启动子进程
                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  ai_batch.py（子进程入口 CLI）                                             │
│  ① load_heroes() → HeroManager 完整校验武将数据                          │
│  ② _load_existing_guides() → 校验断点；错误原件备份为 .corrupt-*         │
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
│     └── convert_ids_to_int(synergizes_with)  ← 搭配武将 ID 元素转 int   │
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
  │    │    ├── POST {model, messages, temperature, max_tokens=16384,
  │    │    │         thinking={type: disabled}}
  │    │    ├── 成功: 仅保留 content / finish_reason / usage
  │    │    ├── content 为空或 finish_reason=length: 提示思考过程耗尽输出额度
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
  "max_tokens": 16384,
  "thinking": {"type": "disabled"}
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
        "content": "以下是对诸葛亮的攻略分析：\n\n---\n\n```json\n{\n  \"hero_id\": 52,\n  \"key_points\": [\n    \"观星是诸葛亮的核心技能，可以在摸牌阶段前控制牌堆顶牌序，判定阶段前控制判定牌\",\n    \"空城状态下免疫杀和决斗，但惧怕AOE伤害\"\n  ],\n  \"weak_against_type\": [\"高爆发型\"],\n  \"strong_against_type\": [\"慢速防御型\"],\n  \"synergizes_with\": [15, 42],\n  \"counter_strategy\": \"优先打断观星后的关键回合\",\n  \"description\": \"诸葛亮是典型的控场型武将，利用观星调节牌序...\",\n  \"tips_for_beginners\": \"新手使用诸葛亮时，优先保证空城状态...\"\n}\n```\n\n### 总结\n诸葛亮在不同模式下皆有不错的出场率..."
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

API 请求显式关闭思考；即使服务端仍返回 `reasoning_content`，程序也不读取、记录、保存或展示，后续解析链路只接收最终 `content`。

| 优先级 | 策略 | 说明 | 适用场景 |
|--------|------|------|----------|
| 1 | 全文 `raw_decode` | 直接 `json.JSONDecoder().raw_decode()` 解析全文 | AI 纯 JSON 输出 |
| 2 | ```json 代码块 | 正则 ````(?:json)?\s*\n?(.*?)``` ```` | AI 用 Markdown 包裹 JSON |
| 3 | --- 分隔线后 | `rfind("\n---")` 取最后一段 | AI 先分析再输出 JSON |
| 4 | { 到 } 区间 | `find("{")` ~ `rfind("}")` 截取 | 兜底 |

每步先尝试直接解析，失败则走 `_repair_strings()` 修复（字符串值内的字面 `\n` → `\\n`），再重试。全部失败抛 `ValueError`。

---

### 14.4 浏览器方式详细流程（PlaywrightGenerator）

#### 14.4.1 调用链

```
PlaywrightGenerator.__init__()
  │
  ├── DeepSeekBrowserSession(browser_config)                  [页面会话]
  │
  ├── DeepSeekBrowserSession._ensure_browser() ← 首次发送前惰性初始化
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
  │    │    └── DeepSeekBrowserSession.send_and_wait(prompt)
  │    │         ├── page.fill(textarea, prompt)
  │    │         ├── page.keyboard.press("Enter")
  │    │         ├── Phase 1: 轮询 assistant 消息数增加（每 500ms）
  │    │         ├── Phase 2: inner_text 长度连续 3 轮不变（每 2s）
  │    │         └── 返回最后一条 assistant 的 inner_text
  │    ├── extract_json(reply) → 与 API 方式同一函数
  │    ├── convert_ids_to_int + inject hero_id
  │    ├── validate_guide(raw) → 与 API 方式同一函数
  │    └── 下一次调用前: _random_rest() → 上次成功后随机休息 60-180 秒
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
  "weak_against_type": ["高爆发型", "拆迁型"],
  "strong_against_type": ["慢速防御型"],
  "synergizes_with": [15, 42],
  "counter_strategy": "优先打断观星后的关键回合",
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
| **写入 JSON** | 每批校验成功结果原子提交 | 每批校验成功结果原子提交 |
| **Token 统计返回** | `usage` 字段（prompt/completion tokens） | `None`（不支持） |
| **断点续传** | ✅ 通过 `_load_existing_guides()` | ✅ 通过 `_load_existing_guides()` |
| **成本估算** | ✅ 支持 dry-run 显示 | ❌ 无 |
| **必备条件** | 有效的 API Key + 网络 | Edge 浏览器 + DeepSeek 已登录 |
| **风控对策** | 限速 + 指数退避重试 | 每次成功后、下一次请求前随机休息 60-180s |
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
    "weak_against_type": ["高爆发型", "拆迁型"],
    "strong_against_type": ["慢速防御型"],
    "synergizes_with": [15, 42],
    "counter_strategy": "优先打断观星后的关键回合",
    "description": "诸葛亮是典型的控场型武将...",
    "tips_for_beginners": "新手使用诸葛亮时，优先保证空城状态..."
  },
  {
    "hero_id": 1,
    "key_points": [...],
    "weak_against_type": [...],
    "strong_against_type": [...],
    "synergizes_with": [...],
    "counter_strategy": "...",
    "description": "...",
    "tips_for_beginners": "..."
  }
]
```

**写入策略**：
- 每累计 10 条攻略或相性校验成功即写入正式数据
- `json.dump()` → `tmp_path.replace(正式路径)`，单次提交保持原子性
- 任一请求失败时保留对应正式记录，已成功批次不回滚

---

### 14.7 相性评分链路的差异

攻略和相性的数据链路几乎完全一致，仅以下环节不同：

| 环节 | 攻略 | 相性 |
|------|------|------|
| CLI 参数 | `--guide` | `--synergy` / `--synergy-pair` / `--synergy-single` |
| Prompt 模板 | `docs/prompts/hero_guide.md` | `docs/prompts/synergy_score.md` |
| 循环函数 | `run_guide_generation()` | `run_synergy_generation()` / `run_synergy_pair_generation()` / `run_synergy_single_generation()` |
| 生成器方法 | `generate_guide(hero)` | `generate_synergy(hero_a, hero_b)` |
| user_prompt 构建 | `build_guide_prompt(hero)`（RAG 注入 `build_rag_context`） | `build_synergy_prompt(hero_a, hero_b)`（RAG 注入 `build_synergy_rag_context`） |
| RAG 注入范围 | 目标武将自身语料块 | 双方武将块 + 过滤后的规则/FAQ/卡牌等跨类块（机制词查询） |
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
