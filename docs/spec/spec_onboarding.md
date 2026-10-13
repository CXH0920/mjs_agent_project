# 首启向导规格（Onboarding）

> 状态：**已实施**（2026-10-13，T1–T10 全部落地；实施偏差与登记见文末「实施记录」）
> 范围：首次启动引导——运行环境自检 / 模拟器连接与分辨率体检 / 试识别，三段一页式对话框。
> 基线：v1.2.1（提交 `2081b5b`，ONNX 编码器迁移与检索诊断已落地）
> 相关：[spec_capture_ocr.md](spec_capture_ocr.md)（采集与 OCR 边界）、[spec_ui_navigation.md](spec_ui_navigation.md)（导航结构）、[spec_config.md](spec_config.md)（配置持久化）
> 修订记录见 §十三。

## 一、目标与验收口径

### 1.1 一句话目标

新机器上，从"双击"到"识别出 8 个武将"≤3 分钟；任一步失败时，用户知道下一个该点哪里。

### 1.2 用户故事

| # | 场景 | 期望 |
|---|---|---|
| US-1 | 第一次拿到工具（或换了一台电脑） | 不用读 README，跟着界面走完就能看到识别结果 |
| US-2 | 照着做但没成功 | 界面直接指出**哪一环断了**（ADB / 模拟器没开 / 分辨率不对 / 没打开选将页），并给出一个可点按钮 |
| US-3 | 只想先看看资料库 | 随时可跳过，且以后不再自动打扰 |

### 1.3 可度量验收指标

| 指标 | 目标 | 测量方式 |
|---|---|---|
| M1 首次识别耗时 | 双击 → "识别出 N 个武将" **≤180 秒**（含首次阻塞预热） | 未配置机器录屏计时 |
| M2 向导自身交互耗时 | **≤60 秒** | 同上，掐表 |
| M3 一次性通过率 | 引导完成后首次试识别成功率 **≥80%**（环境正常前提下） | 自测 5 次 |
| M4 打扰度 | 跳过后不再自动弹；已有状态文件的老用户 **0 次打扰** | 单测 + 手测 |
| M5 失败可归因 | 任一失败态都有**唯一**的下一步动作文案，且该动作在**当前构建形态下可执行** | §六 文案表评审 |

**反例（出现即判定失败）**：向导挡住主界面出不来；向导内出现"ruff 版本三处一致"这类开发者项；向导把开发者命令（`python -m ...`、`pip install ...`）当修复动作丢给打包版用户；1920×1080 被判成 FAIL；**段 2 分辨率体检**在 `screenshots/` 留下 PNG。

> 关于段 3：试识别走 `do_capture`，**每次成功调用必然落盘 1 张 PNG**（`src/business/emulator/capture_service.py:255-260`），这是既有设计而非垃圾——由启动期 14 天回收（`src/main.py:37-56`）与 doctor 的 >300MB/30 天告警（`src/scripts/doctor.py:460-468`）共同治理。只有段 2 的体检截图才应做到零落盘。

### 1.4 合规边界（写进模块 docstring）

向导只做三件事：**只读检查文件与依赖**、**通过 ADB 读屏**、**调用 OCR 识别**。

绝对不做：任何输入注入、点击游戏、进程访问、内存读取、hook、反作弊相关动作。界面文案明示"本工具只读屏幕，不操作游戏"。

### 1.5 时长预算

| 项 | 时长 | 说明 |
|---|---|---|
| 冷启动阻塞预热 | 90–120 秒 | 现状，本期**不优化**（见 §十二） |
| 段 1 运行环境检查 | 2–10 秒 | worker 内；含 OnnxEmbedder 侧车哈希自校验（实测约 0.2–0.5 秒） |
| 段 2 连接 + 分辨率体检 | 3–8 秒 | ADB 往返 |
| 段 3 试识别 | 3–10 秒（**无超时兜底**） | OCR 链路无 timeout，慢机器可能更长；模态下无害，但必须有"正在识别…"过程态 |
| **合计** | **≈110–150 秒** | 落在 M1 的 180 秒内 |

结论：向导自身只占约 15–30 秒，主要成本是预热。若实测超 180 秒，优先做"预热可见化"，**不砍向导步骤**。

## 二、现状与硬约束

### 2.1 可直接复用的接口

| 能力 | 接口 | 位置 |
|---|---|---|
| 检查项模型 | `Finding(group, name, status, detail, fix)`，状态 PASS/WARN/FAIL/SKIP | `src/scripts/doctor.py:50-57` |
| 检查编排（含单组异常隔离） | `run_checks(offline: bool)` | `src/scripts/doctor.py:618-629` |
| 运行环境检查 | `check_environment()`（依赖导入 + OCR 模型齐备） | `src/scripts/doctor.py:78-130` |
| 配置 / 白名单 / RAG 索引检查 | `check_config()` / `check_whitelist()` / `check_rag_index()` | `doctor.py:330` / `:582` / `:476` |
| 模拟器协调器（business 层，UI 可直连） | `MumuConfigCoordinator(config, capture_service, ocr_service, operation_service=None, parent=None, roi_config=None)` | `src/business/emulator/mumu_config_coordinator.py:48-56` |
| ADB 探测 / 设备刷新 / 连接 / 测试 | `detect_adb()`、`refresh_devices()`、`connect(device, selected_explicitly)->str`、`test_device(adb_path, device)->str` | 同上 `:120`、`:123`、`:126-138`、`:143-150` |
| 整屏截图（不落盘、不 OCR，正是 ROI 框选用的那张图） | `start_roi_layout_capture(page_type)->bool` → `roi_layout_screenshot_ready(page_type, image)` | 同上 `:173-184`、`:273-277` |
| 参考分辨率（经 business 取，不碰 `src.ocr`） | `roi_layout(page_type).reference_size` = `[2560,1440]` | 同上 `:194-195`；`config/ocr_rois.default.json:5` |
| 协调器清理（只关自己的线程池，不动共享 ADB 会话） | `shutdown()` | 同上 `:256-261`；`src/business/emulator/emulator_operation_service.py:116-120` |
| OCR 预热状态机 | `capture_service.ocr_warmup_state ∈ {idle,warming,ready,failed}`、`ocr_warmup_state_changed(state, detail)` | `src/business/emulator/capture_service.py:88-90` |
| 试识别（与推荐页同一个底层入口） | `do_capture(hero_names=…, template_name="hero_selection", force_ocr=False, perform_ocr=True)` → `capture_completed(dict)`，payload 键 **恰为** `{image, save_path, ocr_results, ocr_matched}` | 同上 `:129-154`、`:386-393` |
| 一键执行脚本（脚本/QProcess 编排） | `ScriptRunner(QObject)`（三个维护面板在用） | `src/business/common/script_runner.py:21` |
| frozen 下跑模块（`-m` 分发，配合 spec `collect_submodules("src.scripts")`） | `mjs_agent.exe -m <module>` | `src/main.py:279-285`；`打包发版指南.md`「踩坑 11」 |
| 首启状态文件范式 | `should_show()` / `accept()`，版本感知，损坏即按未接受 | `src/config/disclaimer_state.py:27-53` |
| 数据加载报告 | `DataFacade.last_load_report`（**仅 `issues`，无计数/时间戳**） | `src/data/facade.py:47`、`:59-66`；`src/data/issues.py:30-33` |
| 构建类型判定 | `is_full_build()`（开发态 True；frozen 读 `.full_build`） | `src/config/env.py:52-60` |
| 状态栏胶囊 | `StatusChips.set_emulator_state()` / `set_poll_state()` | `src/ui/app/status_chips.py:68-77` |
| 向导关闭后的收尾 | `poll_coordinator.sync_with_connection()` | `src/ui/app/dialog_coordinator.py:65` |

### 2.2 不可破坏的不变量

1. **唯一 OcrWorker FIFO 队列**（`docs/project_doc.md` 运行时边界）→ 试识别必须走 `CaptureService` 既有入口，**不得新建第二条截图/OCR 链路**。
2. **GUI 线程禁止长阻塞 / `waitFor*`**（`docs/retrospective.md` 避坑表）→ 所有检查、截图、OCR 必须异步。`doctor.check_devices()` 内含 `subprocess.run(timeout=10)`（`doctor.py:148-150`），**本方案不使用它**，改走协调器异步路径。
3. **向导不得阻断进入主界面**。

### 2.3 架构门禁（实施前必读）

| 门禁 | 规则 | 对本方案的影响 |
|---|---|---|
| 文件行数棘轮 | `tests/test_architecture.py:636-712` 的 `FILE_LINE_BUDGETS`，**只许收紧** | `ui/app/main_window.py:575`（现 575）、`business/emulator/capture_service.py:479`（现 479）→ **+1 行即红**；`scripts/doctor.py:675`（现 670）→ 仅 5 行余量 |
| 不在预算名单（= 无行数约束，仅受 500 tripwire 限制） | `ui/app/menu_builder.py`（103 行）、`ui/app/dialog_coordinator.py`（224 行）、`business/emulator/ocr_task_coordinator.py`（167 行）、`src/main.py`（286 行） | 本方案的主要改动落在这四个文件上，**不受棘轮限制** |
| 行数 tripwire | `LINE_TRIPWIRE = 500`（`:734`） | 新增文件全部控制在 500 行内，无需登记 |
| 类规模棘轮 | `DEFAULT_MAX_METHODS = 40`、`DEFAULT_MAX_FIELDS = 30` | 新类保持小；状态字段归组为状态对象 |
| 分层契约 | `.importlinter`：`ui` 禁止 import `src.ocr` / `src.scraper` / `src.capture`（唯一豁免 `roi_selector.py` → `src.ocr.roi_config`） | 向导不得 import `roi_config` / `adb_screen`；参考尺寸一律经 `coordinator.roi_layout()` |
| UI 数据白名单 | `tests/test_architecture.py:538` 与"UI 禁止构造数据层" | 状态文件放 **`src/config/`**（不是 `src/data/`），天然合规 |
| **UI → `src.scripts` 属新边** | importlinter 与 UI 数据白名单**都不管**这条边（已核实），合规 | 但此前 UI 从未引用 doctor。已核实 `install_crash_logger` 只在 `doctor.main()` 内调用，**导入无副作用**，可放行；须在 PR 说明里写明这是有意引入的新依赖边 |
| **`capture_service.py` 禁改** | 预算 479/479 已满 | 预热悬挂复位（§3.5）**不得**塞进 capture_service.py；落点见 §四 |

## 三、交互设计

### 3.1 形态选型：单页体检式，不做翻页向导

| 维度 | A. 单页体检式（采用） | B. 三步翻页向导 |
|---|---|---|
| 状态 | 无"当前步"状态机 | 需维护 current_step + 前后导航 + 回溯 |
| 跳过粒度 | 每段独立可跳 | 只能整段跳或逐步跳 |
| 与 doctor 语义 | 天然对齐（PASS/WARN/FAIL 清单） | 需把 Finding 翻译成"步骤成功/失败" |
| 测试成本 | 断言各行状态 | 断言步骤转移矩阵 |
| 用户感知 | 一眼看到还差什么 | 被牵着走 |
| 代码量 | ≈280 行 | ≈450 行 + 状态机 |

### 3.2 触发时机

```
免责声明 → splash → MainWindow() → start_ocr_warmup() → wait_ocr_warmup(120s) → window.show()
                                                                                      ↓
                                                            if should_show_state(): open_onboarding()
```

- 必须在 `window.show()` **之后**：预热是阻塞且长持 GIL 的（`src/main.py:253-260` 注释明说"与事件循环并行会卡住界面"），放在之前会让用户面对冻住的向导。放在之后还有个好处：`wait_ocr_warmup` 已返回，段 3 通常立即可用。
- 模态 `exec()`：既是 UX 习惯（免责声明、模拟器配置同理），也顺带消除与推荐页的并发竞争。
- 状态文件为 `config/.onboarding_state.json`，**缺失 / 版本不符 / 损坏**时触发。
- **可强制触发的测试通道**：新增环境变量 `MJS_ONBOARDING_FORCE=1`，使向导在预热**之前**也构造（仅供发版 smoke 与自测使用）。这是 §九.3 能在 22 秒窗口内验证"构造不崩 + 可跳过"的前提。

### 3.3 段 1 · 运行环境（纯只读检查）

数据来源：doctor 的**运行期子集**。

拆分为两段拼接，**CLI 输出逐字节不变**：

```
check_environment()  →  check_dev_toolchain() + check_runtime_env()
                            ↑ ruff 三处一致、environment.yml 交叉校验（开发者项，向导不显示）
                                                ↑ onnxruntime/rapidocr 可导入与版本、
                                                  v6 内置 + v4 预取模型齐备（向导只调这个）
```

- `run_checks` 增加可选形参 `run_checks(offline=False, checks=None)`，`checks=None` 时行为不变，复用其单组异常隔离。
- **实现注意（关键）**：默认值必须在**函数体内**读模块级 `CHECK_GROUPS`（`checks if checks is not None else CHECK_GROUPS`）。若写成形参默认值 `def run_checks(offline=False, checks=CHECK_GROUPS)`，`tests/test_doctor.py:93`、`:108` 两处 `monkeypatch.setattr(doctor, "CHECK_GROUPS", …)` 会**静默失效**（不再报错，也不再测到东西）。
- 向导调用集合：`check_runtime_env()` + `check_config()` + `check_whitelist()` + `check_rag_index()`。
- **数据项不重跑 `check_data()`**（它会再全量加载一次），改读主窗已有的 `DataFacade.last_load_report`。**但必须处理假阴性**：`LoadReport` 只有 `issues`（`src/data/issues.py:30-33`），且 `load_all()` 在 manager 抛异常时**不会覆盖**它（`src/data/facade.py:47`、`:59-66`），而 `MainWindow._load_data` 的 except 只弹 QMessageBox、**不留可查询标记**（`src/ui/app/main_window.py:543-549`）。→ 数据行必须做成**三态**（正常 / 有 issue / **加载失败**），失败态由主窗写入一个可读标记（如 `_data_error: str | None`）供向导读取。
- 整段放进 `QThread`（照 `src/business/rag/retrieval_diagnostic.py:59-85` 的 worker + `LIVE_WORKERS` 模式）。放 worker 的理由是**段 1 是 I/O 密集、不该占 GUI 线程**；其中 `check_rag_index()` 在 onnx 后端会构造 `OnnxEmbedder`，但该构造只做侧车读取与两次流式哈希（`src/rag/embedder.py:45-51`），实测约 0.2–0.5 秒，不是瓶颈（session 惰性，`:88-91`）。
- 渲染：每条 `[PASS/WARN/FAIL] 名称 · 详情`，`fix` 非空时渲染成按钮；SKIP 项折叠。
- **frozen 语境文案映射（必须做）**：doctor 的 `fix` 是开发者口吻（`doctor.py:104`「按 environment.yml 安装 onnxruntime==1.23.2」、`:127`「python -m src.scripts.fetch_recheck_models 预取 v4」、`:485`/`:515`「python -m src.rag.indexer / export_embedding_onnx」）。打包版用户看到这些即违反 M5 与本文件反例条款。映射规则：
  - **可一键执行**的（预取模型、重建索引、导出编码器）→ 用 `ScriptRunner` 直接跑 `-m <module>`（开发态与冻结态同一套；入口见 `src/main.py:279-285`），按钮文案用中文动作名，不暴露命令；
  - **冻结态无意义**的（`pip/conda 安装依赖`——打包版依赖随包）→ 该检查在 frozen 下应为 PASS 或 SKIP，不展示修复动作；
  - 其余保留原文但在折叠区，作为"反馈给开发者"的信息。
  - 同类漏网文案还有 `src/ocr/engine_loader.py:139` 的运行时 hint，一并纳入映射范围。

### 3.4 段 2 · 模拟器与画面（连接 + 分辨率体检）

1. **取配置**：`get_mumu_config()` 拿 `mumu_adb_path`；未配置时按钮显示「自动探测」→ `coordinator.detect_adb()`。
2. **刷设备**：`coordinator.refresh_devices()` → `devices_changed`（**信号类型是 `object`，实际载荷为 `tuple[MuMuDeviceInfo, ...]`，不是 list**，见 `mumu_config_coordinator.py:34`、`:94-97`）；0 台提示"请先启动 MuMu 模拟器"；多台**不擅自选择**（协调器硬约束 `_device_selection_error`，`:298-302`；UI 纪律见 `src/ui/configuration/mumu_device_page.py:194-246`）。→ **需要设备下拉 + "请选择运行中的实例"占位**，这是额外的 UI 成本，已计入 T5 工时。
3. **连接**：`coordinator.connect(device, selected_explicitly=True)` → `connection_finished(ok, message)`。
4. **分辨率体检**（不动 `capture_service.py`）：
   - `coordinator.start_roi_layout_capture("hero_selection")` 取整屏截图（该路径不落盘、不 OCR）；
   - `roi_layout_screenshot_ready(page_type, image)` 取 `image.width/height`（鸭子类型，无需 import PIL）；
   - 参考尺寸取 `coordinator.roi_layout("hero_selection").reference_size`；
   - 交 `layout_resolution.compare_resolution(actual, reference)` 判定。

判定规则（写死、可测）：

| 条件 | 状态 | 文案方向 |
|---|---|---|
| `actual == reference` | PASS | 画面比例与识别区域一致 |
| 高宽比一致（误差 ≤1%）且宽 ≥ 参考宽×0.5 | WARN | 将按参考尺寸等比缩放，名称框位置可能偏移（可用，识别率或下降）→ 按钮「去重新框选」 |
| 竖屏（高 > 宽）或高宽比不一致 | FAIL | 请在模拟器里把分辨率设为 2560×1440 或其等比（如 1920×1080），再重新框选 → 按钮「打开模拟器配置」 |
| 宽 < 参考宽×0.5 | FAIL | 分辨率过低，名称文字会糊 |

**关键点**：等比缩放是设计内行为（`src/ocr/recognizer.py` 的 ROI 缩放路径按参考尺寸换算），所以 1920×1080 必须判 **WARN 而非 FAIL**，否则误伤大批用户。

**协调器构造的两个注意点**：
- `__init__` 末尾会调 `sync_capture_config()`（`mumu_config_coordinator.py:86`、`:111-114`），把配置草稿推给**共享**截图会话——用同一份 `get_mumu_config()` 构造时值相同，无副作用，但不要拿它去推一份未保存的草稿。
- `operation_service=None` 时协调器自建 `EmulatorOperationService`（`:61-64`），这正是我们要的线程池隔离（`shutdown()` 只关自己）。**传参一律用关键字**：现库唯一使用方 `MumuConfigDialog` 是位置传 5 个参数（`src/ui/configuration/mumu_config_dialog.py:55-61`），跟随位置传参容易踩坑。

「去重新框选」的深链：**`window._dialogs.open_mumu_config()`**，不在向导内嵌 `RoiSelectorDialog`（那需要复刻 `mumu_recognition_page.py:260-263`、`:290-291` 的截图→pixmap→对话框管道）。**深链的交互语义要写死**：
- 该调用是**嵌套 `exec()`**；父窗口以向导自身为 parent，或先隐藏向导再开，避免 z-order 错乱；
- 配置对话框关闭后**向导重新显示并自动重跑段 2**（不做"记忆半完成状态"）；
- 若用户直接关掉向导，段 2 记为未完成，`steps.device` 不写。

### 3.5 段 3 · 试识别

1. **门禁**：**仅 `warming` 时禁用**主按钮 + "识别引擎加载中…"（先例：`src/ui/match/peak_select_panel.py:355-365`——那里也是只在 `warming` 禁用，且 ready/failed 都会撤下提示）。**`failed` 态必须可用**：`warmup_ocr_model()` 在 `failed` 会重新入队（`capture_service.py:295-298`），所以「重试」有真实效果。
2. **warming 悬挂出口（必须补，否则用户被永久锁死）**：`wait_warmup` 超时只 `logger.warning` 就返回、**不动状态机**（`src/business/emulator/ocr_task_coordinator.py:112-114`），状态会永久停在 `warming`；而 `warmup_ocr_model()` 在 `{"warming","ready"}` 直接 return（`capture_service.py:295-298`）→ **重试对悬挂态无效**。处理：超过阈值（建议 150 秒）仍 `warming` 时，显示"识别引擎初始化超时"+「重新初始化」；该动作走**状态机 owner** 的显式复位接口（新增 `ocr_task_coordinator` 上的复位方法，`167` 行、**不在行数棘轮名单**，可安全扩展），复位后再调 `warmup_ocr_model()`。同时给出「先跳过引导」出口。
3. **提示**：点击前显示"请在游戏内打开【选将】页面"。
4. **调用**：`capture_service.do_capture(hero_names=[...], force_ocr=True)`。`hero_names` **与推荐页同源**：`self._hero_mgr.list_heroes()` 的**全量武将名**（`src/ui/recommendation/recommendation_panel.py:669`），用途是编辑距离纠错，**不是**识别白名单。
5. **结果分派**（订阅 `capture_completed` / `capture_failed`）——按真实信号路径写死：

| 信号 | 判定依据 | 呈现 |
|---|---|---|
| `capture_completed` | `ocr_results` 非空 | 成功：识别到 `len(ocr_results)` 个武将 |
| `capture_completed` | `ocr_results` 空（或 None）且 `ocr_matched is False` | **模板未命中走的是这条路径，不是 `capture_failed`**（`ocr_matched = result["outcome"] == "matched"`，`capture_service.py:380-393`；推荐页同款分支见 `recommendation_panel.py:701-712`）→ WARN"请在游戏里打开【选将】再试" |
| `capture_completed` | `outcome == "retryable_ocr"`（默认值，识别引擎不可用） | FAIL"识别引擎当前不可用"→「重试」/「重新初始化」 |
| `capture_failed` | 截图 / 连接失败 | FAIL，附 message → 按 ADB 未配 / 未连接 / 截图失败分类 |
6. **过程态**：OCR 链路无 timeout，必须有"正在识别…"文案与按钮禁用，避免用户以为卡死。
7. **成功后**：写状态文件 + 成功页（"下一步：在游戏里打开选将页，状态栏会显示识别结果"）+ `window._reporter.show_event_message("首次识别成功")`。

与推荐页不冲突的依据：推荐页回调先 `_finish_capture_request()`，无在途请求返回 `None` 即 early return（`recommendation_panel.py:692-696`）→ 不会重复消费；且向导模态，用户点不到后面的面板按钮。

### 3.6 状态与持久化

`config/.onboarding_state.json`：

```json
{
  "onboarding_version": "1",
  "steps": {
    "env": "2026-10-12T10:01:03",
    "device": "2026-10-12T10:01:40",
    "recognize": "2026-10-12T10:02:22"
  },
  "skipped": false,
  "updated_at": "2026-10-12T10:02:22"
}
```

| 函数 | 语义 |
|---|---|
| `should_show_state()` | 文件缺失 / 版本不符 / 损坏 → True（损坏按未完成处理并记 warning，同 `disclaimer_state.py:32-40`） |
| `mark_step(step)` | 记录单步完成时间 |
| `mark_skipped()` | 记录跳过，此后不再自动弹 |
| `reset()` | 供「帮助 → 重看引导」使用 |

**预期管理**：`onboarding_version` 一旦升版，**所有老用户（含已跳过的）都会重新看到向导**。这是有意的产品决策（引导内容实质变化时才升版），但要在 §九 的文档里写明，避免被当成 bug。

## 四、分层与文件划分

| 文件 | 类型 | 内容 | 行数约束 |
|---|---|---|---|
| `src/config/onboarding_state.py` | 新增 | 状态读写 + 容错 + 版本常量 | ≈60 行（<500，无需登记） |
| `src/business/emulator/layout_resolution.py` | 新增 | `compare_resolution(actual, reference) -> dict{status, detail, hint}`，纯函数无 Qt | ≈45 行 |
| `src/ui/app/onboarding_dialog.py` | 新增 | `OnboardingDialog(window)`：三段渲染 + worker 接线 + 协调器接线 + 深链 | ≈300 行 |
| `src/ui/app/dialog_coordinator.py` | 改 | `open_onboarding()`：构造 → `exec()` → 关闭后 `poll_coordinator.sync_with_connection()` | +≈8 行（**不在预算名单**） |
| `src/ui/app/menu_builder.py` | 改 | 帮助菜单加「重看首启引导」 | +1 行（**不在预算名单**） |
| `src/main.py` | 改 | `show()` 后触发 + `MJS_ONBOARDING_FORCE` 测试通道 | +≈8 行（**不在预算名单**） |
| `src/business/emulator/ocr_task_coordinator.py` | 改 | 预热状态机显式复位接口（供 §3.5 悬挂出口） | +≈10 行（**不在预算名单**，167 行） |
| `src/ui/app/main_window.py` | 改 | 动作字典加 `onboarding` 键（**+1 行**）；另把 `_load_data` 异常提示（`:543-549`，7 行）抽到 dialog_coordinator，同时写入 `_data_error` 标记 | **净 −5~6 行**（575/575 已满，必须净减） |
| `src/scripts/doctor.py` | 改 | 拆 `check_runtime_env()` + `run_checks(checks=None)` | **+6~8 行，超 675 预算** |
| `tests/test_onboarding.py` | 新增 | 见 §八 | ≈220 行 |

**行数预算处置**：

1. `scripts/doctor.py`：余量仅 5 行（现 670 / 预算 675），必然超。**主出路 = 上调 `FILE_LINE_BUDGETS` 并在提交说明写明理由**（棘轮哲学允许，见 `tests/test_architecture.py:724` 的提示文案）。早先设想的"合并仅调用一次的小工具"**前提不成立**：`_env_file_lines` 有 2 个调用点（`doctor.py:68`、`:86`），`_env_keys` 也有 2 个（`:332`、`:333`），合并省不下行数。
2. `ui/app/main_window.py`：575/575 已满，本方案要求 +1 行（动作字典），必须**同 PR 净减**（抽出 `_load_data` 的异常提示即可，且该抽出处正好是 §3.3 要求的"加载失败标记"落点）。
3. 其余改动落在无预算约束的文件上（§2.3 已列），无需动棘轮。

关键代码示意（签名以文件为准）：

```python
# src/business/emulator/layout_resolution.py
def compare_resolution(actual: tuple[int, int],
                       reference: tuple[int, int]) -> dict:
    """返回 {'status': 'pass'|'warn'|'fail', 'detail': str, 'hint': str}。纯函数。"""

# src/scripts/doctor.py —— 默认值必须函数体内读全局，否则 monkeypatch 静默失效
def run_checks(offline: bool = False, checks=None) -> list[Finding]:
    selected = checks if checks is not None else CHECK_GROUPS
    ...

# src/ui/app/onboarding_dialog.py
class OnboardingDialog(QDialog):
    def __init__(self, window) -> None: ...
    # 段 1：EnvCheckWorker(QThread) → run_checks(checks=ONBOARDING_CHECKS)
    # 段 2：self._coordinator = MumuConfigCoordinator(
    #           config=get_mumu_config(),
    #           capture_service=window._capture_service,
    #           ocr_service=window._ocr_service,
    #           parent=self)                      # 一律关键字传参
    # 段 3：window._capture_service.do_capture(hero_names=..., force_ocr=True)
    def closeEvent(self, event):
        # 顺序不可颠倒：先停发起新请求（置自身关闭标记），再 shutdown
        # （shutdown 后 _submit 与 _complete 都 early return 且不发信号，
        #   见 emulator_operation_service.py:129-141）
        self._coordinator.shutdown()
        super().closeEvent(event)

# src/ui/app/dialog_coordinator.py
def open_onboarding(self) -> None:
    w = self._window
    OnboardingDialog(w).exec()
    w._poll_coordinator.sync_with_connection()   # 与 open_mumu_config 同款收尾
```

`coordinator.shutdown()` 已核实只关自己的线程池，不动共享 ADB 会话；它同时显式断开 `connection_changed` 中继，不调用会踩"Signal source has been deleted"。

## 五、段 1 检查项口径

| doctor 检查 | 向导是否显示 | 说明 |
|---|---|---|
| ruff 三处一致 / environment.yml 交叉校验 | 否 | 开发者项 |
| onnxruntime / rapidocr 可导入与版本 | 是 | 核心运行依赖；**frozen 下依赖随包，应 PASS** |
| OCR 模型（v6 内置 + v4 预取）齐备 | 是 | 直接决定识别可用性；修复动作走一键 `-m` |
| ADB 路径 / 模拟器在线 | 是（走协调器，不用 `check_devices`） | 规避 10 秒 subprocess 阻塞 |
| 私有仓 manifest / 工作区同步 | 否 | 打包版由首启部署，开发态另有 doctor |
| 关键数据 json / LoadReport | 是（读 `last_load_report`，**三态**） | 不重跑 `check_data()`；须覆盖"加载失败"假阴性 |
| RAG 索引体积 / 段目录数 / ONNX 三件套 | 是 | 精简版也已自带 RAG；体积/段目录两项的修复动作是重建索引（可一键） |
| 配置死键 | 是 | 轻量 |
| 白名单与武将库冲突重检 | 是 | 与识别质量直接相关 |
| AI 链路 / 日志 / 磁盘 / 合规 / 安全 | 否 | 与首启无关，且部分需联网 |

## 六、失败态与文案表

| 段 | 触发条件 | 状态 | 文案 / 动作 | 是否需 frozen 映射 |
|---|---|---|---|---|
| 1 | 数据加载失败（`_data_error` 非空） | FAIL | "数据文件加载失败"+ 原因；动作「查看详情」 | 否 |
| 1 | 依赖缺失 / 版本不符 | FAIL | 开发态：附 `Finding.fix`；**frozen：按 §3.3 规则 PASS 或 SKIP** | **是** |
| 1 | v4 复核模型缺失 | WARN | "复核引擎模型未预取，识别仍可用" + 「预取模型」（ScriptRunner 跑 `-m`） | **是** |
| 1 | 索引体积超限 / 多段目录 | WARN | "索引需要重建" + 「重建索引」（ScriptRunner 跑 `-m`） | **是** |
| 2 | ADB 路径未配置 | FAIL | "未找到 ADB，可自动探测" + 「自动探测」 | 否 |
| 2 | 未探测到设备 | FAIL | "请先启动 MuMu 模拟器" + 「刷新设备」 | 否 |
| 2 | 探测到多台设备 | WARN | "检测到 N 个实例，请选择运行中的那台" + 设备下拉 | 否 |
| 2 | 连接失败 | FAIL | 附协调器 message + 「重试连接」 | 否 |
| 2 | 分辨率等比不符 | WARN | "名称框位置可能偏移" + 「去重新框选」（深链，见 §3.4） | 否 |
| 2 | 竖屏 / 非等比 / 过低 | FAIL | 具体期望分辨率 + 「打开模拟器配置」（深链） | 否 |
| 2 | 截图失败 / 信号成对存在 | FAIL | 附 `roi_layout_screenshot_failed` message + 「重试」 | 否 |
| 3 | 引擎 `warming`（<150 秒） | — | "识别引擎加载中…"，主按钮禁用 | 否 |
| 3 | 引擎 `warming`（≥150 秒，悬挂） | FAIL | "识别引擎初始化超时" + 「重新初始化」（状态机复位） / 「先跳过引导」 | 否 |
| 3 | 引擎 `failed` | FAIL | "识别引擎加载失败" + 「重试」（`warmup_ocr_model` 在 failed 会重新入队） | 否 |
| 3 | 未命中选将页（`capture_completed` + `ocr_matched=False`） | WARN | "请在游戏里打开【选将】再试" + 「重试」 | 否 |
| 3 | `outcome == "retryable_ocr"` | FAIL | "识别引擎当前不可用" + 「重试」/「查看日志」 | 否 |
| 3 | 截图失败（`capture_failed`） | FAIL | 附 message（ADB 未配 / 未连接 / 截图失败分类） | 否 |

## 七、实施任务分解

| ID | 任务 | 验收 | 工时 |
|---|---|---|---|
| T1 | doctor 拆分 + `run_checks(checks=None)` + 预算上调 | CLI 输出与改前逐字节一致（**含组名、组序、fix 文案**）+ 新单测 | 0.5d |
| T2 | `onboarding_state.py` + 单测 | 缺失/损坏/版本变/跳过 4 类用例绿 | 0.5d |
| T3 | `layout_resolution.py` + 单测 | 5 类判定用例绿 | 0.5d |
| T4 | 向导骨架 + 段 1（worker、清单渲染、fix 按钮、数据三态） | offscreen：三段可控；FAIL 项按钮可点；数据失败态可见 | 1.5d |
| T5 | 段 2（探测/连接/设备下拉/截图/判定/深链回归） | 三档判定各 1 例 + 多设备 1 例 + 深链往返 1 例 | 1.5d |
| T6 | 段 3（门禁/悬挂复位/分派/过程态/成功页） | 5 类分派各 1 例（注入假 capture）+ 悬挂复位 1 例 | 1.5d |
| T7 | 接线：`open_onboarding()` + `menu_builder` 入口 + `main.py` 触发与 `MJS_ONBOARDING_FORCE` | 冷启动只弹一次；跳过不再弹；force 通道可复现 | 0.5d |
| T8 | 文档与发版（见 §九） | 未配置机器录屏 ≤180 秒 | 0.5d |
| T9 | frozen 语境文案映射 + 一键修复接线（ScriptRunner） | 打包版下段 1 不出现任何开发者命令；可一键执行的项真能执行 | 0.5d |
| T10 | `main_window._load_data` 异常提示外移 + `_data_error` 标记（顺带净减行数） | 575 预算不越线；数据失败态被向导读到 | 0.5d |

合计 8 人日（含缓冲 8–10 人日）。

## 八、测试矩阵

| 层 | 测试内容 | 方式 |
|---|---|---|
| 纯 pytest | `onboarding_state`：缺失 / 损坏 JSON / 版本不符 / `mark_step` / `mark_skipped` | `tmp_path + monkeypatch`，同 `tests/test_doctor.py` 风格 |
| 纯 pytest | `compare_resolution`：相等 / 等比(1920×1080) / 竖屏 / 过小 / 非等比 | 参数化 |
| 纯 pytest | `doctor.check_runtime_env`：模型缺 / 依赖缺 / 全齐 | monkeypatch 路径，沿用 `test_doctor.py:219-226` 手法 |
| 纯 pytest | `run_checks(checks=[...])` 只跑指定项 + 异常隔离 + **monkeypatch `CHECK_GROUPS` 仍生效** | 同 `test_doctor.py:88-94`，另加一条守护默认值语义的用例 |
| 纯 pytest | frozen 文案映射：给定 build 形态 + Finding，输出文案不含 `python -m` / `pip` | 参数化 |
| 纯 pytest | 预热悬挂：状态机复位接口在 `warming` 悬挂后可重新入队 | 假 coordinator / 假 task |
| offscreen Qt | 向导构造 / 三段渲染 / `warming` 禁用而 `failed` 可用 / 数据失败三态 / 跳过写状态 / 关闭时调 `shutdown` / `closeEvent` 时序（先停发起后 shutdown） | 沿用项目 offscreen 习惯，**不引入 pytest-qt** |
| 契约 | 架构棘轮 + import-linter 全绿 + `main_window.py` 不超 575 | `pytest tests/test_architecture.py`、`python -m importlinter` |
| 人工 | 未配置机器 ≤180 秒计时；1280×720 场景；深链往返后自动重跑段 2；冷启动预热路径 | 录屏留档 |

## 九、文档与发版改动

1. `README.md`「快速开始」改为"双击 → 跟向导走"；现有 conda / `playwright install msedge` / 模型预取三行降级为"开发者说明"。
2. `打包发版指南.md` 新增「首启向导」小节：向导会做/不会做什么、跳过键位置、状态文件位置、`onboarding_version` 升版会重新触发。
3. `release.py` 的 smoke 断言**改为可实现的形式**（原"22 秒内不因向导崩溃 + 可被跳过"在机制上不成立）：
   - `SMOKE_TIMEOUT_S = 22`（`release.py:50`），smoke 全流程为 `os.startfile → sleep(22) → tasklist 查存活 → 扫 logs 关键字 → taskkill /F`（`release.py:429-462`），**无任何 UI 交互能力**；而向导按设计在 `wait_ocr_warmup` 之后（`src/main.py:260-261`）构造，最早 90–120 秒才出现 → 22 秒窗口内向导对象根本不存在，断言空洞为真。
   - **采用方案**：发版 smoke 以 `MJS_ONBOARDING_FORCE=1` 启动 exe，使向导在预热前构造；断言"进程存活 + 日志无致命关键字 + 日志出现向导构造埋点"。若不愿引入该环境变量，则本条**降级为人工验收项**并从 smoke 删除。
4. 若 `scripts/doctor.py` 超预算：同步更新 `FILE_LINE_BUDGETS` 并写理由；若 `main_window.py` 触线：确认已净减。
5. 登记一处文档债：`doctor.py:10` 的 `--json 机器可读输出（ops.py 复用）` 为过期描述——`ops.py:85-89` 的 `cmd_health` 只透传 `--offline`，**不消费 `--json`**。不要以"ops.py 消费 JSON"为前提设计输出契约。

## 十、风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | 向导变成路障 | 永久可见「跳过」；异常一律 try/except 后关闭并记日志；`open_onboarding` 外层再包一层保护 |
| R2 | 与预热抢 GIL 导致冻住 | 只在 `show()` 之后弹；段 3 仅在非 `warming` 可用 |
| R3 | 检查逻辑分叉（向导一套 / CLI 一套） | 同一函数；以"CLI 输出逐字节不变（含组名/组序/fix 文案）"作为验收；禁止复制粘贴 |
| R4 | 非 1440p 误判 FAIL | 等比 → WARN；仅竖屏/非等比/过小判 FAIL |
| R5 | 分辨率探测引入第二条截图链路 | 复用 `start_roi_layout_capture`；明确禁止改 `capture_service.py`（预算已满） |
| R6 | 向导销毁后 `connection_changed` 中继残留导致崩溃 | `closeEvent` 必调 `coordinator.shutdown()`（它显式断开中继） |
| R7 | 与轮询/推荐页并发截图 | 模态 `exec()` + `CaptureService` 单执行器串行；关闭后 `sync_with_connection()` |
| R8 | 段 1 检查占用 GUI 线程 | 整段放 worker（理由是 I/O 密集，不是"慢"）；OnnxEmbedder 构造实测 0.2–0.5 秒 |
| R9 | 架构棘轮红灯 | §2.3 已列硬数字与免约束文件；T4–T6 每个任务结束跑一次 `test_architecture.py` |
| R10 | 用户跳过后再也不看 | 帮助菜单常驻「重看引导」（`onboarding_state.reset()`） |
| R11 | **关闭时序**：shutdown 之后发出的请求被静默吞掉（`emulator_operation_service.py:129-141` 在 shutdown 后不发信号） | `closeEvent` 顺序固定为"先置自身关闭标记停止发起 → 再 `shutdown()`"；已完成的回调允许丢弃 |
| R12 | **数据加载失败假阴性**（§3.3） | 数据行三态 + 主窗写 `_data_error` 标记 |
| R13 | **预热悬挂**把用户锁在"加载中" | 150 秒阈值 + 状态机复位接口（`ocr_task_coordinator`，不在行数棘轮名单） |
| R14 | 深链嵌套 `exec()` 造成 z-order / 状态悬空 | 见 §3.4：以向导为 parent 或先隐藏；返回后自动重跑段 2；不做半完成记忆 |

## 十一、上线验收清单（DoD）

- [ ] 未配置机器：双击 → 识别出 N 个武将 ≤180 秒（录屏留档）
- [ ] 三段各自"通过/不通过"两态渲染正确，FAIL 项都有可点动作
- [ ] **打包版段 1 不出现任何开发者命令**（`python -m` / `pip` / conda）；可一键执行的修复项真能执行
- [ ] 1920×1080 判 WARN；竖屏判 FAIL；2560×1440 判 PASS
- [ ] `warming` 时主按钮禁用；`failed` 时可用且重试有效；悬挂 ≥150 秒有出口
- [ ] 数据加载失败时数据行显示失败态（不是"健康"）
- [ ] 跳过后不再自动弹；帮助菜单可重看；`onboarding_version` 升版会重新触发（预期行为已记录）
- [ ] 向导关闭后轮询状态正常恢复；关闭时无 "Signal source has been deleted"
- [ ] `pytest tests/ -q`、`python -m importlinter`、`tests/test_architecture.py` 全绿；`main_window.py` 不超 575 行
- [ ] `python -m src.scripts.doctor` 输出与改动前逐字节一致（含组名、组序、fix 文案）
- [ ] **段 2 分辨率体检**在 `screenshots/` 零落盘（段 3 每次 1 张属预期）
- [ ] README / 打包发版指南 / release smoke 三处同步
- [ ] `onboarding_dialog.py` 模块 docstring 写明只读边界

## 十二、本期不做

1. 预热三段进度可见化 + "跳过预热先浏览资料库"——独立项，收益大于向导本身，但要改 `src/main.py:247-262` 的启动时序，风险独立。
2. 向导内嵌 ROI 框选（改为深链既有配置页）。
3. 分辨率自适应 ROI（非等比自动映射）——属识别链路改造，不是引导问题。
4. 引导内做联网检查 / API Key 配置（核心对战辅助不需要 Key）。
5. 引导完成率上报（与"不商业化、不发数据"定位冲突）。
6. smoke 拉长到 150 秒以覆盖真实向导路径（改用 `MJS_ONBOARDING_FORCE` 通道，见 §九.3）。

## 十三、修订说明

本版依代码逐条复核修订，修订项与结论如下（"原表述"均为上一版的错误或不准确之处）：

| # | 原表述 | 复核结论 | 落点 |
|---|---|---|---|
| 1 | smoke 断言"22 秒内不因向导崩溃 + 可被跳过" | **不可实现**：向导最早 90–120 秒才构造，smoke 是 22 秒纯启动观察（`release.py:50`、`:429-462`）。改为 `MJS_ONBOARDING_FORCE` 自证通道，或降级人工项 | §3.2、§九.3 |
| 2 | DoD/反例"向导运行期间 0 张垃圾 PNG" | **自相矛盾**：`do_capture` 成功即落盘 1 张（`capture_service.py:255-260`）。口径收窄到段 2，段 3 截图定性为合法产物 | §1.3、§十一 |
| 3 | 帮助菜单「重看引导」 | **成本漏记**：需 `menu_builder` +1、`main_window` +1（575/575 已满）、`dialog_coordinator` +8。已补进文件表与 T7/T10 | §四、§七 |
| 4 | doctor 省行"出路一：合并 `_env_file_lines`/`_env_keys`" | **前提错误**：两者各有 2 个调用点（`:68`/`:86`、`:332`/`:333`）。主出路改为上调预算 | §四 |
| 5 | "CLI 输出逐字节不变"的约束来源 | 原证据"ops.py 经 `--json` 消费"**不成立**（`ops.py:85-89` 只透传 `--offline`）。约束来源改为 render 输出契约（组名/组序）与 `test_doctor.py:93/108/236/281` 的断言 | §九.5、§七 T1 |
| 6 | fix 文案"附原文即可" | **打包版不可用**：文案是开发者口吻（`doctor.py:104/127/485/515`、`engine_loader.py:139`）。补 frozen 语境映射；因 frozen 支持 `-m` 分发（`main.py:279-285`），可一键执行的项改用 `ScriptRunner`，仅"安装依赖"类降级 SKIP | §3.3、§六、§七 T9 |
| 7 | 段 1 数据项"读 `last_load_report`" | **假阴性**：`LoadReport` 只有 `issues`（`issues.py:30-33`），加载异常时不覆盖（`facade.py:47`、`:59-66`），主窗 except 不留标记（`main_window.py:543-549`）。改为三态 + `_data_error` | §3.3、§五、§六、§七 T10 |
| 8 | 段 3 门禁"`!= ready` 禁用" | **先例引用错误**：`peak_select_panel.py:355-365` 是"仅 warming 禁用"，且 failed 态 `warmup_ocr_model` 会重新入队可用。改为仅 warming 禁用 | §3.5、§六 |
| 9 | 未覆盖"预热悬挂" | **必须补**：`wait_warmup` 超时不动状态机（`ocr_task_coordinator.py:112-114`），而 `warmup_ocr_model` 在 warming 是 no-op（`capture_service.py:295-298`）→ 用户被永久锁死。补 150 秒阈值 + 状态机复位接口（落点 167 行、无预算约束） | §3.5、§六、§七 T6 |
| 10 | 段 3 分派"详见 §六" | 精度不足：需写明未命中走 `capture_completed`（`ocr_matched`）而非 `capture_failed`；补 `retryable_ocr`；写明 `hero_names` 来源为全量武将名 | §3.5 |
| 11 | `devices_changed(list)` | **类型错误**：信号载荷是 `tuple`（`mumu_config_coordinator.py:34`、`:94-97`）；多设备下拉的 UI 成本未计 | §3.4、§七 T5 |
| 12 | 协调器构造与传参 | 补充：`__init__` 会 `sync_capture_config()` 推共享配置（`:86`、`:111-114`）；`MumuConfigDialog` 是位置传参 5 个（`:55-61`），本项目一律用关键字 | §3.4、§四 |
| 13 | OnnxEmbedder 耗时 | 高估：构造只做侧车哈希（`embedder.py:45-51`、`:88-91`），实测 0.2–0.5 秒。仍放 worker，理由改为 I/O 密集 | §3.3、§十 R8 |
| 14 | `closeEvent` 时序 | 补：shutdown 后 `_submit`/`_complete` 静默吞请求（`emulator_operation_service.py:129-141`），顺序必须"先停发起后 shutdown" | §四伪码、§十 R11 |
| 15 | `checks` 形参默认值 | 补实现注意：必须函数体内读全局 `CHECK_GROUPS`，否则 `test_doctor.py:93/:108` 的 monkeypatch 静默失效 | §3.3、§四伪码、§八 |
| 16 | 深链开配置对话框 | 补空白：嵌套 `exec()` 的 z-order、返回后是否重跑段 2、未完成如何记账 | §3.4、§十 R14 |
| 17 | 段 2 失败是否悬挂 | 已核实失败信号成对（`roi_layout_screenshot_failed` / `roi_layout_capture_finished`，`:44-45`、`:279-283`），不悬挂；补进 §六 | §六 |
| 18 | `onboarding_version` 升版影响 | 补预期管理：老用户（含已跳过）会重新看到向导 | §3.6、§九.2 |
| 19 | UI → `src.scripts` 依赖边 | 已核实合规（importlinter 与 UI 数据白名单均不管），且导入无副作用；登记为有意引入的新边 | §2.3 |

## 十四、实施记录（2026-10-13）

T1–T10 已全部落地，测试 1747+ 全绿、importlinter 三契约 KEPT、doctor CLI 输出与改前逐字节一致。与规格的实施偏差登记如下：

| # | 偏差 | 说明 |
|---|---|---|
| 1 | 段 3 分派"引擎不可用"的判定 | `capture_completed` payload 实际不含 `outcome` 键（`capture_service.py` 既有契约，且该文件预算 479/479 禁改），改用 `ocr_warmup_state == "failed"` 判定。局限：warmup ready 后的瞬态任务异常会落入"未命中"WARN——有重试自愈，待 capture_service 有净减空间时补 outcome 键 |
| 2 | `_data_error` 标记落点 | 原计划放 `MainWindow`，因类字段数棘轮（36>35）落 `DialogCoordinator`，与失败弹窗同源 |
| 3 | `onboarding_dialog.py` 行数 | 528→541（T9 一键修复接线 + EnvCheckWorker 闪退修复），超 500 tripwire，登记预算 542 |
| 4 | 深链返回的协调器刷新 | 实施后复核发现 `update_adb_path` 半刷新残留旧端口草稿会把共享会话倒退（多实例换端口场景），改为整只重建 |
| 5 | `devices_changed` 测试载荷 | 规格修订 #11 所述 tuple 载荷确认无误 |

M1/M2（≤180 秒录屏计时）、段 2 零落盘抽查、深链真实 z-order、打包版 smoke 实测为人工验收项，见交付报告。
