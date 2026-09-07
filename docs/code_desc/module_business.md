# 模块：业务服务层

> 对应目录：`src/business/`
> 职责：QProcess 子进程管理、服务编排、截图与 OCR 调度、官方榜单图片导入与复核、推荐/对局摘要组装
> 知识库相关服务（元规则维护、审计、索引精化、语料任务定义、分类建议）见 [`./module_rag.md`](./module_rag.md)

---

## 一、模块职责

本层是 UI 层和采集层之间的**桥梁**，负责：

1. **QProcess 子进程管理** — 构建 CLI 参数、启动/监控/终止子进程、转发 stdout/stderr、清理临时文件
2. **ADB 截图业务编排** — 管理 AdbCapture 生命周期，协调截图 → 模板匹配 → OCR 的流程，并提供选将推荐 / 巅峰赛 / 对局攻略三板块共享的单次截图入口
3. **OCR 控制服务** — 模板管理、轮询会话与退避控制、冷却管理，以及会话代数取消
4. **模拟器后台操作** — 独立执行设备探测与 ADB 会话操作，避免实例枚举阻塞模板截图
5. **官方榜单导入** — 解析固定版式的 2v2 胜率/出场榜、巅峰赛胜率/出场榜与武将放逐榜，按表格行安全覆盖 CSV；名称校验不通过时保存复核会话供人工修正后复用（不重新 OCR）
6. **推荐数据组装** — 一次读取胜率与推荐指数快照，并提供数值化的卡片排名数据
7. **卡牌图鉴服务** — 跨仓储视图组装、取值校验与追加内容写编排

元规则文档维护纯函数与 RAG 索引精化三层架构已整体迁至 [`./module_rag.md`](./module_rag.md)，本层不再承担其职责描述。

核心设计原则：**不持有 UI 引用**，全部通过 Qt Signal 与主窗口通信。

---

## 二、文件结构

```
src/business/
├── __init__.py
├── card_catalog.py                    # 卡牌图鉴：跨仓储视图、取值校验、追加写编排
├── ai_cost.py                         # AI 成本估算业务层入口
├── fetching/
│   ├── base_fetch_service.py          # QProcess 生命周期、行缓冲与统一收尾
│   ├── fetch_utils.py                 # QProcess 公共工具函数
│   ├── hero_fetch_service.py          # 武将采集业务
│   ├── guide_fetch_service.py         # 攻略生成业务
│   ├── synergy_fetch_service.py       # 相性生成业务
│   └── synergy_reload_worker.py       # 相性数据后台重载
├── emulator/
│   ├── capture_service.py             # ADB 截图与 OCR 调度
│   ├── emulator_operation_service.py  # 模拟器后台操作
│   └── mumu_config_coordinator.py     # MuMu 配置状态协调
├── recognition/
│   ├── ocr_service.py                 # OCR 控制、模板和轮询
│   ├── ocr_worker.py                  # 唯一后台识别队列
│   ├── official_data_import_service.py # 官方榜单导入（面板守卫 + 批量写回）
│   └── peak_select_watcher.py         # 巅峰赛（2v2）选将实时识别循环
├── analysis/
│   ├── recommendation_service.py      # 推荐数据组装
│   ├── match_analysis_service.py      # 对局攻略分析
│   └── peak_ban_advice.py             # 巅峰赛禁选建议双维度象限判定
├── maintenance/
│   ├── data_management_service.py     # 数据清理、修复与修改事务
│   └── combo_import_service.py        # 实战配队导入合并（幂等）
├── announcement/
│   └── announcement_service.py        # 公告检查与百科 diff 服务（线程 + Qt 信号）
└── common/
    └── script_runner.py               # QProcess 异步执行 Python 脚本公共封装
```

`emulator` 只向 `recognition` 依赖 OCR 服务和任务类型；各二级包的
`__init__.py` 保持轻量，根包仅继续导出原有公共服务。所有资源目录统一
基于 `src.config.env.PROJECT_ROOT`，不依赖源码目录层级。

RAG 相关服务（`maintenance/` 下 `rule_doc_ops_service.py`、`classification_suggest.py` 与整个 `rag/` 目录）见 [`./module_rag.md`](./module_rag.md)。

---

## 三、核心逻辑

### 3.1 QProcess 服务模式

三个采集服务（Hero / Guide / Synergy）均继承 `BaseFetchService`，由基类统一提供 `_start_process / _read_stdout / _read_stderr / _on_finished / _on_error / cancel`，子类只覆写 `_on_stdout_line / _on_process_finished / _cleanup_context / _on_process_error` 等钩子：

```
QObject 子类
  ├── 多个 Signal 用于 UI 通信
  ├── fetch_*() 方法 → 构建参数 → _start_process() → QProcess.start()
  ├── _on_stdout_ready() → _read_stdout() → 按完整 UTF-8 行分发 → 发射信号
  ├── _on_finished() → 读取残余管道 → 检查 exit_code → 清理临时文件 → 发射完成/错误信号
  └── cancel() → 置取消标记 → 终止子进程（Windows 清理整棵进程树）
```

**信号列表（基类 / 子类）：**

```
[基类] status_changed   → 状态栏文字
[基类] error_occurred   → 错误信息（失败摘要或进程错误描述）
[基类] cancelled        → 进程树清理完成后通知 UI 刷新已分批提交的数据
[Hero] fetch_completed(bool)          → 成功/失败
[Hero] progress_updated(int, int, str)→ (当前步, 总步数, 阶段文字)
[Guide/Synergy] progress_output(str)  → 白名单内的生成进度行（START / OK / FAIL / SKIP / 冷却 / RAG 降级 / 限流重试）
[Guide/Synergy] progress_value(int, int)→ (current, total) 供进度条
[Guide/Synergy] fetch_completed(bool, str)→ (success, message) 通知 UI
[Synergy] reload_finished / reload_failed(str)→ 后台重载相性数据的结果
```

三个 `fetch_*()` 方法（含 Guide/Synergy 的全部提交入口）统一返回 `bool`，表示子进程是否成功启动；忙碌等未启动场景不发任何完成信号，调用方据此避免等待一个永不到来的信号。

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

AI 生成服务以子进程退出码作为成败来源：CLI 根据 `GenerationResult` 在出现失败项时返回非零；子进程 stdout 逐行经 `subprocess.ai.stdout` logger 落盘到 `scraper/ai_generation.log`（handler 固定 DEBUG、`keep_debug=True`，即使 root level ≥ WARNING 也保留 429/length/JSON 等失败原因），界面只接收明确的生成进度与冷却状态。

**失败消息来源**（`_process_failure_message`）：优先取 CLI 结束前输出的失败摘要行 `^\s*\[错误\]\s*生成失败：(.+)$`（多行搜索，部分成功时比单个错误标记更准确），其次在完整 stdout/stderr 缓冲中识别"思考过程耗尽输出额度"并作为明确原因透传，其余失败显示"进程退出码: N"。基类在退出码非零时发射 `error_occurred(msg)`；Guide/Synergy 仅在退出码为 0 时发 `fetch_completed(True, ...)`，Hero 无论成败都发 `fetch_completed(exit_code == 0)`。完整 stdout/stderr 缓冲只用于失败原因识别，结束后立即清空，不再整体写入业务日志。

**失败项收集**：`_dispatch_stdout_line` 在转发每行 stdout 时按 `\[(\d+)/(\d+)\]\s+(.+?)\s+FAIL(?:\s|$)` 把失败项名（武将名/相性对名）追加到 `_failed_items`，对外通过 `failed_items` 属性暴露；工作流出错弹窗的"查看详情"据此列出失败清单，而非仅显示退出码。`_start_process` 每次启动前清空该列表与全部缓冲。

**进度协议**：`fetch_utils.parse_generation_event` 把 `[i/N] ... START/OK/FAIL/SKIP`、`[重试]`、`[休息]` 等 CLI 协议行解析为 `GenerationEvent`，与白名单过滤共用同一解析源。Guide 在带 `(current, total)` 的事件（start/ok/fail/skip）上推进 `progress_value`；Synergy 只在结果落定（ok/fail/skip）后推进，避免 START 行先推进度条。

用户主动中止会置 `_cancel_requested` 并标记取消状态；Windows 通过 `taskkill /PID <pid> /T /F` 异步结束 AI Python 进程及全部 Playwright/Edge 后代，`_finish_cancellation()` 以 50 ms 轮询等待清理进程结束，完成后才发出 `cancelled`，避免浏览器残留占用 OCR 所需资源。其他平台仍终止当前子进程；取消引起的崩溃事件会被忽略，临时文件与上下文释放由 `finished`/`errorOccurred` 统一收尾。

`SynergyReloadWorker` 在后台解析已分批提交的 `synergies.json`；经 `SynergyFetchService.reload_from_disk()` 触发（已有重载进行中时直接返回 False），完成后 `SynergyFetchService._on_reload_loaded()` 由主线程一次性把结果写回 `SynergyManager`（`replace_loaded_data`）并发 `reload_finished`，避免取消后同步解析 JSON 阻塞窗口事件循环。

**RAG 双版本传参**：`GuideFetchService.fetch_all/incremental/specific` 与 `SynergyFetchService.fetch_pair/single/pairs_list` 均提供 `use_rag: bool = True` 参数（由 `BackendChooseDialog` 的「语料增强」单选传入，默认 RAG 增强）。`use_rag=False`（经典模式）时，子进程参数追加 `--no-rag`，AI CLI 侧将 `RAG_ENABLED=false`，生成链路不再注入 RAG 语料。进度行白名单（`fetch_utils.is_generation_progress_line`）同步放行 `[RAG]` 与 `[重试]` 前缀：前者展示 AI 侧的 RAG 降级提示（`[RAG] 语料不可用，本次已降级为经典模式（原因）`），后者由 `api_generator._call_api` 在 429 指数退避前输出，进度窗口据此显示重试轮次与等待秒数。

**相性三种提交模式**：`fetch_pair`（两个武将配对，`--synergy-pair`）、`fetch_single`（一个武将对全体，`--synergy-single`）与 `fetch_pairs_list`（实战配队清单的显式 `hero_a_id/hero_b_id` 配对列表，`--synergy-list`）共用 `_submit()`：`_is_busy()` 拦截 → 写临时 JSON → 拼 CLI 参数（`--no-rag` / `--update` / `--browser`）→ `_start_process()`。`overwrite=True` 追加 `--update`，与 Guide 的增量/指定模式一致。

### 3.2 CaptureService（截图业务）

`CaptureService` 是选将推荐 / 巅峰赛 / 对局攻略三板块共享的截图会话与 OCR 队列入口。内部持有两把互不嵌套的锁与两个单线程执行器：`_session_lock` 只保护会话对象与状态字段的快速读写，`_adb_io_lock` 单独串行化 `connect()` / `screencap_full()` 这类秒级阻塞 IO（超时重试最坏约 45 秒）；`_adb_executor`（`adb-capture`）串行执行 ADB 截图，`_image_save_executor`（`image-save`）并行保存 PNG，两者互不等待。

截图流程（手动截图路径）：

```
do_capture(hero_names, template_name="hero_selection", force_ocr=False, perform_ocr=True)
  └─ [_adb_executor 单线程] capture_screenshot()
       ├─ [_session_lock] 取会话；未连接则 _connect_capture()
       │   └─ [_adb_io_lock] AdbCapture.connect()
       ├─ [_adb_io_lock] AdbCapture.screencap_full() → PIL Image（不写磁盘、不触发 OCR）
       └─ future.add_done_callback → _capture_ready 信号
            └─ [GUI 线程] _on_background_capture_ready() → _handle_capture_result()
                 ├─ should_ocr = perform_ocr 且（force_ocr 或 启用 OCR 或 轮询模式）
                 ├─ [轮询且未过冷却] 跳过 OCR
                 ├─ _queue_capture_ocr() → OcrTask 入唯一队列（结果经 _on_ocr_task_completed 回来）
                 └─ _schedule_image_save() → [_image_save_executor] save_image() → image_saved 信号
```

`do_capture()` 和 `do_capture_from_file()` 支持传入 `template_name` 与 `force_ocr`。对局攻略导入使用 `match_guide` 模板并强制执行 OCR，不受"启用 OCR 识别"开关影响；选将推荐保持默认的 `hero_selection` 模板流程。`perform_ocr=False` 时直接发 `capture_completed` 并附带保存结果。模板匹配阈值按模板名分键：`mumu_match_guide_threshold` 与 `mumu_hero_selection_threshold`，缺省回落到 `mumu_ocr_match_threshold`（0.8）。

`capture_screenshot()` 是不保存文件、不触发 OCR 的共享会话接口，供模板制作与轮询复用。连接期间配置可能已变更并重建 `AdbCapture`，因此连接后会重新比对引用，失效时返回"ADB 配置已变更，请重试"。

**三板块共享一次截图的轮询路径**由 UI 层 `PollCoordinator` 编排（OcrService 只控制定时、冷却与会话，不经过 `do_capture()`）：

```
OcrService.poll_tick → PollCoordinator._on_poll_tick()
  ├─ begin_poll() → 取本轮会话代数；取 due_poll_tasks()（按各自冷却过滤）
  ├─ [_poll_thread_lock 非阻塞] 后台线程 do_poll_work()
  │   ├─ CaptureService.capture_for_poll(capture) → [_adb_executor] 单次 screencap_full()（内存中，不写磁盘）
  │   └─ 每个到期任务 CaptureService.submit_ocr_task(image, template_name, allow_result_reuse=True)
  │       └─ OcrTask.completed 有限等待 10 秒（PollCoordinator.POLL_OCR_WAIT_TIMEOUT_SECONDS）
  ├─ _poll_result_received → GUI 线程 _consume_poll_result()（代数/capture 双重校验后 complete_poll）
  └─ poll_result_ready → MainWindow._on_poll_result() 分发到选将推荐面板 / 对局攻略面板
```

轮询提速的实际实现：定时器改为**常驻重复模式**，周期由 `OcrService.complete_poll()` 动态调整（健康时 `max(间隔, 单拍处理耗时)`，失败时进入退避），不再用"单次触发 + 重新排程"。每拍只截一张图，`hero_selection` 与 `match_guide` 两个模板任务串行复用同一张图；`capture_for_poll()` 统一经 `_adb_executor` 排队，与手动截图、模板截图互斥。

**页面指纹去重**：标准轮询提交时置 `allow_result_reuse=True`，`OcrWorker` 按名条 ROI 生成 16×16 灰度块指纹，页面未变化时复用上次 OCR 结果（返回 `matched_reused` + 条目级拷贝）。指纹 ROI 与 recognizer 同规则按参考尺寸比例缩放以适配非参考分辨率；缓存键包含 `hero_names`，武将数据重载后强制失效。手动识别与巅峰赛动态 ROI 路径不启用指纹。

`submit_ocr_task()` 的参数集为 `(image, hero_names, template_name, recognize=True, rois=None, match_template=True, fallback_on_template_miss=False, allow_result_reuse=False)`；`fallback_on_template_miss=True` 用于对局攻略任务，模板未命中仍继续 OCR 以保留跳转判断素材。轮询 OCR 命中一次页面后 `CaptureService` 记 180 秒冷却（`POLL_MATCH_COOLDOWN_SECONDS`），窗口期内的轮询 OCR 被跳过；`OcrService.POLL_MATCH_COOLDOWN_MS` 与之同值但作用于任务级冷却，两者语义不同。

**OCR 预热**：`warmup_ocr_model()` 把模型加载、特征预热与推理预热作为特殊 `OcrTask` 投入同一串行队列（状态 `idle/warming/ready/failed`，经 `ocr_warmup_state_changed` 广播）；`wait_ocr_warmup(timeout_ms=15_000)` 供主窗口显示前的启动画面阶段阻塞等待——Paddle 初始化期间长时间持有 GIL，若在事件循环运行后再预热会卡住界面。

同步等待路径带超时保护：`OcrService.run_ocr()` 对 `OcrTask.completed` 做 30 秒有限等待，超时记录告警并返回 `None`，配合识别器加载熔断，避免引擎异常时调用线程无限阻塞。`shutdown()` 停止两个执行器并把 OCR worker 转入退役列表（不在 GUI 线程同步等待），进程退出钩子统一收尾。

### 3.3 OcrService（OCR 控制）

控制模板制作、轮询会话与退避状态；不持有任何截图或识别器，识别工作全部经注入的 `set_ocr_task_submitter()` 交给 `CaptureService.submit_ocr_task()`：

```
OcrService (QObject)
  ├── status_changed          → 模板/轮询状态文字
  ├── template_changed(bool)  → 模板加载或已删除
  ├── ocr_completed(list)     → 识别结果
  ├── poll_tick()             → 轮询触发信号（常驻重复 QTimer 驱动）
  ├── poll_state_changed(str, str) → (状态, 详情)
  │
  ├── create_template(image, roi, template_name) → 制作指定模板
  ├── select_template(file_path, template_name)  → 从文件加载指定模板
  ├── delete_template(template_name)             → 删除指定模板
  ├── template_path(name) / is_template_loaded(name)
  ├── start_poll(interval_ms)                    → 启动/重启轮询会话
  ├── stop_poll()                                → 停止轮询会话
  ├── resume_poll()                              → 用户主动恢复已暂停轮询
  ├── begin_poll() -> int | None                 → 标记一轮开始，返回会话代数
  ├── complete_poll(generation, outcome, detail) → 主线程记录一轮结果并迁移状态
  ├── invalidate_inflight_poll()                 → 作废在途轮询（巅峰赛启动时调用）
  ├── is_poll_cancelled(generation)              → 代数过期或取消标记已置位
  ├── activate_task(name) / deactivate_task(name)
  ├── set_task_cooldown(name, seconds=None) / clear_task_cooldown(name)
  ├── due_poll_tasks() -> list[str]              → 获取当前到期任务
  ├── get_task_state(name) -> PollTaskState      → 供调度与测试读取
  └── run_ocr(image, rois=None)                   → 兼容外部同步调用（30 秒有限等待）
```

**轮询会话模型**：`PollSession(generation, cancel_event)` 提供会话代数——`start_poll()` / `stop_poll()` / `invalidate_inflight_poll()` 各递增一次代数并置位旧取消标记。在途轮询的后台线程在每个关键写入点检查取消标记，结果回 GUI 线程时再经代数校验，过期结果直接丢弃，避免"停止后又被旧拍重新挂起"或旧结果回写冷却。`_poll_in_flight` 单飞标记保证同一时刻只有一拍在执行；`invalidate_inflight_poll()` 必须同时复位该标记，否则在途一轮不再调用 `complete_poll()` 会让后续轮询永久假死。

**状态机**：`stopped / running / backing_off / paused`，由 `complete_poll()` 依 outcome 迁移——

- `matched` / `healthy_no_match`：失败计数归零，若定时器间隔已被拉长则恢复基础间隔，状态回 `running`；
- `prerequisite_unconfigured` / `prerequisite_template_missing`：停止定时器并转 `paused`（提示未配置 ADB / 未加载识别模板）；
- 其他可重试失败（连接、截图、OCR、超时）：失败计数 +1，连续达到 `POLL_MAX_FAILURES = 5` 转 `paused`，否则把定时器间隔动态拉长为 `max(基础间隔, POLL_BACKOFF_DELAYS_MS[计数-1])`（`2s / 5s / 15s / 30s`）并转 `backing_off`，提示剩余重试次数。定时器持续运行（Qt 对激活中的定时器改间隔会重启计数），因此不再需要"单次触发 + 重新排程"。

`start_poll()` 把间隔钳到 `max(interval_ms, 1000)`，并把任务状态重置为 `hero_selection` 激活、`match_guide` 停用；两个任务各自维护 `PollTaskState(active, cooldown_until, last_match_time, consecutive_failures)`，冷却彼此独立，任一任务冷却只跳过自己。任务级冷却缺省取 `POLL_MATCH_COOLDOWN_MS = 180_000`（3 分钟）。

模板按名称独立管理：武将选择模板继续使用 `templates/wujiang_select.png`，对局攻略模板使用 `templates/match_guide/template.png`。模板缺失时对应任务返回 `template_missing`，由消费端停用该任务，不影响另一个任务。

### 3.4 EmulatorOperationService（模拟器后台操作）

`EmulatorOperationService` 只依赖 `CaptureService` 和底层探测模块，不持有 UI。它使用两个单线程执行器：探测线程负责 ADB 路径与 MuMu 实例枚举，ADB 会话线程负责连接、设备测试和模板截图；两类任务互不排队。`probe_all_devices_with_status()` 会在 MuMuManager 异常退出时重试一次，并把失败原因与"正常但没有实例"区分开。

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
           -> 面板守卫 + 榜单内部唯一性补全 + 跨榜单交集补全 + 名称完整性门禁
           -> 校验通过：逐榜单写待复核 CSV → 原子覆盖正式 CSV → 清理推荐指数/胜率缓存
           -> 校验不通过：写待复核 CSV/行截图 → 保存复核会话 JSON（不重新 OCR）→ 抛 ValueError
```

**版式与输出：**

| 类型 key | 图片 | 表格 | CSV | 列 |
|---|---|---|---|---|
| `2v2` | 2v2 | 左侧"胜率最高" | `data/2v2胜率排行.csv` | 排名、武将、胜率 |
| `2v2` | 2v2 | 右侧"出场最多" | `data/2v2出场排行.csv` | 排名、武将 |
| `peak` | 巅峰赛 | 左侧胜率榜 | `data/巅峰赛胜率排行.csv` | 排名、武将、胜率 |
| `peak` | 巅峰赛 | 右侧出场榜 | `data/巅峰赛出场排行.csv` | 排名、武将 |
| `exile` | 武将放逐 | 左 1-80 + 右 81-160 | `data/武将放逐.csv` | 排名、武将 |

同一次导入不能混用旧版长图与新版分页图片（`variant` 唯一）；`2v2` / `peak` 必须检出左右两个榜单且左右行数一致，`exile` 走 `validate_exile_row_counts()` 校验；同一张图片被重复选择会直接报错。

低置信度、排名 OCR 不一致、胜率模板异常或名称被词表校正仍写入正式 CSV，并写入对应 `*_待复核.csv`（含行截图路径、来源图片、页序号与原图坐标）。未确认名称、重复名称或同规模输出集合不一致属于阻断错误：服务保存复核记录和行截图，但**保留原正式 CSV** 并抛 `ValueError`，同时把整批结果持久化到 `data/official_import_pending.json` 供复核界面修正后直接落盘。

**复核会话（`official_import_pending.json`）**：`_save_pending_session()` 在名称校验失败时写入 `{key, image_paths, page_count, variant, validation_errors, outputs}`（`outputs` 含每个榜单的 `records` 与 `reviews`）。复核界面经 `review_candidates(ocr_name, current)` 取候选（当前值 ∪ OCR 原文 ∪ 距离 ≤ `CANDIDATE_EXPANSION_EDIT_DISTANCE=2` 且共享字符的武将 ∪ 歧义候选，为空时全表按距离排序），修正后调用 `apply_reviewed_records(pending, {(榜单, 排名): 武将名})` 重跑名称门禁——通过则写正式 CSV 并清理会话，不通过则抛错且不写任何文件。`load_pending_session()` / `clear_pending_session()` 为模块级函数，损坏或不存在时返回 `None` / 静默跳过。

**面板守卫（`_validate_panel_rank_sequence`）**：在排名 OCR 提供足够一致证据时阻止错序页面覆盖数据。收集每个面板的 `rank_offsets`（`OCR排名 − 行序`），有效偏移不足 3 个时不判定（避免短面板误伤）；否则要求最频繁偏移出现次数 ≥ `max(3, round(len × 0.6))` 且与期望起始位（`期望起始排名 − 1`）不符才抛错并拒绝导入，防止用户上传了页序混乱的图片却无感知覆盖历史数据。

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

`boundaries` 由视觉行检测得到，因此 `expected_rank` 来自行序而非 OCR 排名。若相邻边界间距超过中位行高度的 1.5 倍，服务会按常规行高补插边界，并将补插边界后的数据行写入待复核，防止单条横线漏检导致后续排名整体前移。2v2/巅峰赛 胜率格会先向左扩展 ROI，避免截断贴近列线的首位数字；出场榜及放逐榜的排名/武将分界固定为面板宽度的 45%，避免排名数字落入武将 OCR 区域。武将格汇总原图与增强图的 OCR 候选，优先采用精确命中词表的完整姓名；两路精确结果冲突时不按置信度强选。单字结果继续按字形补识别；公共前缀再调用 `chinese_cht` 时，精确或编辑距离纠正结果必须属于简体 OCR 产生的候选白名单。仍未确认的名称在整榜完成后先做**榜单内部唯一性补全**（排除已占用候选，只有唯一剩余且无竞争时才补全），再做**跨榜单交集补全**（`_resolve_names_across_outputs`：各未确认行的扩展候选集交集恰为 1 时统一改名，避免同一名将因左右榜 OCR 差异被误判为"集合不一致"）。最终未知名、重复名或同规模输出集合不一致会阻止正式覆盖。该逻辑仅用于官方导入，不影响常规武将识别。胜率继续由排名格和同列小数位构建字体模板。

**复核阈值**：名称 OCR 置信度 < `NAME_CONFIDENCE_REVIEW_THRESHOLD = 0.75` 标记"武将名称置信度低"；胜率数字模板置信度 < `TEMPLATE_RATE_REVIEW_THRESHOLD = 0.90` 且与 OCR 胜率不一致时标记"胜率OCR与数字模板不一致"（两者语义不同，勿合并）；候选池扩展编辑距离 `CANDIDATE_EXPANSION_EDIT_DISTANCE = 2`，宽松于名称纠错的 1——此处是"扩大候选集供后续复核"，不是直接纠错。

**进度与收尾**：worker 的进度先报 `total_steps`（每行 `2` 步当该列含"胜率"——模板准备 + 行识别，否则 `1` 步），罕见字兜底只更新状态文字（`current = -1` 时仅改状态）。全部写入用 `NamedTemporaryFile` + `replace()` 原子覆盖。正式 CSV 落盘后清理缓存：本批未包含 `巅峰赛胜率排行.csv` 时调 `mark_recommendation_index_stale(True)` 标记推荐指数待重建（对话框据此发 `recommendation_indexes_stale` 到推荐面板）；写入 `2v2胜率排行.csv` / `巅峰赛胜率排行.csv` 时分别调 `clear_win_rate_cache()` / `clear_peak_win_rate_cache()`。2v2、巅峰赛与放逐图片作为整批任务在唯一 `OcrWorker` 中串行执行，`import_pages()` 返回 `{name, pages, variant, records, reviews, outputs}`。

**名称降级决策顺序：**

1. 收集原图放大与增强锐化两次 OCR 的全部文本块；完整文本精确命中 `heroes.json` 词表时优先采用，不与单字的错误高置信度竞争；两路精确结果指向不同武将时转入歧义兜底。
2. 写入前，完整候选统一通过 `CharacterSimilarityService.correct_hero_name()` 的编辑距离与字形特征二次判定，不因高置信度跳过校正；发生校正时以"武将名称已由词表校正"写入待复核 CSV 和行截图。
3. 若最高候选为单字，按亮色字形切分 2-4 个字符，保留原背景、左右内容与边缘留白后逐字 OCR；拼接结果通过 `CharacterSimilarityService.correct_hero_name()` 校正后必须仍命中词表。
4. 逐字 OCR 未得到可用名称时，只有 OCR 原文在词表中唯一对应一个前缀候选才自动补全。
5. 公共前缀存在多个候选时，按需调用繁体 `chinese_cht` 模型继续确认；繁体结果只能在当前候选白名单内精确命中或唯一纠正，不能跳转到其他武将。
6. 繁体仍无法确认时保留原文，整榜结束后排除已确认名称。只剩一个未占用候选且没有其他行竞争时自动补全；否则进入跨榜单交集补全，仍不唯一则写入待复核、保存复核会话并阻止正式 CSV 覆盖。

**公共接口：**

| 接口 | 参数 | 返回/信号 | 说明 |
|---|---|---|---|
| `OfficialDataImportService.import_selected()` | `{类型: 图片路径或列表}` | `list[dict]` | 空路径跳过；多个类型依次执行 |
| `OfficialDataImportService.import_pages()` | `key`, `image_paths`, `progress_callback`, `status_callback` | `{name, pages, variant, records, reviews, outputs}` | 合并同类有序分页，全部校验后覆盖 CSV |
| `OfficialDataImportService.import_file()` | `key`, `image_path`, `progress_callback`, `status_callback` | `dict` | 单页快捷入口，委托 `import_pages` |
| `OfficialDataImportService.apply_reviewed_records()` | `pending`, `{(榜单, 排名): 武将名}` | `dict` | 人工复核修正后重跑门禁并写正式 CSV；失败抛错且不写文件 |
| `OfficialDataImportService.review_candidates()` | `ocr_name`, `current=None` | `list[str]` | 复核界面候选名（当前值 ∪ 距离 ≤2 ∪ 歧义候选，空则全表按距离排序） |
| `OfficialDataImportService.is_known_hero_name()` | `name` | `bool` | 复核界面统计用 |
| `load_pending_session()` / `clear_pending_session()` | `path=None` | `dict \| None` | 模块级读写 `data/official_import_pending.json` |
| `CaptureService.submit_official_import()` | `{类型: 路径列表}` | `OfficialImportTask` | 空选择 / 任务重叠时抛 `ValueError` / `RuntimeError` |
| `CaptureService.official_import_progress` | `status`, `current`, `total` | 当前榜单的 OCR 工作进度 | 等待队列、胜率模板准备、逐行识别和罕见字兜底状态都会更新；`current < 0` 仅更新状态文字 |
| `CaptureService.official_import_completed` | - | `list[dict]` | 整批任务完成 |
| `CaptureService.official_import_failed` | - | `str` | 整批任务失败原因 |

该顺序能优先恢复低置信度但完整的词表候选，同时避免将"郭""范"等多候选单字或"夏侯""司马"等复姓公共前缀强行改为错误角色。

---

### 3.6 AnnouncementService（公告更新检查）

`AnnouncementService(QObject)` 提供手动"检查公告更新"：`check_now()` 在 `threading.Thread`（`announcement-check`）中执行 `_do_check()`，结果通过内部信号 `_check_done(object)` 回到 GUI 线程，再由 `_finalize_check()` 统一写共享状态、持久化快照并对外广播 `check_finished(object)`（避免 worker 线程与 `mark_applied()` 跨线程竞争及快照文件并发写碰撞）；`is_busy` 防重复点击，`cooldown_remaining` 提供 `CHECK_COOLDOWN_SECONDS = 60` 秒最小检查间隔，主窗口对忙碌/冷却状态弹出提示。`check_now()` 与 `prepare_update_candidates()` 均返回 `bool`——`False` 表示被忙碌或冷却拦截，由调用方提示用户；后台阶段文字经 `progress_changed(str)` 广播。

一次检查 = 拉公告（`fetch_latest_announcements`）→ 章节标题分类（`classify_hero_related`）→ `AnnouncementManager.merge_new` 去重落盘 → 拉百科（`fetch_baike_heroes`）→ 内容哈希 diff（`build_hero_snapshot`/`diff_heroes`）→ `mark_ready_if_updated` → `_sync_timeline` 武将变更时间轴同步。公告或百科拉取异常只写入 `result.error` / `baike_ok=False` 并记日志，不覆盖旧快照、不中断应用。

**阶段令牌（`_last_snapshot` / `pending_saves`）**：worker 线程计算的百科快照不直接写盘，而是通过 `AnnouncementCheckResult.snapshot` / `pending_saves` 字段带回到 `_finalize_check()`，由 GUI 线程统一持久化并更新 `_last_snapshot`。`mark_applied()` 在"更新武将数据"完成后由主窗口调用：公告置已处理，并把 `_last_snapshot` 写回快照使差异归零。

**首次基线本地化**：首次启用百科基线时优先用本地 `heroes.json` 初始化，避免官网快照被用作基线从而掩盖本地缺失（否则 diff 恒空、新增/调整永远不会提示）。若本地数据文件存在但加载为空则不写快照并记录警告；全新安装（无任何本地武将数据文件）则以当前百科为基线不提醒。

**`collect_base_candidates()` / `prepare_update_candidates()`**：提供"更新候选准备"接口，两者都由调用方在 GUI 线程传入 `local_heroes_plain` / `announcements` / `diff` 只读快照，后台线程不再访问主窗口可变状态。`collect_base_candidates()` 纯内存、无网络，供 UI 预判是否有可更新项；`prepare_update_candidates()` 在独立 `_prepare_thread`（`announcement-prepare-update`）中拉取官网百科并计算字段级差异候选，完成后发 `update_candidates_prepared({"candidates", "official_ok", "error"?)}`——异常被兜底为 `official_ok=False` + `error`，避免 UI 进度条永不消失。两者与 `check_now()` 共用 `is_busy`（任一在途即视为忙碌）防并发。

**`_sync_timeline()`**：将 hero_related 公告的武将变更同步到时间轴（`append_announcement_events`），幂等、按 `ref/(date, hero)` 去重，全量扫描而非仅本批新增，重复检查可补齐此前同步失败的记录；失败仅记日志并返回 0，不中断检查。

### 3.7 巅峰赛识别循环（peak_select_watcher.py）

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
- **牌面签名** `board_signature()`：坐标全量量化（位置 4px、尺寸 8px 步长），吸收卡位检测像素级抖动，仅布局变化才触发 OCR

### 3.8 巅峰赛禁选建议（peak_ban_advice.py）

纯函数双维度象限判定，阈值按版本微调只需改常量：

| 常量 | 值 | 含义 |
|------|-----|------|
| `HOT_PICK_RANK_MAX` | 50 | 出场排名 ≤50 为热门 |
| `STRONG_WIN_RATE_MIN` | 50.0 | 胜率 ≥50% 为强势 |

- `evaluate_peak_ban_advice(win_rate, pick_rank, win_rate_rank)` → `PeakBanAdvice` 或 `None`
- 强势 + 冷门 → `PeakBanAdvice("ban_first", "Ban 位首选", weight=1000, bpi=1000+rank-win_rank)`
- 强势 + 热门 → `PeakBanAdvice("hot_pick", "热门强将", weight=500, bpi=500+rank-win_rank)`
- 弱势或维度缺失 → `None`（不打标签）
- `BPI = 权重 + 出场排名 − 胜率排名` 用于卡片排序

### 3.9 AI 成本估算入口（ai_cost.py）

`estimate_generation_cost(items, kind, model=None, use_rag=None)`：UI 经本模块估算成本，不直接依赖采集层；估算规则变更时 UI 无感知。攻略用 `estimate_cost`，相性用 `estimate_item_cost`（含 RAG 预算影响）。

### 3.10 实战配队导入（combo_import_service.py）

`run_import(source, heroes, output)` 幂等合并。武将名→ID 映射，未匹配项进报告；座次解析 + position 交叉校验；手工记录优先；非手工旧记录源中已不存在则移除；重复执行输出稳定。报告字段见 `module_scraper.md` 3.6 节。

### 3.11 脚本运行器（script_runner.py）

`ScriptRunner(QObject)` QProcess 异步执行 Python 脚本公共封装（自 `ui/shared/widgets` 迁入）：

```python
class ScriptRunner(QObject):
    output = Signal(bytes)
    finished = Signal(int)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def run(self, python: str, script: Path, args: list[str], working_dir: Path) -> bool:
        """启动脚本；已有任务运行时返回 False。"""
        if self.is_running():
            return False
        proc = QProcess(self)
        proc.setWorkingDirectory(str(working_dir))
        proc.readyReadStandardOutput.connect(lambda: self.output.emit(proc.readAllStandardOutput()))
        proc.readyReadStandardError.connect(lambda: self.output.emit(proc.readAllStandardError()))
        proc.finished.connect(lambda code, _status: self._on_finished(code))
        proc.start(python, ([str(script)] if script else []) + args)
        self._proc = proc
        return True
```

- `is_running()` 防并发（同一时刻只允许一个任务）
- `output(bytes)` / `finished(int)` 信号
- `run(python, script, args, working_dir)` 启动；已有任务返回 False
- `_on_finished(code)`：置 `_proc = None` 后发 `finished(code)`——先清引用再广播，保证 `finished` 回调里再次 `run()` 不会被 `is_running()` 误拦

业务层（RuleDocOpsService）与 UI 均可使用，仅依赖 QtCore，无 UI 控件依赖。

### 3.12 卡牌图鉴服务（card_catalog.py）

`CardCatalogService` 跨仓储视图组装，合并基础卡牌（`cards.json`）、字段定义（`card_field_schema.json`）与追加内容（`card_annotations.json`），不依赖 Qt。三个仓储的 `load()` 返回 `DataIssue` 列表，追加内容引用未知卡牌 ID 记为 `orphan_annotation` 警告，追加字段值类型不匹配记为 `invalid_field_value` 警告。

**核心方法**：

- `load_all()` — 加载三个仓储并校验追加内容引用完整性
- `list_views(keyword, card_type, adjustment)` — 组装视图列表（`CardViewModel`），支持关键词搜索、类型筛选、加强/削弱/活跃/待办四态过滤，保持 cards.json 类型分组和同组基础 ID 顺序
- `get_view(card_id)` — 获取单张卡牌视图，含字段值校验（`effect_entries`/`markdown`/`tags`/`boolean`/`number`/`select` 六种类型）
- `save_annotation_fields(card_id, fields)` — 校验字段类型、必填规则后写盘
- `add_effect_entry()` / `add_field()` / `update_field()` / `archive_field()` — 追加内容写编排

**editable 守卫**：`self.editable = base_available and schema.available and annotations.available`，编辑前检查，不可用时抛 `ValueError`。

### 3.13 推荐与对局摘要组装（analysis/）

两个纯数据服务，不依赖 Qt，供选将推荐页与对局攻略页读取快照。

`RecommendationService` 把胜率和推荐指数两条持久化数据聚合为一次刷新所需的 `RecommendationData(win_rates, indexes, indexes_stale)`，构造参数全部可注入（`load_win_rates` / `load_recommendation_indexes` / `refresh_recommendation_indexes` / `is_recommendation_index_stale` / `mark_recommendation_index_stale`）。三个方法：

- `load()` — 只读快照，附带指数是否过期标记；
- `rebuild_indexes()` — 重建推荐指数、清除过期标记，返回新鲜快照；
- `mark_indexes_stale()` — 官方榜单导入后标记指数待重建（不经此服务也能由 `mark_recommendation_index_stale(True)` 直接触发）。

`RecommendationData.rank_win_rates(names)` 按输入槽位返回**有效胜率的前三排名** `{槽位下标: 名次}`，只统计胜率非空的槽位，供卡片显示"当前选将中第 N 高胜率"。

`MatchAnalysisService(guide_manager, win_rates)` 基于本地攻略与单将胜率生成已确认 2v2 阵容的离线摘要 `MatchAnalysis`，无网络、无 LLM：

- `priorities` — 敌方按胜率降序取攻略中的 `counter_strategy`，上限 3 条；
- `threats` — 敌方每人的 `key_points` 前 2 条；
- `ally_tips` — 我方每人的 `key_points[0]` 与 `tips_for_beginners`；
- `missing_data` — 收集"暂无攻略"与"暂无历史单将胜率"的武将，逐条列出；
- 每条提示都带 `source_field`（`counter_strategy` / `key_points[i]` / `tips_for_beginners`）供界面标注来源。

### 3.14 知识库相关功能（已迁出）

元规则维护、知识库审计、索引精化、RAG 语料任务定义、武将分类 LLM 建议已整体迁至 [`./module_rag.md`](./module_rag.md)，此处不再重复。

---

## 四、关键代码片段

### 4.1 QProcess 参数构建与启动

```python
def _start_process(self, args: list[str]) -> None:
    """启动子进程并连接信号"""
    self._cancel_requested = False
    self._cancel_cleanup_process = None
    self._stdout_buffer.clear()          # 上一次任务的缓冲不能串到新任务
    self._stdout_line_buffer.clear()
    self._stderr_buffer.clear()
    self._failed_items.clear()
    self._process = QProcess(self)
    self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
    self._process.readyReadStandardError.connect(self._on_stderr_ready)
    self._process.finished.connect(self._on_finished)
    self._process.errorOccurred.connect(self._on_error)
    process_env = QProcessEnvironment.systemEnvironment()
    process_env.insert("MJS_QPROCESS_CHILD", "1")
    self._process.setProcessEnvironment(process_env)
    logger.info("启动子进程: python %s", " ".join(args))
    self._process.start(sys.executable, args)
```

> **设计思路：** `SeparateChannels` 确保 stdout 和 stderr 不混在一起。信号连接在 start 之前绑定，避免丢失启动瞬间的事件。`sys.executable` 保证与父进程使用同一 Python 解释器。启动前清空全部缓冲与失败项列表，保证失败原因识别和弹窗清单只反映本次任务。`MJS_QPROCESS_CHILD=1` 使子进程不再直写日志文件，全部输出经父进程收集。

### 4.2 stdout 行缓冲与进度正则解析

```python
def _read_stdout(self) -> None:
    if not self._process:
        return
    data = bytes(self._process.readAllStandardOutput())
    if not data:
        return
    self._stdout_buffer.extend(data)       # 完整 stdout，仅供失败原因识别
    self._stdout_line_buffer.extend(data)  # 实时行解析
    self._dispatch_stdout_lines()

def _dispatch_stdout_lines(self, flush: bool = False) -> None:
    while b"\n" in self._stdout_line_buffer:
        line, _, remaining = self._stdout_line_buffer.partition(b"\n")
        self._stdout_line_buffer[:] = remaining
        self._dispatch_stdout_line(line)
    if flush and self._stdout_line_buffer:          # 进程结束时的末尾残行
        self._dispatch_stdout_line(bytes(self._stdout_line_buffer))
        self._stdout_line_buffer.clear()
```

> **设计思路：** QProcess 的一次 readyRead 不等于一行输出，且 UTF-8 字符可能跨分块。基类保留未完成字节，只有读到换行后才解码并交给子类；进程结束时 `flush=True` 再分发末尾残行。取消时只调用 `cancel_process()`，不在 GUI 线程使用 `waitForFinished()`；临时文件清理和状态通知继续由 `finished` 信号统一完成。

`fetch_utils._GENERATION_PROGRESS_PATTERN` 除了放行 `[i/N] ... START/OK/FAIL/SKIP`、`[...] 开始...` 与 `[休息] ...` 冷却行外，还放行 `^\s*\[RAG\]` 与 `^\s*\[重试\]` 前缀，分别用于展示 RAG 降级提示与 API 限流重试状态；这两类行不包含 `[i/N]`，不会影响进度条解析。`[重试]` 行由 `api_generator._call_api` 在指数退避前输出，`GuideProgressDialog` 解析后在状态栏显示重试状态并在详情栏标注当前进度与重试原因。协议行的结构化解析集中在 `fetch_utils.parse_generation_event()` → `GenerationEvent`，白名单过滤与进度条推进共用同一解析源。

`BaseFetchService._dispatch_stdout_line` 在转发每行 stdout 的同时，用正则 `\[(\d+)/(\d+)\]\s+(.+?)\s+FAIL(?:\s|$)` 收集失败项名到 `_failed_items`，供工作流在出错弹窗的"查看详情"中列出失败清单。`_start_process` 为所有 QProcess 子进程统一注入 `MJS_QPROCESS_CHILD=1` 环境变量（含 AI 子进程），使其不直写文件、stdout/stderr 交由父进程统一收集到 `subprocess.ai.*` / `subprocess.official.*` logger，最终由 `scraper/ai_generation.log`、`scraper/official.log` handler 的 `keep_debug=True`（级别固定 DEBUG、不跟随用户级别）保留 429/length/JSON 等失败原因，即使 root level ≥ WARNING 也不丢。

### 4.3 临时文件自动清理

```python
def _cleanup_context(self) -> None:
    """默认清理 _context 中的临时 heroes 文件；无资源时为 no-op。"""
    self._cleanup_tmp_file()

def _cleanup_tmp_file(self) -> None:
    tmp_path = self._context.get("tmp_path", "") if self._context else ""
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
            logger.debug("已清理临时文件: %s", tmp_path)
        except OSError as e:
            logger.warning("清理临时文件失败 %s: %s", tmp_path, e)
```

> **设计思路：** 指定获取模式（指定采集、指定配对、选定武将、实战配队清单）需要写入临时 JSON 文件传给子进程。`_on_finished()` 与 `_on_error()` 都在返回前调用 `_cleanup_context()`，并清空全部缓冲、置 `_context = None`，避免残留文件堆积。

---

## 五、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.manager` | `DataIssue` 模型（CardCatalogService 的仓储校验结果） |
| 依赖 | `src.scraper.*` | 构建 CLI 参数调用爬虫/AI 脚本（`official` / `incremental` / `ai_batch`） |
| 依赖 | `src.capture.adb_screen` | CaptureService / EmulatorOperationService 持有 AdbCapture 实例 |
| 依赖 | `src.capture.prober` | ADB 路径探测与 MuMu 实例枚举（`probe_mumu_adb` / `test_adb_path` / `probe_all_devices_with_status`） |
| 依赖 | `src.capture.image_utils` / `image_validation` | 截图文件保存与本地图片加载 |
| 依赖 | `src.ocr.*` | 模板管理器、识别器、ROI 布局配置 |
| 依赖 | `src.ocr.paddle_loader` | 官方榜单按需提供简体 / 繁体 PaddleOCR 引擎 |
| 依赖 | `src.ocr.official_board_parser` | 官方榜单图片读取、固定版式切分、横线恢复和胜率数字模板算法 |
| 依赖 | `src.ocr.character_similarity` | 官方榜单复用公开的武将词表纠错服务 |
| 依赖 | `src.data.win_rate_repository` | 胜率 CSV 覆盖后清空读取缓存（`clear_win_rate_cache`） |
| 依赖 | `src.data.peak_win_rate_repository` | 巅峰赛胜率 CSV 覆盖后清空读取缓存（`clear_peak_win_rate_cache`） |
| 依赖 | `src.data.recommendation_index_repository` | 推荐指数加载 / 重建 / 过期标记（`mark_recommendation_index_stale`） |
| 依赖 | `src.data.combo_manager` / `combo_seats` | 实战配队导入合并与 note 座次解析 |
| 依赖 | `src.data.hero_manager` / `guide_manager` / `synergy_manager` | 数据清理、失效关联修复与修改事务 |
| 依赖 | `src.data.card_catalog` | CardCatalogService 的三个仓储（cards/schema/annotations）与基础模型 |
| 依赖 | `src.data.json_repository` | `atomic_write_json()`（RuleDocOpsService，知识库范围） |
| 依赖 | `src.data.announcement_manager` | AnnouncementService 的公告合并去重与百科快照持久化 |
| 依赖 | `src.data.hero_timeline` | 武将变更时间轴（announcement 同步） |
| 依赖 | `src.scraper.official_source.announcement` | 公告 / 百科拉取、武将快照与更新候选计算 |
| 依赖 | `src.scraper.ai.*` | 成本估算入口（`estimate_cost` / `estimate_item_cost`）与分类建议的 JSON 解析 |
| 依赖 | `src.config.env` | API 档案解析（`resolve_api_config` / `get_api_config`）、供应商预设（`PROVIDER_PRESETS`）、截图目录与模拟器配置读写 |
| 被调用方 | `src.ui.app.main_window` | 主窗口连接业务服务的 Signal，UI 操作触发 `fetch_*()`；`PollCoordinator` 编排三板块轮询 |
| 被调用方 | `src.ui.app.poll_coordinator` | 消费 `OcrService.poll_tick`，调用 `CaptureService.capture_for_poll` / `submit_ocr_task` |
| 被调用方 | `src.ui.data_admin.official_data_import_dialog` | 触发后台导入、展示进度，并串联复核会话 |
| 被调用方 | `src.ui.data_admin.official_import_review_dialog` | 调用 `review_candidates()` / `apply_reviewed_records()` 完成人工修正落盘 |
| 知识库相关 | [`./module_rag.md`](./module_rag.md) | 元规则维护、知识库审计、索引精化、语料任务定义、武将分类 LLM 建议的依赖与被调用方 |
