# 模块：业务服务层

> 对应目录：`src/business/`
> 职责：QProcess 子进程管理、服务编排、截图与 OCR 调度、官方榜单图片导入

---

## 一、模块职责

本层是 UI 层和采集层之间的**桥梁**，负责：

1. **QProcess 子进程管理** — 构建 CLI 参数、启动/监控/终止子进程、转发 stdout/stderr、清理临时文件
2. **ADB 截图业务编排** — 管理 AdbCapture 生命周期，协调截图 → 模板匹配 → OCR 的流程
3. **OCR 控制服务** — 模板管理、轮询控制、冷却管理，以及会话取消与失败退避
4. **模拟器后台操作** — 独立执行设备探测与 ADB 会话操作，避免实例枚举阻塞模板截图
5. **官方榜单导入** — 解析固定版式的 2v2 胜率/出场榜与武将放逐榜，按表格行安全覆盖 CSV
6. **推荐数据组装** — 一次读取胜率与推荐指数快照，并提供数值化的卡片排名数据
7. **元规则文档维护纯函数** — 为「知识库维护 → 元规则维护」页签提供 audit 输出解析、数据段差异解析、提案读写与疑难登记（命令执行由 UI 层 QProcess 完成）

核心设计原则：**不持有 UI 引用**，全部通过 Qt Signal 与主窗口通信。

---

## 二、文件结构

```
src/business/
├── __init__.py
├── fetching/
│   ├── base_fetch_service.py    # QProcess 生命周期、行缓冲与统一收尾
│   ├── fetch_utils.py           # QProcess 公共工具函数
│   ├── hero_fetch_service.py    # 武将采集业务
│   ├── guide_fetch_service.py   # 攻略生成业务
│   ├── synergy_fetch_service.py # 相性生成业务
│   └── synergy_reload_worker.py # 相性数据后台重载
├── emulator/
│   ├── capture_service.py       # ADB 截图与 OCR 调度
│   ├── emulator_operation_service.py # 模拟器后台操作
│   └── mumu_config_coordinator.py # MuMu 配置状态协调
├── recognition/
│   ├── ocr_service.py           # OCR 控制、模板和轮询
│   ├── ocr_worker.py            # 唯一后台识别队列
│   ├── official_data_import_service.py # 官方榜单导入
│   └── peak_select_watcher.py   # 巅峰赛（2v2）选将实时识别循环
├── analysis/
│   ├── recommendation_service.py # 推荐数据组装
│   ├── match_analysis_service.py # 对局攻略分析
│   └── peak_ban_advice.py       # 巅峰赛禁选建议双维度象限判定
├── maintenance/
│   ├── data_management_service.py # 数据清理、修复与修改事务
│   ├── combo_import_service.py    # 实战配队导入合并（幂等）
│   └── classification_suggest.py  # 武将机制分类 LLM 建议
├── rag/
│   ├── refinement_service.py      # 索引精化：块三分类扫描、LLM 建议、curated 读写/取消
│   ├── audit_service.py           # 知识库审计：AuditIssue 结构化条目与各数据源校验
│   ├── rule_doc_service.py        # 元规则 T0 文档维护纯函数（audit/差异/提案/疑难）
│   └── task_defs.py               # RAG 语料任务定义单一事实源（10 个任务）
├── common/
│   └── script_runner.py           # QProcess 异步执行 Python 脚本公共封装
├── ai_cost.py                     # AI 成本估算业务层入口
└── announcement/
    └── announcement_service.py    # 公告检查与百科 diff 服务（线程 + Qt 信号）
```

`emulator` 只向 `recognition` 依赖 OCR 服务和任务类型；各二级包的
`__init__.py` 保持轻量，根包仅继续导出原有四个公共服务。所有资源目录统一
基于 `src.config.env.PROJECT_ROOT`，不依赖源码目录层级。

---

## 三、核心逻辑

### 3.1 QProcess 服务模式

三个采集服务（Hero / Guide / Synergy）遵循完全相同设计模式：

```
QObject 子类
  ├── 多个 Signal 用于 UI 通信
  ├── fetch_*() 方法 → 构建参数 → QProcess.start()
  ├── _on_stdout_ready() → 按完整 UTF-8 行解析进度 → 发射信号
  ├── _on_finished() → 检查 exit_code → 清理临时文件 → 发射完成信号
  └── cancel() → 终止子进程
```

**信号列表：**

```
status_changed → 状态栏文字
progress_output → 白名单内的生成进度行（START / OK / FAIL / SKIP / 冷却 / RAG 降级提示）
progress_value → (current, total) 供进度条
fetch_completed → (success, message) 通知 UI
error_occurred → 错误信息
cancelled → 用户中止后通知 UI 刷新已分批提交的数据
```

**子进程通信链路：**

```
┌─────────┐   stdout(UTF-8)   ┌──────────────┐
│ 父进程   │ ←────────────── │ 子进程       │
│ (UI)    │   stderr(UTF-8)   │ (CLI 脚本)   │
│         │ ←────────────── │              │
│         │   finished(int)   │              │
│         │ ←────────────── │              │
└─────────┘                  └──────────────┘
```

所有服务使用 `SeparateChannels` 模式，分别读取 stdout 和 stderr。

AI 生成服务以子进程退出码作为成败来源：CLI 根据 `GenerationResult` 在出现失败项时返回非零；stdout 逐行写入 `scraper/ai_generation.log`，界面只接收明确的生成进度和冷却状态。“思考过程耗尽输出额度”从完整缓冲中识别后作为明确原因透传，其余失败仍显示退出码；完整 stdout/stderr 不再重复复制到业务日志。`_dispatch_stdout_line` 同时按 `[i/N] 名字 FAIL` 行收集失败项到 `failed_items`，工作流出错时据此在弹窗”查看详情”中列出失败武将/相性对清单，而非仅显示退出码。用户主动中止会标记取消状态；Windows 通过 `taskkill /T /F` 异步结束 AI Python 进程及全部 Playwright/Edge 后代，进程树清理完成后才发出 `cancelled`，避免浏览器残留占用 OCR 所需资源。其他平台仍终止当前子进程；取消引起的崩溃事件会被忽略，临时文件由 `finished` 统一收尾。

`SynergyReloadWorker` 在后台解析已分批提交的 `synergies.json`；完成后由主线程一次性替换 `SynergyManager` 的内存数据并通知界面刷新，避免取消后同步解析 JSON 阻塞窗口事件循环。

**RAG 双版本传参**：`GuideFetchService.fetch_all/incremental/specific` 与 `SynergyFetchService.fetch_pair/single` 均新增 `use_rag: bool = True` 参数（由 `BackendChooseDialog` 的「语料增强」单选传入，默认 RAG 增强）。`use_rag=False`（经典模式）时，子进程参数追加 `--no-rag`，AI CLI 侧将 `RAG_ENABLED=false`，生成链路不再注入 RAG 语料。进度行白名单（`fetch_utils.is_generation_progress_line`）同步放行 `[RAG]` 前缀，使 AI 侧的 RAG 降级提示（`[RAG] 语料不可用，本次已降级为经典模式（原因）`）能显示在进度窗口。

### 3.2 CaptureService（截图业务）

截图流程（手动截图路径）：

```
do_capture()
  └─ [adb-capture 单线程] capture_screenshot() → AdbCapture.screencap_full() → PIL Image
       └─ [GUI 线程] 保存截图到 screenshots/ → OCR 启用？ → emit capture_completed(...)
```

`CaptureService.do_capture()` 和 `do_capture_from_file()` 支持传入 `template_name` 与 `force_ocr`。对局攻略导入使用 `match_guide` 模板并强制执行 OCR，不受“启用 OCR 识别”开关影响；选将推荐保持默认的 `hero_selection` 模板流程。

`capture_screenshot()` 是不保存文件、不触发 OCR 的共享会话接口。它用于模板制作，并与连接/断开共享同一把会话锁，避免后台模板截图和前台截图同时操作同一个 `AdbCapture`。

轮询路径由 `PollCoordinator` 编排（OcrService 只控制定时、冷却与会话，不经过 `do_capture()`）：

```
OcrService.poll_tick → PollCoordinator._on_poll_tick()
  ├─ screencap_full()（内存中，不写磁盘，仅执行一次）
  ├─ hero_selection 模板 → GeneralRecognizer.recognize() → 填入推荐面板 8 槽
  └─ match_guide 模板 → 预留对局攻略结果
```

两个任务共用一个定时器、后台采集锁和截图，但分别维护 `active`、`cooldown_until`、`last_match_time` 与失败状态。武将选择成功后激活对局攻略任务；任一任务冷却时只跳过该任务，不影响另一个任务。

同步等待路径带超时保护：`CaptureService.run_ocr_if_matched()` 与 `OcrService.run_ocr()` 对 `OcrTask.completed` 做 30 秒有限等待，超时记录告警并返回空结果（`(None, False)` / `None`），配合识别器加载熔断，避免引擎异常时调用线程无限阻塞。

### 3.3 OcrService（OCR 控制）

控制模板制作、OCR 启用和轮询定时器：

```
OcrService (QObject)
  ├── template_changed → 更新 UI 状态
  ├── ocr_completed → 识别结果
  ├── poll_tick → 轮询触发信号（QTimer 驱动）
  │
  ├── create_template(image, roi, template_name) → 制作指定模板
  ├── start_poll(interval_ms)                    → 启动轮询
  ├── stop_poll()                                → 停止轮询
  ├── activate_task(name)                        → 激活指定任务
  ├── set_task_cooldown(name, seconds)           → 设置指定任务冷却
  └── due_poll_tasks()                            → 获取当前到期任务
```

模板按名称独立管理：旧的武将选择模板继续使用 `templates/wujiang_select.png`，对局攻略模板使用 `templates/match_guide/template.png`。模板缺失只影响对应任务，不会暂停另一个任务。

### 3.4 EmulatorOperationService（模拟器后台操作）

`EmulatorOperationService` 只依赖 `CaptureService` 和底层探测模块，不持有 UI。它使用两个单线程执行器：探测线程负责 ADB 路径与 MuMu 实例枚举，ADB 会话线程负责连接、设备测试和模板截图；两类任务互不排队。`probe_all_devices_with_status()` 会在 MuMuManager 异常退出时重试一次，并把失败原因与“正常但没有实例”区分开。

```
MumuConfigDialog
  -> MumuConfigCoordinator.detect_adb() / refresh_devices()
  -> EmulatorOperationService 的后台结果
  -> MumuConfigCoordinator 转发设备、连接和模板截图状态
  -> UI 渲染状态或打开 RoiSelectorDialog
  -> MumuConfigCoordinator.create_template(image, roi, template_name)
  -> OcrService.create_template(image, roi, template_name)
```

`MumuConfigCoordinator` 持有配置草稿、已探测设备和模板截图进行状态；设备刷新失败时视图保留上一次成功的列表与选择。ROI 框选和文件选择保留在 UI 线程，模板保存、运行时 ADB 配置和轮询恢复均由协调器委托服务完成；关闭对话框后协调器停止后台操作，避免迟到回调更新已销毁控件。

### 3.5 OfficialDataImportService（官方榜单导入）

该服务处理本地官方榜单图片，不依赖 ADB 或模板匹配。固定版式、横线检测、单元格切分和胜率数字模板算法位于 `src.ocr.official_board_parser`；服务接收 `OcrWorker` 注入的 PaddleOCR 引擎，并负责姓名纠错、复核记录、进度编排与 CSV 持久化。目标仍是用视觉行边界确定行，而不是按 OCR 成功数量排列，避免漏识别一个名称后其余排名整体错位。

```
OfficialDataImportDialog
  -> CaptureService.submit_official_import()
     -> OcrWorker.submit(OfficialImportTask)
        -> OfficialDataImportService.import_pages()（按已选图片顺序串行执行）
           -> official_board_parser 读取图片、检测横线并按列比例裁剪单元格
           -> 简体 PaddleOCR；名称歧义时在原候选白名单内使用繁体模型 / 胜率数字模板识别
           -> 榜单内部唯一性补全 + 名称完整性门禁
           -> 校验通过后原子覆盖 CSV；失败仅写待复核 CSV/行截图
```

**版式与输出：**

| 图片 | 表格 | CSV | 列 |
|---|---|---|---|
| 2v2 | 左侧“胜率最高” | `data/2v2胜率排行.csv` | 排名、武将、胜率 |
| 2v2 | 右侧“出场最多” | `data/2v2出场排行.csv` | 排名、武将 |
| 武将放逐 | 左 1-80 + 右 81-160 | `data/武将放逐.csv` | 排名、武将 |

低置信度、排名 OCR 不一致或胜率模板异常仍写入正式 CSV，并写入对应 `*_待复核.csv`。未确认名称、重复名称或同规模输出集合不一致属于阻断错误：服务保存复核记录和行截图，但保留原正式 CSV。

**公共接口：**

| 接口 | 参数 | 返回/信号 | 说明 |
|---|---|---|---|
| `OfficialDataImportService.import_selected()` | `{类型: 图片路径或列表}` | `list[dict]` | 空路径跳过；两个类型依次执行 |
| `OfficialDataImportService.import_pages()` | `key`, `image_paths` | `{name, pages, records, reviews, outputs}` | 合并同类有序分页，全部校验后覆盖 CSV |
| `CaptureService.official_import_progress` | `status`, `current`, `total` | 当前榜单的 OCR 工作进度 | 等待队列、胜率模板准备、逐行识别和罕见字兜底状态都会更新 |
| `CaptureService.official_import_completed` | - | `list[dict]` | 整批任务完成 |
| `CaptureService.official_import_failed` | - | `str` | 整批任务失败原因 |

**关键实现：**

```python
boundaries = official_board_parser.find_data_boundaries(
    panel, image.shape[0], layout, panel_index,
)
boundaries, repaired_ranks = official_board_parser.restore_missing_boundaries(boundaries)
for top, bottom in zip(boundaries, boundaries[1:]):
    expected_rank = len(batch["records"]) + 1
    fields = self._recognize_row(row, columns, column_breaks)
```

`boundaries` 由视觉行检测得到，因此 `expected_rank` 来自行序而非 OCR 排名。若相邻边界间距超过中位行高的 1.5 倍，服务会按常规行高补插边界，并将补插边界后的数据行写入待复核，防止单条横线漏检导致后续排名整体前移。2v2 胜率格会先向左扩展 ROI，避免截断贴近列线的首位数字；2v2 出场榜及放逐榜的排名/武将分界固定为面板宽度的 45%，避免排名数字落入武将 OCR 区域。武将格汇总原图与增强图的 OCR 候选，优先采用精确命中词表的完整姓名；两路精确结果冲突时不按置信度强选。单字结果继续按字形补识别；公共前缀再调用 `chinese_cht` 时，精确或编辑距离纠正结果必须属于简体 OCR 产生的候选白名单。仍未确认的名称在整榜完成后排除已占用候选，只有唯一剩余且无竞争时才补全。最终未知名、重复名或同规模输出集合不一致会阻止正式覆盖。该逻辑仅用于官方导入，不影响常规武将识别。胜率继续由排名格和同列小数位构建字体模板。worker 的进度包含模板准备和逐行识别，罕见字兜底只更新状态文字；2v2 与放逐图片作为整批任务在唯一 `OcrWorker` 中串行执行。

**名称降级决策顺序：**

1. 收集原图放大与增强锐化两次 OCR 的全部文本块；完整文本精确命中 `heroes.json` 词表时优先采用，不与单字的错误高置信度竞争；两路精确结果指向不同武将时转入歧义兜底。
2. 写入前，完整候选统一通过 `CharacterSimilarityService.correct_hero_name()` 的编辑距离与字形特征二次判定，不因高置信度跳过校正；发生校正时以“武将名称已由词表校正”写入待复核 CSV 和行截图。
3. 若最高候选为单字，按亮色字形切分 2-4 个字符，保留原背景、左右内容与边缘留白后逐字 OCR；拼接结果通过 `CharacterSimilarityService.correct_hero_name()` 校正后必须仍命中词表。
4. 逐字 OCR 未得到可用名称时，只有 OCR 原文在词表中唯一对应一个前缀候选才自动补全。
5. 公共前缀存在多个候选时，按需调用繁体 `chinese_cht` 模型继续确认；繁体结果只能在当前候选白名单内精确命中或唯一纠正，不能跳转到其他武将。
6. 繁体仍无法确认时保留原文，整榜结束后排除已确认名称。只剩一个未占用候选且没有其他行竞争时自动补全；否则写入待复核并阻止正式 CSV 覆盖。

该顺序能优先恢复低置信度但完整的词表候选，同时避免将“郭”“范”等多候选单字或“夏侯”“司马”等复姓公共前缀强行改为错误角色。

---

### 3.6 AnnouncementService（公告更新检查）

`AnnouncementService(QObject)` 提供手动“检查公告更新”：`check_now()` 在 `threading.Thread` 中执行 `_do_check()`，结果通过 `check_finished(object)` 信号回到 GUI 线程（沿用 PollCoordinator 的线程+信号模式）；`is_busy` 防重复点击，`cooldown_remaining` 提供 60 秒最小检查间隔，主窗口对忙碌/冷却状态弹出提示。

一次检查 = 拉公告（`fetch_latest_announcements`，API 失败回退 HTML）→ 章节标题分类（`classify_hero_related`）→ `AnnouncementManager.merge_new` 去重落盘 → 拉百科（`fetch_baike_heroes`）→ 内容哈希 diff（`build_hero_snapshot`/`diff_heroes`）→ `mark_ready_if_updated`。首次运行以本地 `heroes.json` 初始化百科快照基线、不提醒；百科/公告失败只记日志，不覆盖旧快照。

`mark_applied()` 在“更新武将数据”完成后由主窗口调用：公告置已处理并把最近一次百科哈希写回快照，使差异归零。

### 3.7 RuleDocService（元规则 T0 文档维护）

`src/business/rag/rule_doc_service.py` 面向「知识库维护 → 元规则维护」页签提供**纯函数**（不持 UI 引用、不启动进程），命令执行由 UI 层 `rule_doc_panel.py` 用 QProcess 完成：

- **audit 输出解析** — `parse_audit_output(text)` 解析 `scripts/audit_rule_doc.py` 输出的 `[ERROR]/[WARN]/[INFO]` 行与汇总行；`audit_issue_counts(issues)` 统计各级别数量，供「文档状态」页签展示。
- **数据段差异解析** — `parse_sync_diff(path)` 读取 `scripts/sync_rule_stats.py --json` 输出的差异项（段/行号/类型/旧值/新值/摘要）；`sync_json_path()` 与 `confirmed_diff_path()` 分别定位差异报告（`scripts/.sync_rule_stats_report.json`）与 B2 确认清单（`scripts/.sync_confirmed_diffs.json`）。
- **提案读写** — `list_proposals()` 扫描 `docs/archive/proposals/CP-*.json`（按编号倒序并统计待确认/已确认/已驳回）；`parse_proposal()` 读取提案；`doc_target_line()` / `doc_section_context()` / `doc_line_at()` / `doc_context_around()` 用于差异详情页现场定位文档行与上下文；`update_proposal_item()` 原位更新条目 `status/edited_text`（临时文件 + `os.replace` 原子写）。
- **疑难登记** — `load_pending()` / `add_pending()` 维护本地待办 `docs/rule_doc_pending.json`；`pending_to_proposal()` 把一条 open 疑难转成 FAQ 新增提案（`CP-日期-Pxxx.json`，`status=pending`）；`parse_doc_chapter7()` 只读解析文档第 7 章疑难登记表。

> 关键常量：`DEFAULT_DOC`（`docs/元规则整理-完整版.md`）、`PROPOSAL_DIR`（`docs/archive/proposals/`）、`PENDING_FILE`（`docs/rule_doc_pending.json`）、`RAG_EVALS_DIR`（`data/rag_evals/`）。

### 3.8 AuditService（知识库审计，2026-08 重构）

`src/business/rag/audit_service.py` 生成知识库维护工作台的结构化审计条目：

- **`AuditIssue`**（frozen dataclass）— `kind/message/severity/target_tab/target`，供 UI 渲染跳转按钮。
- **`audit_summary(root, pending_refinement=None)`** — 新增 `pending_refinement` 可选参数：UI 工作台同一轮刷新已用 `list_pending()` 算过待精化清单，传入可避免重复读语料文件；`None` 时内部读取。
- **`collect_orphan_category_keys(hero_names, classification)`** — 新增“分类表引用未知武将”反向校验（#10）：对比 `hero_classification.json` 的 `hero_categories` 键与 `heroes.json` 武将名，返回升序脏键列表（如 `'贾诩(限定)'`），对应审计条目 `orphan_category_key`。
- **`GENERIC_HERO_NAMES`** — 泛指/占位武将名常量 `{"通用", "—", "众多武将"}`，专属牌 `hero` 字段校验与 UI 共用，消除重复字符串。
- 校验覆盖：未归类武将、分类表孤儿键、专属牌未知武将/缺结算、卡牌点数花色/张数=162、装备件数/字段、索引字段待精化（排第一位）。

### 3.9 RefinementService（索引精化服务，2026-08 扩展）

`src/business/rag/refinement_service.py` 为「索引精化」对话框提供语料块视图与 curated 读写：

- **`PendingBlock`** — 扩展 `method`（`llm|manual`）与 `updated_at`（ISO 日期）字段，待精化/已精化/普通块共用同一视图。
- **`scan_blocks(corpus_dir)`** — 一次扫描卡牌/武将语料按精化状态三分类返回 `{"pending":[], "curated":[], "normal":[]}`：pending=无 curated 且任一索引字段为空；curated=有 curated（fields 以 curated 内容为权威）；normal=无 curated 且四字段全非空（构建规则已填满）。武将语料只取技能块（跳过 overview）。
- **`list_pending()` / `list_curated()` / `list_normal()`** — `list_pending` 改为 `scan_blocks()["pending"]` 的薄封装（对外行为不变），新增已精化/普通块清单。
- **`suggest_one(block, generator)`** — 公开单块 LLM 建议接口（原私有 `_suggest_one` 改名），改用 `AIBatchGenerator.complete()` 公开对话补全接口；API/解析失败返回 None。
- **`clear_curated(corpus_dir, block_id, fname)`** — 取消精化：删除块顶层 `curated` 字段并原子写回；块不存在抛 `ValueError`，本就没有 curated 返回 False。
- **`apply_curated()`** — 零改动复用：已支持对任意 block_id 覆盖写（含已有 curated 的块），重建时 `merge_curated` 保留最新成果。
- 原子写统一委托 `src.data.json_repository.atomic_write_json`（indent=1 与 build 脚本一致）。

### 3.10 巅峰赛识别循环（peak_select_watcher.py，2026-08 新增）

`PeakSelectWatcher` 独立 QObject，与标准轮询并存。标准轮询挂起/恢复由 watcher 内部自动协调。

```
Tick（每 1.5s）→ _thread_lock 非阻塞 → _do_work() 后台线程
  ├─ CaptureService.capture_for_poll() 截图
  ├─ detect_selection_cards() → None → _handle_board_absent()
  │   └─ miss_ticks++ → BOARD_EXIT_TICKS=2 后 _restore_standard_tasks()
  ├─ board_signature(cards) 量化坐标/尺寸
  │   └─ == 上次 → 牌面未变化，沿用结果
  ├─ _suspend_standard_tasks() 挂起 hero_selection/match_guide
  ├─ _recognize_board() 提交 OcrTask，15s 超时
  └─ _publish_pool() → parse_pool() → PoolSnapshot → pool_updated
```

- **PoolSnapshot**：`card_count / names / pending / stage("ban"/"pick") / overlap / banned`
- **图片导入** `recognize_image_file()`：独立 `_import_lock`，不影响循环
- **`stage` 判定**：≥12 张 = "ban" 禁选阶段；8~11 张 = "pick" 候选阶段

### 3.11 巅峰赛禁选建议（peak_ban_advice.py，2026-08 新增）

纯函数双维度象限判定，阈值按版本微调只需改常量：

| 常量 | 值 | 含义 |
|------|-----|------|
| `HOT_PICK_RANK_MAX` | 50 | 出场排名 ≤50 为热门 |
| `STRONG_WIN_RATE_MIN` | 50.0 | 胜率 ≥50% 为强势 |

- 强势 + 冷门 → `PeakBanAdvice("ban_first", "Ban 位首选", weight=1000, ...)`
- 强势 + 热门 → `PeakBanAdvice("hot_pick", "热门强将", weight=500, ...)`
- 弱势或维度缺失 → `None`（不打标签）
- `BPI = 权重 + 出场排名 − 胜率排名` 用于卡片排序

### 3.12 AI 成本估算入口（ai_cost.py，2026-08 新增）

`estimate_generation_cost(items, kind, model=None, use_rag=None)`：UI 经本模块估算成本，不直接依赖采集层；估算规则变更时 UI 无感知。攻略用 `estimate_cost`，相性用 `estimate_item_cost`（含 RAG 预算影响）。

### 3.13 实战配队导入（combo_import_service.py，2026-08 新增）

`run_import(source, heroes, output)` 幂等合并。武将名→ID 映射，未匹配项进报告；座次解析 + position 交叉校验；手工记录优先；非手工旧记录源中已不存在则移除；重复执行输出稳定。报告字段见 `module_scraper.md` 3.6 节。

### 3.14 武将分类 LLM 建议（classification_suggest.py，2026-08 新增）

`suggest_hero_categories(hero, skills_text, position, categories, generator)`：调用 LLM 从给定分类清单中选该武将符合的机制分类（可多选）。LLM 响应通过 `json_extract.extract_json()` 提取，结果只保留清单内、去重保序的分类名，失败返回 None。UI 面板仅持线程壳调用本模块。

### 3.15 脚本运行器（script_runner.py，2026-08 新增）

`ScriptRunner(QObject)` QProcess 异步执行 Python 脚本公共封装（自 ui/shared 迁入）：

- `is_running()` 防并发（同一时刻只允许一个任务）
- `output(bytes)` / `finished(int)` 信号
- `run(python, script, args, working_dir)` 启动；已有任务返回 False

业务层（RuleDocOpsService）与 UI 均可使用，仅依赖 QtCore，无 UI 控件依赖。

## 四、关键代码片段

### 4.1 QProcess 参数构建与启动

```python
def _start_process(self, args: list[str]) -> None:
    self._process = QProcess(self)
    self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
    self._process.readyReadStandardError.connect(self._on_stderr_ready)
    self._process.finished.connect(self._on_finished)
    self._process.errorOccurred.connect(self._on_error)
    self._process.start(sys.executable, args)
```

> **设计思路：** `SeparateChannels` 确保 stdout 和 stderr 不混在一起。信号连接在 start 之前绑定，避免丢失启动瞬间的事件。`sys.executable` 保证与父进程使用同一 Python 解释器。

### 4.2 stdout 行缓冲与进度正则解析

```python
def _on_stdout_ready(self) -> None:
    data = self._process.readAllStandardOutput()
    self._stdout_line_buffer.extend(data)
    while b"\n" in self._stdout_line_buffer:
        raw_line, _, remaining = self._stdout_line_buffer.partition(b"\n")
        self._stdout_line_buffer[:] = remaining
        self._on_stdout_line(raw_line.decode("utf-8", errors="replace").strip())
```

> **设计思路：** QProcess 的一次 readyRead 不等于一行输出，且 UTF-8 字符可能跨分块。基类保留未完成字节，只有读到换行后才解码并交给子类；进程结束时还会读取残余管道内容并分发行尾。取消时只调用 `kill()`，不在 GUI 线程使用 `waitForFinished()`；临时文件清理和状态通知继续由 `finished` 信号统一完成。

`fetch_utils._GENERATION_PROGRESS_PATTERN` 除了放行 `[i/N] ... START/OK/FAIL/SKIP`、`[...] 开始...` 与 `[休息] ...` 冷却行外，还放行 `^\s*\[RAG\]` 与 `^\s*\[重试\]` 前缀，分别用于展示 RAG 降级提示与 API 限流重试状态；这两类行不包含 `[i/N]`，不会影响进度条解析。`[重试]` 行由 `api_generator._call_api` 在指数退避前 `print(f"  [重试] ...，第 n/N 次，w 秒后重试")` 输出，`GuideProgressDialog` 解析后在状态栏显示"⏳ 重试中"并在详情栏标注当前进度与重试原因。

`BaseFetchService._dispatch_stdout_line` 在转发每行 stdout 的同时，用正则 `\[(\d+)/(\d+)\]\s+(.+?)\s+FAIL` 收集失败项名（武将名/相性对名）到 `_failed_items`，供工作流在出错弹窗的"查看详情"中列出失败清单。`_start_process` 为日志命名空间为 `subprocess.ai` 的 AI 子进程额外注入 `MJS_AI_CHILD=1` 环境变量，使 AI 子进程直写日志文件（见配置模块日志系统），避免父进程以 INFO 级转发 stdout 时在 root level≥WARNING 下丢失 429/length/JSON 等失败原因。

### 4.3 临时文件自动清理

```python
def _on_finished(self, exit_code: int) -> None:
    tmp_path = self._context.get("tmp_path", "")
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
        except OSError as e:
            logger.warning("清理临时文件失败 %s: %s", tmp_path, e)
```

> **设计思路：** 指定获取模式（指定采集、指定配对、选定武将）需要写入临时 JSON 文件传给子进程。正常和异常退出都要清理，避免残留文件堆积。

---

## 五、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.manager` | 子进程完成后调用 `manager.load()` 刷新数据缓存 |
| 依赖 | `src.scraper.*` | 构建 CLI 参数调用爬虫/AI 脚本 |
| 依赖 | `src.capture.adb_screen` | CaptureService 持有 AdbCapture 实例 |
| 依赖 | `src.ocr.*` | OCR 控制服务管理模板和识别器 |
| 依赖 | `src.ocr.official_board_parser` | 官方榜单图片读取、固定版式切分、横线恢复和胜率数字模板算法 |
| 依赖 | `src.ocr.character_similarity` | 官方榜单复用公开的武将词表纠错服务 |
| 依赖 | `src.data.win_rate_repository` | 胜率 CSV 覆盖后清空读取缓存 |
| 依赖 | `src.data.recommendation_index_repository` | 提供推荐指数 CSV 的手动重建接口 |
| 被调用方 | `src.ui.app.main_window` | 主窗口连接业务服务的 Signal，UI 操作触发 fetch_*() |
| 被调用方 | `src.ui.data_admin.official_data_import_dialog` | 对话框创建后台导入线程并显示结果 |
| 依赖 | `docs/元规则整理-完整版.md` | rule_doc_service 只读解析文档行/章节/第 7 章疑难表 |
| 依赖 | `docs/archive/proposals/` | 提案 JSON 读写与状态更新 |
| 依赖 | `scripts/.sync_rule_stats_report.json`、`.sync_confirmed_diffs.json` | 数据段差异报告与 B2 确认清单定位 |
| 被调用方 | `src.ui.maintenance.rule_doc_panel` | 元规则维护页签调用纯函数渲染表格与详情 |
| 依赖 | `src.data.json_repository` | refinement_service 原子写 curated 语料 |
| 依赖 | `src.scraper.ai.api_generator` | `AIBatchGenerator.complete()` 提供 LLM 精化建议 |
| 依赖 | `src.data`（heroes/hero_classification/special_cards/card_points/equip_attrs） | audit_service 校验数据源一致性 |
| 被调用方 | `src.ui.maintenance.rag_maintenance_panel` | 审计横幅与索引精化入口调用 audit_summary / list_pending |
