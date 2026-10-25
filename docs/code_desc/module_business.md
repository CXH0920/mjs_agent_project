# 模块：业务服务层

> 对应目录：`src/business/`
> 职责：QProcess 子进程管理（看门狗 + 任务台账）、服务编排、截图与 OCR 调度、官方榜单图片导入与复核、推荐/对局摘要组装、公告检查与百科 diff、卡牌百科同步与忽略名单管理、备份恢复
> 知识库相关服务（元规则维护、审计、索引精化、语料任务定义、分类建议）见 [`./module_rag.md`](./module_rag.md)
> 文档日期：2026-10-23（代码基线 a63bc3c）

---

## 一、模块职责

本层是 UI 层和采集层之间的**桥梁**，负责：

1. **QProcess 子进程管理** — 构建 CLI 参数、启动/监控/终止子进程、转发 stdout/stderr、清理临时文件；30 分钟看门狗兜底（超时 kill + ERROR）与任务结果台账（`task_ledger`，跨日志轮转窗口留痕）
2. **ADB 截图业务编排** — 管理 AdbCapture 生命周期，协调截图 → 模板匹配 → OCR 的流程，并提供选将推荐 / 巅峰赛 / 对局攻略三板块共享的单次截图入口
3. **OCR 控制服务** — 模板管理、轮询会话与退避控制、冷却管理，以及会话代数取消
4. **模拟器后台操作** — 独立执行设备探测与 ADB 会话操作，避免实例枚举阻塞模板截图
5. **官方榜单导入** — 解析固定版式的 2v2 胜率/出场榜、巅峰赛胜率/出场榜与武将放逐榜，按表格行安全覆盖 CSV；名称校验不通过时保存复核会话供人工修正后复用（不重新 OCR）
6. **推荐数据组装** — 一次读取胜率与推荐指数快照，并提供数值化的卡片排名数据
7. **卡牌图鉴服务** — 跨仓储视图组装、取值校验与追加内容写编排
8. **公告检查与百科 diff** — 手动触发公告更新检查，百科内容哈希 diff 与忽略名单过滤；首次基线本地化、阶段令牌跨线程统一持久化
9. **卡牌百科同步** — 手动触发官网同步检查（非定时轮询），差异比对与忽略名单管理；首跑基线本地化、变更记录追加驱动 curated 精化
10. **忽略名单基础设施** — `baike_ignore_store` 统一管理卡牌与武将百科差异的忽略/恢复/查询，CardSyncService 与 AnnouncementService 共享
11. **数据新鲜度判定** — `freshness.py` 无 Qt 依赖纯业务层：武将/攻略/相性/官方榜单四项健康判定与建议操作路由，供状态栏数据新鲜度芯片（`src/ui/app/data_freshness_chip.py`）消费；损坏文件留底与档位阈值可配置
12. **备份恢复** — `backup_restore_service.py` 消费 `data/backups/` 自动快照：列出、键级 diff 预览与原子恢复（恢复前先快照当前状态保证双向可回退、坏备份两侧拒绝）
13. **官网访问冷却闸门** — `common/check_cooldown.py` 的 `CheckCooldown`：公告检查与卡牌检查共用的忙碌判定 + 最小间隔原子闸门（`try_acquire` 加锁使"忙碌→冷却→置位"不可分割）
14. **首启向导分辨率体检** — `emulator/layout_resolution.py` 的 `compare_resolution`：纯函数判定模拟器画面与 ROI 参考尺寸的 pass/warn/fail（等比缩放判 WARN、竖屏/非等比/过低判 FAIL）

元规则文档维护纯函数与 RAG 索引精化三层架构已整体迁至 [`./module_rag.md`](./module_rag.md)，本层不再承担其职责描述。

核心设计原则：**不持有 UI 引用**，全部通过 Qt Signal 与主窗口通信。

---

## 二、文件结构

```
src/business/
├── __init__.py
├── card_catalog.py                    # 卡牌图鉴：跨仓储视图、取值校验、追加写编排
├── card_sync.py                       # 卡牌百科同步服务（CardSyncService）
├── ai_cost.py                         # AI 成本估算业务层入口
├── freshness.py                       # 数据新鲜度判定核心（武将/攻略/相性/榜单四项，无 Qt 依赖；损坏留底 + 阈值可配置）
├── fetching/
│   ├── base_fetch_service.py          # QProcess 生命周期、行缓冲与统一收尾
│   ├── fetch_utils.py                 # QProcess 公共工具函数
│   ├── hero_fetch_service.py          # 武将采集业务
│   ├── guide_fetch_service.py         # 攻略生成业务
│   ├── synergy_fetch_service.py       # 相性生成业务
│   └── synergy_reload_worker.py       # 相性数据后台重载
├── emulator/
│   ├── capture_service.py             # ADB 截图与 OCR 调度门面（截图流水线 + 结果拼装；连接域出仓后 479 行）
│   ├── capture_connection.py          # ADB 连接域（CaptureConnectionManager：配置热更/会话状态/连接编排，P1-8 第三刀出仓）
│   ├── layout_resolution.py           # 模拟器画面与 ROI 参考尺寸体检判定（compare_resolution，纯函数，首启向导段 2）
│   ├── ocr_task_coordinator.py        # OCR worker 生命周期、预热状态机与任务组装（审计 G8 刀 3；关停后拒绝复活 worker）
│   ├── official_import_gateway.py     # 官方榜单导入网关（提交/排他/进度转发/结果分派，审计 G8 刀 1）
│   ├── image_save_scheduler.py        # PNG 后台保存调度（单线程执行器，审计 G8 刀 2）
│   ├── emulator_operation_service.py  # 模拟器后台操作
│   └── mumu_config_coordinator.py     # MuMu 配置状态协调
├── recognition/
│   ├── name_resolution.py             # 武将名纠错与词表消解纯规则（HeroNameResolver）；白名单静态冲突检查（find_whitelist_conflicts）
│   ├── ocr_service.py                 # OCR 控制、模板和轮询
│   ├── ocr_worker.py                  # 唯一后台识别队列（弃单任务短路 + 预热标志复位）
│   ├── official_data_import_service.py # 官方榜单导入（识别编排 + 面板守卫 + 批量写回）
│   ├── official_ocr_engines.py        # 官方导入双 OCR 引擎懒加载与跨任务移交（OfficialOcrEngines，罕见字计数熔断 + 冷却）
│   ├── pending_stats.py               # OCR 未决错法频次与人工确认答案记录
│   ├── peak_select_watcher.py         # 巅峰赛（2v2）选将实时识别循环
│   └── session_guard.py               # 竞态守卫原语（SessionGuard/BusyGate/OneShotToken，P0-3 定稿；BusyGate 增 reset）
├── analysis/
│   ├── recommendation_service.py      # 推荐数据组装
│   ├── match_analysis_service.py      # 对局攻略分析
│   ├── peak_ban_advice.py             # 巅峰赛禁选建议双维度象限判定
│   └── win_rate_ranking.py            # 胜率名次单一事实源（derive_win_rate_ranks，选将推荐与巅峰赛禁选共用）
├── maintenance/
│   ├── data_management_service.py     # 数据清理、修复与修改事务（主线程守卫 + 回滚汇总）
│   ├── backup_restore_service.py      # 备份恢复服务（data/backups 快照的列出/预览/恢复）
│   └── combo_import_service.py        # 实战配队导入合并（幂等；源结构校验 + 覆盖前快照）
├── announcement/
│   └── announcement_service.py        # 公告检查与百科 diff 服务（线程 + Qt 信号；共享冷却闸门）
└── common/
    ├── script_runner.py               # QProcess 异步执行 Python 脚本公共封装（含 30 分钟看门狗 + 树杀 + 启动失败终态通知）
    ├── task_ledger.py                 # 任务结果台账（JSONL 追加 logs/task_results.jsonl，跨进程文件锁串行化）
    └── check_cooldown.py              # 手动检查的冷却闸门（CheckCooldown，忙碌判定 + 最小间隔原子操作）
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

**看门狗（b3327f1 → f1b13f8 无进度上限）**：`_WATCHDOG_TIMEOUT_MS = 30*60*1000`，`_start_process` 尾部 `_start_watchdog()` 开启 QTimer 单发定时器，`_on_finished` / `_on_error` 先 `_stop_watchdog()`；超时回调记 ERROR 后 `cancel_process(process)`（树杀，不用 `cancel()` 以免把"超时失败"记成"用户取消"）。**看门狗计时在每条进度行时重置**（`_dispatch_stdout_line` 经 `is_generation_progress_line` 识别 START/OK/FAIL/SKIP/重试/休息行调 `_feed_watchdog()`）——把"总时长上限"收敛为"无进度上限"：browser 模式配对间还有反风控随机休息，长清单全程超 30 分钟也照常放行，只有"无任何进度的黑洞化"被拦。超时日志措辞相应改为"超过 30 分钟无进度"。

**启动与取消的 Starting 阶段崩溃（VUL-B2-001）**：Qt6 对尚处 `Starting` 状态的进程 `kill()` 会在事件循环内原生 abort（`processId()` 为 0、树杀退化为 kill）。`cancel()` 在 `Starting` 时只置 `_cancel_requested` 并挂 `started` → `_on_started_after_cancel`，等进程真正起来后补树杀；`cancel_process` 对 `Starting` 进程直接跳过并告警。`_on_error` 对取消期间的 `FailedToStart` 先 `_release_process()` 再 `_finish_cancellation()`，避免 `_cancel_requested` 恒为 True 导致永久忙碌（TD-A5-001）。

**取消清理轮询有界（9ccc2fc）**：`_finish_cancellation()` 的 50 ms 轮询加 `_CANCEL_CLEANUP_MAX_POLLS = 100` 上限（约 5 秒）——taskkill 被安全软件拦截或 PID 复用时可能永不返回，无上限会让 `_cancel_requested` 永不复位，服务进入只有重启才能解除的永久忙碌态。超限记 WARNING 后强制收尾。

**子进程工作目录与进程释放（9ccc2fc）**：`_start_process` 设 `setWorkingDirectory(PROJECT_ROOT)`——子进程以 `-m 相对模块名` 启动，快捷方式/打包态从其他目录启动时按当前目录解析会直接报"找不到模块"（TD-A6-001）；`_release_process()` 在 `_start_process` 开头与 `_on_finished` 结尾置空引用并 `deleteLater()` 释放已结束的 QProcess，避免对象与信号连接随任务轮次持续累积（TD-A8-001）。

**完成钩子重入进程保护（4bd9586）**：`_on_finished` 在调用 `_on_process_finished()` 钩子**前**先记录 `finished_process = self._process`，钩子返回后仅当 `self._process is finished_process` 才执行 `_release_process()`——钩子可能经直连信号同步发起下一阶段（公告多阶段更新链：检查完成后直接触发武将变更获取），此刻 `_process` 已是重入启动的新进程，按旧逻辑释放会把新 QProcess `deleteLater`，其析构时强杀仍在运行的子进程（表现为随机退出码 0xF291=62097）。

**错误路径台账**：`_on_error` 补 `record_task(ok=False, ...)`——`FailedToStart` 等错误 Qt 不发 `finished`，不在此记则台账缺失该类失败。

**任务台账（c88bf63 → 2b73535 跨进程锁）**：`_on_finished` 调 `record_task(self._service_name, ok=exit_code==0 and not self._cancel_requested, exit_code=..., failed=len(self._failed_items), duration_s=..., reason=...)` 逐任务追加 JSONL 到 `logs/task_results.jsonl`——日志按 10MB×5 轮转（65~78 天窗口），失败痕迹会随轮转消失，台账是唯一能跨窗口留住"某天某任务是否失败"的记录。reason 优先取 CLI 失败摘要行，其次"思考过程耗尽输出额度"，否则退出码。**跨进程文件锁**：写者含 UI 父进程与维护脚本/索引构建脚本，彼此无先后约定，仅靠 `O_APPEND` 在 NTFS 上实测静默丢 2%~12% 记录——`record_task` 经 `_ledger_lock()` 对 `*.lock` 首字节加非阻塞独占锁（`msvcrt.LK_NBLCK` / `fcntl.LOCK_NB`，50 次×20 ms 有界等待），锁机制不可用时降级无锁写入（台账是辅助设施，不能因锁故障拖垮任务）。`read_entries()` 跳过空行与非对象 JSON 行（外部工具追加的 `123`、`["x"]`）并记 WARNING，不再因 `entry.get` 抛 `AttributeError` 让整份台账读不出。

**stderr tqdm 降噪（1426686）**：子进程 stderr 文本含 `%|`（tqdm 进度条特征，权重加载走 stderr 属正常行为）降为 DEBUG，其余 stderr 保持 WARNING——原一律 WARNING 曾占 ai_generation.log 中 89% 的 WARNING 噪声。

**进度协议**：`fetch_utils.parse_generation_event` 把 `[i/N] ... START/OK/FAIL/SKIP`、`[重试]`、`[休息]` 等 CLI 协议行解析为 `GenerationEvent`，与白名单过滤共用同一解析源。Guide 在带 `(current, total)` 的事件（start/ok/fail/skip）上推进 `progress_value`；Synergy 只在结果落定（ok/fail/skip）后推进，避免 START 行先推进度条。

用户主动中止会置 `_cancel_requested` 并标记取消状态；Windows 通过 `taskkill /PID <pid> /T /F` 异步结束 AI Python 进程及全部 Playwright/Edge 后代，`_finish_cancellation()` 以 50 ms 轮询等待清理进程结束（有界 100 轮），完成后才发出 `cancelled`，避免浏览器残留占用 OCR 所需资源。其他平台仍终止当前子进程；取消引起的崩溃事件会被忽略，临时文件与上下文释放由 `finished`/`errorOccurred` 统一收尾。

`SynergyReloadWorker` 在后台解析已分批提交的 `synergies.json`；经 `SynergyFetchService.reload_from_disk()` 触发，完成后 `SynergyFetchService._on_reload_loaded()` 由主线程一次性把结果写回 `SynergyManager`（`replace_loaded_data`）并发 `reload_finished`，避免取消后同步解析 JSON 阻塞窗口事件循环。**相性重载双向互斥（1d8334a）**：`_is_busy()` 覆写把后台重载与抓取子进程纳入同一把互斥——只拦"抓取中重载"不够，重载线程读的是启动前旧快照，期间若启动生成子进程并分批提交，重载完成会用旧快照覆盖新数据（VUL-B3-002）；`_on_reload_loaded` 写回前比对文件 mtime（`_reload_stale`），期间文件已变则放弃写回并发 `reload_finished` 让调用方重触发；`reloading` 属性供 UI 区分拦截文案；`_on_reload_failed` 与 `_on_reload_loaded` 对称释放 worker 引用（deleteLater 销毁 C++ 对象后残留引用会让下次 `isRunning()` 抛 `RuntimeError`）。

**空目标伪成功拦截（1d8334a）**：`HeroFetchService.fetch_specific()` 空列表直接拒绝并返回 False——子进程对空目标会打印"无目标武将"并以 0 退出，父进程据此上报成功，公告更新流程会把未采集的批次当成已完成并 `mark_applied`（VUL-B4-002）。宁可不发信号，也不发假的成功信号。

**RAG 双版本传参**：`GuideFetchService.fetch_all/incremental/specific` 与 `SynergyFetchService.fetch_pair/single/pairs_list` 均提供 `use_rag: bool = True` 参数（由 `BackendChooseDialog` 的「语料增强」单选传入，默认 RAG 增强）。`use_rag=False`（经典模式）时，子进程参数追加 `--no-rag`，AI CLI 侧将 `RAG_ENABLED=false`，生成链路不再注入 RAG 语料。进度行白名单（`fetch_utils.is_generation_progress_line`）同步放行 `[RAG]` 与 `[重试]` 前缀：前者展示 AI 侧的 RAG 降级提示（`[RAG] 语料不可用，本次已降级为经典模式（原因）`），后者由 `api_generator._call_api` 在 429 指数退避前输出，进度窗口据此显示重试轮次与等待秒数。

**相性三种提交模式**：`fetch_pair`（两个武将配对，`--synergy-pair`）、`fetch_single`（一个武将对全体，`--synergy-single`）与 `fetch_pairs_list`（实战配队清单的显式 `hero_a_id/hero_b_id` 配对列表，`--synergy-list`）共用 `_submit()`：`_is_busy()` 拦截 → 写临时 JSON → 拼 CLI 参数（`--no-rag` / `--update` / `--browser`）→ `_start_process()`。`overwrite=True` 追加 `--update`，与 Guide 的增量/指定模式一致。

### 3.2 CaptureService（截图业务门面）与同包协作模块

`CaptureService` 是选将推荐 / 巅峰赛 / 对局攻略三板块共享的截图会话与 OCR 队列入口（UI 看到的公共信号面不变）。**职责域出仓（审计 G8 + P1-8 第三刀，2026-10）**后，CaptureService（479 行）只保留截图流水线与结果拼装，四块职责下沉到同包协作模块，对 UI 仍以门面信号转发；关停守卫与全仓自造竞态守卫统一收口到 `src/business/recognition/session_guard.py` 命名原语（P0-3，三原语定义与全部消费点见 3.18）：

| 模块 | 职责 | 与 CaptureService 的边界 |
|------|------|------|
| `OcrTaskCoordinator`（ocr_task_coordinator.py） | OCR worker 唯一创建点（`ensure_worker()`，经构造注入的 `on_worker_created` 回调同步接线后才 `start()`，跨线程首建同样成立；**shutdown 后拒绝复活 worker**）、模型预热状态机（idle/warming/ready/failed，`reset_warmup()` 悬挂出口）、识别任务的阈值/ROI 组装（`build_task()`） | 持有 `_worker`（创建由 `_worker_lock` 双检保护）/`_worker_retired`/`_on_worker_created`/`_warmup_task`/`_warmup_state`；配置经 `config_provider` 晚绑定读取（阈值改动实时生效），ROI 共享 `CaptureService._roi_config` |
| `OfficialImportGateway`（official_import_gateway.py） | 官方榜单导入网关：整批任务提交、进行中排他集合、worker 进度转发、完成/失败分派 | 持有 `_pending` 排他集合；`progress/completed/failed` 三信号与 CaptureService 同名门面信号直连 |
| `ImageSaveScheduler`（image_save_scheduler.py） | PNG 后台保存：单线程执行器（`image-save`）串行落盘，完成后广播 `image_saved` | 无锁、无挂起表；`save_future` 由 CaptureService 持有用于结果拼装 |
| `CaptureConnectionManager`（capture_connection.py） | 采集会话连接域（**模拟器 ADB / 官方 PC 客户端双形态**）：配置热更与采集会话重建（模拟器形态按路径/端口/品牌/截图模式重建 `AdbCapture`；PC 形态按 `emulator_type=pc_client` 构建 `PcClientCapture`，不要求 ADB 配置，窗口标题变更或跨形态切换时重建；连接键 `emulator_*` 新键优先、`mumu_*` 旧键回退）、会话状态机（unconfigured/disconnected/connecting/connected/offline）、连接/断开编排（事件文案按形态分立「ADB 已连接 / PC 客户端已连接」）、`is_current` 过期实例校验 | 持有 `_capture`（类型 `AdbCapture \| PcClientCapture`）/`_config`/会话状态与 `_session_lock`/`_adb_io_lock`（锁职责原样迁移）；状态与状态栏消息经回调回发，emit 留在 CaptureService |

两把互不嵌套的锁随连接域迁入 `CaptureConnectionManager`：`_session_lock` 只保护会话对象与状态字段的快速读写（GUI 快速路径争用，锁内只做纯内存操作，不发 IO、不 emit），`_adb_io_lock` 单独串行化 `connect()` / `screencap_full()` 这类秒级阻塞 IO（超时重试最坏约 45 秒），经 `session_lock` / `adb_io_lock` 只读属性暴露给宿主的截图域临界区。CaptureService 自身保留 `_adb_executor`（`adb-capture`）单线程执行器串行执行 ADB 截图，与图像保存执行器（已迁入 `ImageSaveScheduler`）互不等待；截图请求与 OCR 结果的关联表（`_pending_ocr_captures`）与完成分派留在 CaptureService。关停守卫为 `_shutdown_token`（OneShotToken，P0-3 收口，原 `_closed` 裸 bool）：`do_capture()` / `_on_background_capture_ready()` / `capture_for_poll()` 在入口检查 `spent`，`shutdown()` 时 `mark()` 置位后拒绝新任务，语义不变。`_config` property 保留直通 `self._connection._config` 的测试兼容缝（生产代码走 `update_config()`）。

截图流程（手动截图路径）：

```
do_capture(hero_names, template_name="hero_selection", force_ocr=False, perform_ocr=True)
  └─ [_adb_executor 单线程] capture_screenshot()
       ├─ [connection.session_lock] 取会话；未连接则 connect_capture()
       │   └─ [connection.adb_io_lock] AdbCapture.connect()
       ├─ is_current(capture) 复核（连接期间配置热更重建实例则拒绝）
       ├─ [connection.adb_io_lock] AdbCapture.screencap_full() → PIL Image（不写磁盘、不触发 OCR）
       └─ future.add_done_callback → _capture_ready 信号
            └─ [GUI 线程] _on_background_capture_ready() → _handle_capture_result()
                  ├─ should_ocr = perform_ocr 且（force_ocr 或 启用 OCR）
                  ├─ _queue_capture_ocr() → build_task() 组装 → _ensure_ocr_worker().submit() 入唯一队列（结果经 _on_ocr_task_completed 回来）
                 └─ _save_scheduler.schedule() → [ImageSaveScheduler 单线程] save_image() → image_saved 信号
```

> **2026-09 变更**：删除了 `is_poll` 全局配置误读（原从 `mumu_ocr_poll_mode` 读取）、`POLL_MATCH_COOLDOWN_SECONDS` 常量、`_poll_cooldown_until` 字段与冷却跳过分支。`should_ocr` 不再包含 `or is_poll` 项。真实轮询冷却统一由 `OcrService.set_task_cooldown()` 按任务级承担（见 3.3），CaptureService 不再自行持有冷却状态。

> **2026-10 修复**：
> - **截图终止信号保证（a2f6ecb）**：`_handle_capture_result()` 整体包 `try/except`——目录不可写、执行器已关停、OCR 协调器已退役都在此抛出，漏接会让调用方的在途锁永不释放（界面永久停在"正在截图"）。截图请求必须且只能以 `capture_completed` / `capture_failed` 之一收尾。
> - **轮询截图等待上界（VUL-B3-002）**：`capture_for_poll()` 的 `future.result()` 加 `timeout=_POLL_CAPTURE_TIMEOUT_S`（120 秒，约等于执行器中一个最坏在途任务：连接约 45s + 截图重试 3×15s 加余量），超时按可重试失败返回，执行器积压时轮询线程不再无限期静默阻塞。
> - **重连路径统一（TD-A2-001）**：`_reconnect_if_needed()` 是手动截图与轮询截图共用的共享会话重连——经 `connect_capture` 完成状态同步、连接后校验 `is_current`；轮询路径原先自持 `connect()` 分支，与手动路径漂移。

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

`submit_ocr_task()` 的参数集为 `(image, hero_names, template_name, recognize=True, rois=None, match_template=True, fallback_on_template_miss=False, allow_result_reuse=False)`；`fallback_on_template_miss=True` 用于对局攻略任务，模板未命中仍继续 OCR 以保留跳转判断素材。轮询任务命中一次页面后由 `OcrService.set_task_cooldown()` 按任务记冷却（`POLL_MATCH_COOLDOWN_MS`，`hero_selection` 的时长取 `mumu_hero_selection_cooldown` 配置），冷却中的任务不进入 `due_poll_tasks()`，窗口期内该任务不再匹配、不 OCR。

**OCR 预热**：`warmup_ocr_model()` 把模型加载、特征预热与推理预热作为特殊 `OcrTask` 投入同一串行队列（状态 `idle/warming/ready/failed`，经 `ocr_warmup_state_changed` 广播；状态机在 `OcrTaskCoordinator`，CaptureService 保留门面）；`wait_ocr_warmup(timeout_ms=15_000)` 供主窗口显示前的启动画面阶段阻塞等待——引擎初始化期间长时间持有 GIL，若在事件循环运行后再预热会卡住界面。**预热弃单与复位（0fff5e2）**：等待超时的预热任务经 `OcrTask.abandon()` 置 `cancelled` 并释放整帧图像引用（worker 被长任务占住期间滞留任务到取出才释放，峰值仍是 O(占用时长)），worker 取出弃单任务时短路（`outcome=cancelled`）且不执行过期 OCR；`reset_warmup()` 是预热悬挂出口——丢弃在途预热任务并把状态机拨回 idle，同时经 `reset_warmup_state()` 复位 worker 的 `_warmup_queued` 标志（被弃单或滞留的预热任务不会自行复位，只拨回 idle 会让 `warmup_model` 恒返回 None、重试永久无效，VUL-B4-002）。首启向导的「重新初始化」经此接口复位后再重新入队。

**2026-09 变更**：
- **ADB raw 帧截图提速（cd35c98）**：`capture_for_poll()` 直接返回 numpy 数组（经 `_adb_executor` 排队），与手动截图、模板截图互斥；`AdbCapture.screencap_raw()` 跳过 PIL 解码直接返回 PNG bytes，大幅降低轮询路径的 IO 与解码开销。
- **`reset_ocr_recognizer_cache()`（9ca1b91）**：白名单治理后调用的引擎缓存重置入口。OCR 识别器（含复核引擎）按 `hero_names` 分片缓存，用户层白名单更新后需使全部旧缓存失效，该方法清除所有分片并在下一轮 OCR 时按新词表重建。

**2026-10 职责域出仓（审计 G8）**：官方导入网关 / 图像保存调度 / OCR 任务编排依次出仓为 `official_import_gateway.py`、`image_save_scheduler.py`、`ocr_task_coordinator.py`（见 3.2 开头的职责表）。对外契约不变：UI 侧 12 处信号连接与全部公共方法名保持原样，`submit_ocr_task()` 的提交动作仍经 `self._ensure_ocr_worker()`（实例级补丁锚点语义保留）；worker 接线（`task_completed` → CaptureService 完成分派 / OfficialImportGateway 结果分派，`official_progress` → Gateway 进度转发）经构造注入的 `on_worker_created` 回调同步完成，回调在 `_worker_lock` 持有期间执行且先于 worker `start()`（2026-10-02 修复：替代原 `worker_created` 信号，消除跨线程首建时 Queued 投递致接线滞后、官方导入 `_pending` 永久锁死的竞态）。

### 3.3 OcrService（OCR 控制）

控制模板制作、轮询会话与退避状态；不持有任何截图或识别器，识别工作全部经 `submit_ocr_task()` 交给 `CaptureService`：

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
  ├── pause_for_idle()                          → 闲置检测达阈值时暂停轮询（独立于故障 paused）
  ├── is_poll_idle_paused() -> bool             → 当前是否处于闲置暂停态
  ├── complete_poll(generation, outcome, detail) → 主线程记录一轮结果并迁移状态
  ├── invalidate_inflight_poll()                 → 作废在途轮询（巅峰赛启动时调用）
  ├── activate_task(name) / deactivate_task(name)
  ├── set_task_cooldown(name, seconds=None) / clear_task_cooldown(name)
  ├── due_poll_tasks() -> list[str]              → 获取当前到期任务
  ├── get_task_state(name) -> PollTaskState      → 供调度与测试读取
```

**轮询会话模型**：`PollSession(generation, cancel_event)` 提供会话代数——`start_poll()` / `stop_poll()` / `invalidate_inflight_poll()` 各递增一次代数并置位旧取消标记。在途轮询的后台线程在每个关键写入点检查取消标记，结果回 GUI 线程时再经代数校验，过期结果直接丢弃，避免"停止后又被旧拍重新挂起"或旧结果回写冷却。在途拍单飞由 `_poll_gate`（`BusyGate`，89e4e8d/P0-3 收口，原 `_poll_in_flight` 裸 bool）保证：`begin_poll()` 检查 `is_busy` 后 `acquire()` 占用，`complete_poll()` 由持有者 `release()`；**生命周期边界（`stop_poll` / 重启 `start_poll` / `invalidate_inflight_poll` / `_halt_poll`）一律用 `reset()`**——调用方声明"占用者不是我"，在途持有者不会走到自己的完成路径，不 reset 会把后续请求永久挡在忙态（9ccc2fc，TD-A9-001：`release` 与 `reset` 语义分立，使"谁在释放"可被逐一审计）。

**状态机**：`stopped / running / backing_off / paused / idle_paused`，由 `complete_poll()` 依 outcome 迁移——

- `matched` / `healthy_no_match`：失败计数归零，若定时器间隔已被拉长则恢复基础间隔，状态回 `running`；
- `prerequisite_unconfigured` / `prerequisite_template_missing`：停止定时器并转 `paused`（提示未配置 ADB / 未加载识别模板）；
- 其他可重试失败（连接、截图、OCR、超时）：失败计数 +1，连续达到 `POLL_MAX_FAILURES = 5` 转 `paused`，否则把定时器间隔动态拉长为 `max(基础间隔, POLL_BACKOFF_DELAYS_MS[计数-1])`（`2s / 5s / 15s / 30s`）并转 `backing_off`，提示剩余重试次数。定时器持续运行（Qt 对激活中的定时器改间隔会重启计数），因此不再需要"单次触发 + 重新排程"。
- **`idle_paused`（2026-09 新增）**：连续 5 分钟无画面变化时由 `PollCoordinator` 检测帧指纹不变后调 `pause_for_idle()` 进入。独立于故障 `paused`，由整帧指纹判闲（`frame_fingerprint` 32×18 灰度、MAD<3 判同）驱动。恢复路径：点击状态栏闲置暂停胶囊、重新激活主窗口、或触发 `sync_with_connection()`（配置保存/连接变化/导入对话框关闭）。

`start_poll()` 把间隔钳到 `max(interval_ms, 1000)`，并把任务状态重置为 `hero_selection` 激活、`match_guide` 停用；两个任务各自维护 `PollTaskState(active, cooldown_until, consecutive_failures)`，冷却彼此独立，任一任务冷却只跳过自己。任务级冷却缺省取 `POLL_MATCH_COOLDOWN_MS = 180_000`（3 分钟）。

模板按名称独立管理：武将选择模板继续使用 `templates/wujiang_select.png`，对局攻略模板使用 `templates/match_guide/template.png`。模板缺失时对应任务返回 `template_missing`，由消费端停用该任务，不影响另一个任务。

### 3.4 EmulatorOperationService（模拟器后台操作）

`EmulatorOperationService` 只依赖 `CaptureService` 和底层探测模块，不持有 UI。它使用两个单线程执行器：探测线程负责 ADB 路径与所选品牌实例枚举（`probe_all_devices_with_status(emulator_type)` 经 `probers` 注册表分发五品牌；探测执行器**同键任务去重**——连点刷新不会累积排队多个相同探测任务），ADB 会话线程负责连接、设备测试和模板截图；两类任务互不排队。`probe_all_devices_with_status()` 会把失败原因与"正常但没有实例"区分开。关停守卫为 `_shutdown_token`（OneShotToken，df019a5/P0-3 收口，原 `_closed` 裸 bool）：`shutdown()` 时 `mark()` 置位，此后探测与 ADB 会话任务在入口检查 `spent` 拒绝，语义不变。

> **架构防火墙声明（637102b）**：模块文档字符串明确声明 `EmulatorOperationService` 不包含 ADB 输入能力（不提供 `input_tap` / `input_swipe` / `input_text` 等 ADB 输入方法），仅承担设备探测、连接管理与截图获取。此声明是合规化改造的一部分，确保业务层无法被调用方误用为自动化操作入口。

```
MumuConfigDialog
  -> MumuConfigCoordinator.detect_adb() / refresh_devices()
  -> EmulatorOperationService 的后台结果
  -> MumuConfigCoordinator 转发设备、连接和模板截图状态
  -> UI 渲染状态或打开 RoiSelectorDialog
  -> MumuConfigCoordinator.create_template(image, roi, template_name)
  -> OcrService.create_template(image, roi, template_name)
```

`MumuConfigCoordinator` 持有配置草稿、已探测设备和模板截图进行状态；设备刷新失败时视图保留上一次成功的列表与选择。ROI 框选和文件选择保留在 UI 线程，模板保存、运行时 ADB 配置和轮询恢复均由协调器委托服务完成；关闭对话框后协调器停止后台操作，避免迟到回调更新已销毁控件。`shutdown()` 还会显式断开 `CaptureService.connection_changed` → `self.connection_state_changed.emit` 的中继连接（6ea1866）——`signal.emit` 形式的连接没有 QObject receiver，不随对话框销毁自动解除，残留连接会在下次 ADB 连接状态广播时对已销毁的对话框抛 "Signal source has been deleted"。

### 3.5 OfficialDataImportService（官方榜单导入）

该服务处理本地官方榜单图片，不依赖 ADB 或模板匹配。固定版式、横线检测、单元格切分和胜率数字模板算法位于 `src.ocr.official_board_parser`；服务接收 `OcrWorker` 注入的 OCR 引擎，负责识别编排、复核记录与 CSV 持久化。按职责域拆分后的协作模块：武将名纠错与词表消解纯规则在 `name_resolution.HeroNameResolver`（服务与复核对话框共用同一实例），双 OCR 引擎懒加载与罕见字兜底策略在 `official_ocr_engines.OfficialOcrEngines`（worker 经服务的 `ocr_engine` / `rare_char_ocr_engine` property 跨任务移交引擎；B1 起 `main` 与识别管线同源取 v6 主引擎，`rare_char` 只取 v4 复核引擎——`chinese_cht` 繁体兜底链已退役）。目标仍是用视觉行边界确定行，而不是按 OCR 成功数量排列，避免漏识别一个名称后其余排名整体错位。

```
OfficialDataImportDialog
  -> CaptureService.submit_official_import()
     -> OfficialImportGateway.submit(worker, paths)          [审计 G8 后经网关：排他集合与排队提示在此]
     -> OcrWorker.submit(OfficialImportTask)
        -> OfficialDataImportService.import_pages()（按已选图片顺序串行执行）
           -> official_board_parser 读取图片、检测横线并按列比例裁剪单元格
           -> 简体主引擎（v6）；名称歧义时在原候选白名单内用复核引擎（v4） / 胜率数字模板识别
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

**拼接长图兼容（366303c，20261022 版式）**：官方 2026-10-22 起发布"新版头部大图 + N 段分页表格整页首尾拼接"的长图。`import_pages()` 在版式检测前先经 `official_board_parser.split_stitched_image()` 处理——纵横比 ≥4 的图片按静默带切分为补边虚拟分页（切段原理与参数标定见 `module_capture_ocr.md` §3.10），导入侧把 `(原图路径, 补边段图, 段 y 偏移)` 组装为 `page_units` 统一进入原分页链路；行截图的"原图坐标"经 `segment_offset` 还原回原图 y 坐标。切不出多个有效段的旧版长图与分页图仍按整图走原路径，行为不变。

**单格识别快通道（28fa11b，整板导入提速约 4.6 倍）**：`_recognize_cell()` 与名称识别入口在引擎已注入时先经 `_fast_path_cell()` 尝试 **2× 放大单候选**识别（原路径为 4× 双候选，det 输入面积主导耗时，单次引擎调用约 1/4）。采信纪律严格，未过门一律回退原路径，精度下界不变（2026-10 七种版式语料 ×120 格逐格比对零不一致后才启用）：数字格（`_fast_numeric_cell`）要求单行结果、置信度 ≥ `FAST_PATH_MIN_CONFIDENCE = 0.90` 且匹配"纯排名/胜率"格式 `FAST_PATH_NUMERIC_PATTERN`（`\d{1,3}(?:\.\d{2})?%?`）；名称格（`_fast_name_cell`）要求单行结果、置信度达标且经 `chinese_text()` 归一后精确命中武将词表。OCR 结果解析同步收口为 `_parse_ocr_lines()` 静态方法。

**待复核行截图（8c0f871）**：待复核条目的行截图从整行改为**仅保留武将名称格**（`_row` → `_name_cell`）——排名与胜率已有 OCR 值可对照，整行画面无复核价值，名称格截图聚焦人工比对目标。

低置信度、排名 OCR 不一致、胜率模板异常或名称被词表校正仍写入正式 CSV，并写入对应 `*_待复核.csv`（含行截图路径、来源图片、页序号与原图坐标）。未确认名称、重复名称或同规模输出集合不一致属于阻断错误：服务保存复核记录和行截图，但**保留原正式 CSV** 并抛 `ValueError`，同时把整批结果持久化到 `data/official_import_pending.json` 供复核界面修正后直接落盘。

**复核会话（`official_import_pending.json`）**：`_save_pending_session()` 在名称校验失败时写入 `{key, image_paths, page_count, variant, validation_errors, outputs}`（`outputs` 含每个榜单的 `records` 与 `reviews`）。复核界面经 `HeroNameResolver.review_candidates(ocr_name, current)` 取候选（当前值 ∪ OCR 原文 ∪ 距离 ≤ `CANDIDATE_EXPANSION_EDIT_DISTANCE=2` 且共享字符的武将 ∪ 歧义候选，为空时全表按距离排序），修正后调用 `apply_reviewed_records(pending, {(榜单, 排名): 武将名})` 重跑名称门禁——通过则写正式 CSV 并清理会话，不通过则抛错且不写任何文件。`load_pending_session()` / `clear_pending_session()` 为模块级函数，损坏或不存在时返回 `None` / 静默跳过。

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

`boundaries` 由视觉行检测得到，因此 `expected_rank` 来自行序而非 OCR 排名。若相邻边界间距超过中位行高度的 1.5 倍，服务会按常规行高补插边界，并将补插边界后的数据行写入待复核，防止单条横线漏检导致后续排名整体前移。2v2/巅峰赛 胜率格会先向左扩展 ROI，避免截断贴近列线的首位数字；出场榜及放逐榜的排名/武将分界固定为面板宽度的 45%，避免排名数字落入武将 OCR 区域。武将格汇总原图与增强图的 OCR 候选，优先采用精确命中词表的完整姓名；两路精确结果冲突时不按置信度强选。单字结果继续按字形补识别；公共前缀再调用复核引擎（v4）时，精确或编辑距离纠正结果必须属于主引擎 OCR 产生的候选白名单。仍未确认的名称在整榜完成后先做**榜单内部唯一性补全**（排除已占用候选，只有唯一剩余且无竞争时才补全），再做**跨榜单交集补全**（`HeroNameResolver.resolve_names_across_outputs`：各未确认行的扩展候选集交集恰为 1 时统一改名，避免同一名将因左右榜 OCR 差异被误判为"集合不一致"）。最终未知名、重复名或同规模输出集合不一致会阻止正式覆盖。该逻辑仅用于官方导入，不影响常规武将识别。胜率继续由排名格和同列小数位构建字体模板。

**复核阈值**：名称 OCR 置信度 < `NAME_CONFIDENCE_REVIEW_THRESHOLD = 0.75` 标记"武将名称置信度低"；胜率数字模板置信度 < `TEMPLATE_RATE_REVIEW_THRESHOLD = 0.90` 且与 OCR 胜率不一致时标记"胜率OCR与数字模板不一致"（两者语义不同，勿合并）；候选池扩展编辑距离 `CANDIDATE_EXPANSION_EDIT_DISTANCE = 2`，宽松于名称纠错的 1——此处是"扩大候选集供后续复核"，不是直接纠错。

**进度与收尾**：worker 的进度先报 `total_steps`（每行 `2` 步当该列含"胜率"——模板准备 + 行识别，否则 `1` 步），罕见字兜底只更新状态文字（`current = -1` 时仅改状态）。全部写入用 `NamedTemporaryFile` + `replace()` 原子覆盖。正式 CSV 落盘后调 data 层 `notify_official_outputs_written(outputs)`（`recommendation_index_repository`）做跨仓储联动：本批未包含 `巅峰赛胜率排行.csv` 时标记推荐指数待重建（对话框据此发 `recommendation_indexes_stale` 到推荐面板）；包含 `2v2胜率排行.csv` / `巅峰赛胜率排行.csv` 时分别调 `clear_win_rate_cache()` / `clear_peak_win_rate_cache()`。2v2、巅峰赛与放逐图片作为整批任务在唯一 `OcrWorker` 中串行执行，`import_pages()` 返回 `{name, pages, variant, records, reviews, outputs}`。

**名称降级决策顺序：**

1. 收集原图放大与增强锐化两次 OCR 的全部文本块；完整文本精确命中 `heroes.json` 词表时优先采用，不与单字的错误高置信度竞争；两路精确结果指向不同武将时转入歧义兜底。
2. 写入前，完整候选统一通过 `CharacterSimilarityService.correct_hero_name()` 的编辑距离与字形特征二次判定，不因高置信度跳过校正；发生校正时以"武将名称已由词表校正"写入待复核 CSV 和行截图。
3. 若最高候选为单字，按亮色字形切分 2-4 个字符，保留原背景、左右内容与边缘留白后逐字 OCR；拼接结果通过 `CharacterSimilarityService.correct_hero_name()` 校正后必须仍命中词表。
4. 逐字 OCR 未得到可用名称时，只有 OCR 原文在词表中唯一对应一个前缀候选才自动补全。
5. 公共前缀存在多个候选时，按需调用复核引擎（v4）继续确认；复核结果只能在当前候选白名单内精确命中或唯一纠正，不能跳转到其他武将。
6. 复核仍无法确认时保留原文，整榜结束后排除已确认名称。只剩一个未占用候选且没有其他行竞争时自动补全；否则进入跨榜单交集补全，仍不唯一则写入待复核、保存复核会话并阻止正式 CSV 覆盖。

**公共接口：**

| 接口 | 参数 | 返回/信号 | 说明 |
|---|---|---|---|
| `OfficialDataImportService.import_pages()` | `key`, `image_paths`, `progress_callback`, `status_callback` | `{name, pages, variant, records, reviews, outputs}` | 合并同类有序分页，全部校验后覆盖 CSV |
| `OfficialDataImportService.apply_reviewed_records()` | `pending`, `{(榜单, 排名): 武将名}` | `dict` | 人工复核修正后重跑门禁并写正式 CSV；失败抛错且不写文件 |
| `OfficialDataImportService.ocr_engine` / `rare_char_ocr_engine` | - | 已注入/已加载的引擎实例 | 供 OcrWorker 任务前后收回复用（原始值，不触发懒加载；懒加载策略在 `OfficialOcrEngines`） |
| `HeroNameResolver.review_candidates()` | `ocr_name`, `current=None` | `list[str]` | 复核界面候选名（当前值 ∪ 距离 ≤2 ∪ 歧义候选，空则全表按距离排序） |
| `HeroNameResolver.is_known_hero_name()` | `name` | `bool` | 复核界面统计用 |
| `load_pending_session()` / `clear_pending_session()` | `path=None` | `dict \| None` | 模块级读写 `data/official_import_pending.json` |
| `CaptureService.submit_official_import()` | `{类型: 路径列表}` | `OfficialImportTask` | 空选择 / 任务重叠时抛 `ValueError` / `RuntimeError`（门面委托 `OfficialImportGateway.submit()`，排他集合在网关内） |
| `CaptureService.official_import_progress` | `status`, `current`, `total` | 当前榜单的 OCR 工作进度 | 等待队列、胜率模板准备、逐行识别和罕见字兜底状态都会更新；`current < 0` 仅更新状态文字 |
| `CaptureService.official_import_completed` | - | `list[dict]` | 整批任务完成 |
| `CaptureService.official_import_failed` | - | `str` | 整批任务失败原因 |

该顺序能优先恢复低置信度但完整的词表候选，同时避免将"郭""范"等多候选单字或"夏侯""司马"等复姓公共前缀强行改为错误角色。

**复核引擎集成（d88fc2f 引入，B1 起复核为 v4 → cb2b47d 计数熔断）**：官方导入流程与标准 OCR 识别路径共享同一复核引擎。当未决名称经词表校正仍无法唯一确认时，`recognizer.py` 将候选送入复核引擎（`engine_loader.get_recheck_ocr_engine()`，当前为 PP-OCRv4-mobile/ONNX，与主引擎互为异构）做候选内确认，复核通过则直接补全，不通过则走原有的待复核流程。复核引擎由 `MUMU_OCR_RECHECK_ENABLED`（代码默认 False）控制，缺失模型时直接报错熔断不触发联网下载。官方导入侧的引擎加载位于 `official_ocr_engines.OfficialOcrEngines`（`main` 取 `get_primary_ocr_engine()` 与识别管线同源；`rare_char` 只取 `get_recheck_ocr_engine()`，开关关闭或引擎不可用时 `rare_char_failed=True` 保留原结果走待复核——`chinese_cht` 兜底链已退役）。**罕见字计数熔断（TD-A5-001）**：单次瞬时异常（显存不足、单字不支持）只损失当格兜底，经 `note_rare_char_failure()` 累计，连续达 `_RARE_CHAR_FAIL_LIMIT = 3` 才进入熔断；熔断带 `_RARE_CHAR_COOLDOWN_SECONDS = 60.0` 冷却窗口，期满自动恢复重试（失败计数同步清零）。作用域为单个 `OfficialOcrEngines` 实例（每次导入任务新建），不跨任务。原先单次异常即永久熔断会把整批导入的罕见字识别全量降级为待复核。

---

### 3.6 AnnouncementService（公告更新检查）

`AnnouncementService(QObject)` 提供手动"检查公告更新"：`check_now()` 在 `threading.Thread`（`announcement-check`）中执行 `_do_check()`，结果通过内部信号 `_check_done(object)` 回到 GUI 线程，再由 `_finalize_check()` 统一写共享状态、持久化快照并对外广播 `check_finished(object)`（避免 worker 线程与 `mark_applied()` 跨线程竞争及快照文件并发写碰撞）；`is_busy` 防重复点击，`cooldown_remaining` 提供 `CHECK_COOLDOWN_SECONDS = 60` 秒最小检查间隔，主窗口对忙碌/冷却状态弹出提示。`check_now()` 与 `prepare_update_candidates()` 均返回 `bool`——`False` 表示被忙碌或冷却拦截，由调用方提示用户；后台阶段文字经 `progress_changed(str)` 广播。

**冷却闸门收口（d5cceeb）与冷却窗口分立（39ec62b）**：忙碌判定与冷却计时收口到 `common/check_cooldown.CheckCooldown`（TD-A2-001）——公告检查与卡牌检查此前各自持有一份逐行同构的实现，两侧阈值一旦分叉用户看到的"60 秒冷却"会与实际行为不符。闸门内部加锁使"检查忙碌 → 检查冷却 → 置位时间戳"成为原子操作（原为无锁 check-then-act，安全性完全依赖"所有调用方都在 GUI 主线程"这一注释约定；一旦从 worker/定时器触发，双击即可起两个线程并发写同一份数据文件，VUL-B3-001）。`check_now()` 与 `prepare_update_candidates()` 均经 `_gate.try_acquire()` 判定。2026-10-24 起两个动作**各持独立冷却窗口**：公告检查走 `_gate`，获取武将变更走 `_prepare_gate`（同为 `CHECK_COOLDOWN_SECONDS = 60` 秒）——公告检查后首次获取武将变更不再被牵连拦截，各自连续触发才限频；忙碌判定两闸门共用（任一在途即忙），对外暴露 `cooldown_remaining` 与 `prepare_cooldown_remaining` 两个只读属性。

**忽略名单名字兜底（801c9f1）**：`ignore_hero()` / `list_ignored_heroes()` 的条目键改为武将名（原 entry_id 在官网改版后可能漂移）。

一次检查 = 拉公告（`fetch_latest_announcements`）→ 章节标题分类（`classify_hero_related`）→ `AnnouncementManager.merge_new` 去重落盘 → 拉百科（`fetch_baike_heroes`）→ 内容哈希 diff（`build_hero_snapshot`/`diff_heroes`）→ **忽略名单过滤**（`baike_ignore_store.filter_ignored` 扣除已忽略差异，`ready` 判定一致压制）→ `mark_ready_if_updated` → `_sync_timeline` 武将变更时间轴同步。公告或百科拉取异常只写入 `result.error` / `baike_ok=False` 并记日志，不覆盖旧快照、不中断应用。

**阶段令牌（`_last_snapshot` / `pending_saves`）**：worker 线程计算的百科快照不直接写盘，而是通过 `AnnouncementCheckResult.snapshot` / `pending_saves` 字段带回到 `_finalize_check()`，由 GUI 线程统一持久化并更新 `_last_snapshot`。`mark_applied()` 在"更新武将数据"完成后由主窗口调用：公告置已处理，并把 `_last_snapshot` 写回快照使差异归零。**忙时跳过（801c9f1）**：`mark_applied()` 在检查/候选准备进行中直接跳过——此刻推进会与 worker 的公告状态推进及快照读写并发（横幅保留，用户可在下次检查后重试）。

**首次基线本地化（801c9f1 首跑语义修正）**：首次启用百科基线时优先用本地 `heroes.json` 初始化，避免官网快照被用作基线从而掩盖本地缺失（否则 diff 恒空、新增/调整永远不会提示）。若本地数据文件存在但加载为空则不写快照并记录警告；全新安装（无任何本地武将数据文件）则以当前百科为基线不提醒。**首跑判定扩展**：`_is_first_run()` 不再只看文件是否存在——文件存在但加载报错且内存为空（内容不可用，如损坏后留底）也按首跑处理；此时若按非首跑处理，全部历史公告会被当作新公告重新入库并覆盖写回，用户此前"已处理"的进度与历史记录一并丢失。「内存为空」不可省：`load_issues` 只在 `load()` 时清空而 `load()` 仅启动调一次，`merge_new` 跳过坏爬虫记录记的 error 与损坏恢复重建后的过期 error 都会残留在列表里——只看 error 会把同会话后续检查全部误判为首跑，新公告被静默吞掉。

**快照落盘兜底（801c9f1）**：`_finalize_check()` 对 `pending_saves` 逐条 `try/except` 而非只捕 `OSError`——收尾一旦中断，`check_finished` 不会发射，进度条与"正在检查"状态将永久停留；`mark_applied()` 的 `save_baike_snapshot` 同样包 try/except，否则抛错会冒泡到 Qt 槽，其后的横幅刷新/对话框刷新/成功提示全部被跳过，用户只看到"没反应"。

**`collect_base_candidates()` / `prepare_update_candidates()`**：提供"更新候选准备"接口，两者都由调用方在 GUI 线程传入 `local_heroes_plain` / `announcements` / `diff` 只读快照，后台线程不再访问主窗口可变状态。`collect_base_candidates()` 纯内存、无网络，供 UI 预判是否有可更新项；`prepare_update_candidates()` 在独立 `_prepare_thread`（`announcement-prepare-update`）中拉取官网百科并计算字段级差异候选，完成后发 `update_candidates_prepared({"candidates", "official_ok", "error"?)}`——异常被兜底为 `official_ok=False` + `error`，避免 UI 进度条永不消失。两者与 `check_now()` 共用 `is_busy`（任一在途即视为忙碌）防并发。

**`_sync_timeline()`**：将 hero_related 公告的武将变更同步到时间轴（`append_announcement_events`），幂等、按 `ref/(date, hero)` 去重，全量扫描而非仅本批新增，重复检查可补齐此前同步失败的记录；失败仅记日志并返回 0，不中断检查。

**忽略名单（`baike_ignore_store` 集成）**：

- `ignore_hero(entry_id, name, state, content_hash)` — 把一名武将的当前差异写入忽略名单（`heroes` 命名空间）
- `ignored_hero_count()` / `list_ignored_heroes()` — 查询忽略名单条数与全量条目
- `restore_heroes(entry_ids=None)` — 恢复被忽略的武将差异（`None` 时全部恢复）
- `_do_check()` 在 `diff_heroes()` 之后调用 `filter_ignored()` 过滤差异，`AnnouncementCheckResult.ignored_count` 记录被过滤条数供 UI 显示"已忽略 N"
- `collect_base_candidates()` / `prepare_update_candidates()` 均传入 `ignore_entries` 参数，候选列表同样压制已忽略条目
- 忽略名单文件由 `baike_ignore_store` 模块管理（`load_baike_ignores` / `ignore_entry` / `remove_entry`），CardSyncService 也共享同一基础设施

### 3.7 巅峰赛识别循环（peak_select_watcher.py）

`PeakSelectWatcher` 独立 QObject，与标准轮询并存。标准轮询挂起/恢复由 watcher 内部自动协调。

```
Tick（每 1.5s）→ _thread_lock 非阻塞 → _do_work() 后台线程（session = SessionGuard.current() 快照，过期旧拍在写入点前放弃）
  ├─ CaptureService.capture_for_poll() 截图
  ├─ detect_selection_cards() → None → _handle_board_absent()
  │   └─ miss_ticks++ → BOARD_EXIT_TICKS=4 后 _clear_session_state(keep_miss_ticks=True)，match_guide 保持挂起并发 board_exited（衔接激活归 PollCoordinator.handle_peak_exit）
  ├─ 检出牌面 → miss_ticks 归零 + _suspend_standard_tasks() 挂起 hero_selection/match_guide（幂等重挂）
  │   └─ _restore_missing_cards() 漏检补齐 + layout_is_plausible() 排布可信校验（不可信拍跳过不发）
  ├─ board_signature(cards) 生成原始 bbox 元组签名
  │   └─ board_signature_equal() 逐卡容差判等 → 牌面未变化，沿用结果；扣住牌面在此复现即补发布
  ├─ 新牌面 → _recognize_board() 提交 OcrTask，15s 超时
  ├─ _refresh_resolutions() 逐拍验证人工确认存续（闭包/读数指纹/重排迁移）
  └─ 发布决策 → 卡数高于上一发布拍扣进 _deferred_board 复核（疑似过场坏帧）；正常 → _publish_pool() → parse_pool() → PoolSnapshot → pool_updated
```

会话世代由 `SessionGuard`（`src/business/recognition/session_guard.py`，4542244/P0-1 收口，原裸 `_session` int）守卫：`start()` / `stop()` 各于状态锁内 `begin()` 开新世代，`start()` / `stop()` / 牌面退出三处会话边界统一走 `_clear_session_state(keep_signature, keep_miss_ticks)` 清空牌面上下文（新增会话字段只改一处）。**过场坏帧扣住复核**（036d0ae）：选将/禁选期真实池子单调不增，发布前发现新拍卡数严格高于上一发布拍即判疑似过场坏帧（2026-10-09 场景切换期 raw 截图返回旧 9 张帧致面板候选 8→9→8 跳变），扣进 `_deferred_board` 不发布；紧邻下一拍签名原样复现→补发布，签名不同→按坏帧丢弃，牌面缺席拍作废扣住状态（复核窗口只认紧邻下一拍，坏帧隔拍重现须重新扣住）；卡数下降（禁转选 14→9）与同数重排不受影响照常立即发布。完整规则与测试见 [`./module_peak_combos.md`](./module_peak_combos.md)。

- **PoolSnapshot**：`card_count / names / pending / stage("ban"/"pick") / overlap / banned`
- **图片导入** `recognize_image_file()`：独立 `_import_lock`，不影响循环签名与挂起状态；**导入前校验旧确认**（48b0f99）——`verified_resolutions_for_import()` 与实时循环共用定位语义（名在闭包 / 原文复现 / 唯一迁移），但导入是一次性快照、没有后续拍可宽限，无法定位的旧确认立即丢弃并提示"图片识别完成（N 条旧人工确认与该牌面不符，已丢弃）"
- **`stage` 判定**：≥12 张 = "ban" 禁选阶段；8~11 张 = "pick" 候选阶段
- **牌面签名** `board_signature()` / `board_signature_equal()`：原始 bbox 元组 + 逐卡容差比较（位置 ±8px、尺寸 ±16px），吸收卡面 idle 浮动动画实测漂移 2 倍幅度，消除 round 量化边界翻转致同板反复全量 OCR（a01836d）

**2026-09 巅峰赛优化（e39b746）**：
- **兜底人工确认不再随拍重置**：人工确认结果在单拍闭包缺名时进入宽限期保留（不因拍面变化而丢弃），确认优先于自动结论。
- **逐拍内容验证加宽限淘汰**：确认后的候选名在后续拍中持续比对牌面内容指纹，连续多拍验证不到才淘汰。宽限期确保单拍牌面抖动或漏识别不会直接清除已确认候选。
- **读数原文指纹兜底**：当 OCR 识别出的牌面内容与预期不符时，以原文指纹作为兜底证据稳定错读，避免在宽限期内因单次误读导致候选被错误淘汰。

**2026-09 巅峰赛选将人工确认残留修复（48b0f99）**：
- **停止识别后清空会话状态**：`stop()` 在状态锁内调 `_clear_session_state(keep_signature=True, keep_miss_ticks=True)` 清空确认表 `_resolutions` / 读数指纹 `_resolution_raws` / 失验计数 `_stale_rounds` / 禁将基线 `_ban_names` / 牌面板引用 `_last_board`（P0-1 起与 `start()`、牌面退出共用同一清空方法；停止后无拍运行，保留签名与缺席计数无差异）——槽位号是跨牌面不稳定键（选将板与禁将板同槽位并非同一张牌），残留确认会被后续图片导入按槽位号盲目套用，曾把禁将板的卓文君顶成选将板确认的孙尚香（2026-09-30 事故）。
- **`confirm_pending()` 牌面在位守卫**：无在识别中的牌面（`_last_board is None`）时拒绝确认并提示"牌面已不在识别中，确认未生效"，杜绝退出/停止后残留待确认行被误点写入旧确认。
- **`_handle_board_absent()` 同步清理**：牌面连续 `BOARD_EXIT_TICKS=4` 拍缺席时清空确认表/读数/失验/禁将基线/牌面板引用，与 `stop()` 对称。
- **面板同名槽位显性告警**：`peak_select_panel.py` 候选池出现重复武将名时写日志 + 追加识别日志 + 状态栏 warning 提示复核（TONE_WARNING），不再静默去重隐藏"14 张牌只显示 13 个名字"；会话结束（停止/牌面退出）摘除待确认行防误点，卡片保留供复盘。

**2026-10 巅峰赛修复（62c5c23 / 8020da8 / 67a67ef）**：
- **缺席阈值放宽至 4 拍**（`BOARD_EXIT_TICKS = 4`，约 6 秒）：禁将揭晓/转场会让牌面整块不可见（2026-10-12 实测转场连续 2 拍无卡状组件），2 拍阈值在该局误退出 3 次（20:46/20:50/20:57），每次都要手动重开识别；真实离开页面会持续无牌面，放宽到 4 拍只让后续衔接晚约 3 秒。
- **漏检补齐与排布可信校验**：`_restore_missing_cards()` 在本拍少卡且存活卡逐张原位时按上一拍卡位补齐（禁将揭晓动画把相邻两卡焊成一块会让本拍只认回部分卡，2026-10-12 11 张盘面检出 9 张致面板跳变）；`layout_is_plausible()` 校验排布是游戏可能出现的形状（如 9 张成 5+4 即不可信），不可信拍既不发布也不计入缺席，面板保持上一拍内容。
- **退出衔接收敛（67a67ef）**：`match_guide` 牌面退出后保持挂起，衔接激活由主窗口经 `board_exited` 转交 `PollCoordinator.handle_peak_exit` 负责（任务状态的写者收敛一处）。

完整规则与测试见 [`./module_peak_combos.md`](./module_peak_combos.md)。

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

`estimate_generation_cost(items, kind, model=None, use_rag=None)`：UI 经本模块估算成本，不直接依赖采集层；估算规则变更时 UI 无感知。攻略用 `estimate_cost`，相性用 `estimate_item_cost`（含 RAG 预算影响）。**口径修正（87eac8d）**：`use_rag=None` 时按默认口径（RAG 语料注入，与对话框默认勾选一致）显式置 `True`——底层 `if use_rag` 会把 `None` 按 `False` 结算成经典口径；`kind` 不在 `("guide", "synergy")` 时抛 `ValueError`，不静默降级。

### 3.10 实战配队导入（combo_import_service.py）

`run_import(source, heroes, output)` 幂等合并。武将名→ID 映射，未匹配项进报告；座次解析 + position 交叉校验；手工记录优先；非手工旧记录源中已不存在则移除；重复执行输出稳定。报告字段见 `module_scraper.md` 3.6 节。

**2026-09 导入合并保护增强（4fa9a5d）**：
- **手工记录优先保留**：`manual=True` 的记录在同 key（武将对 + 座次）冲突时优先于源记录保留，确保用户手动维护的配队不被外部导入覆盖。
- **逻辑删除无条件保留**：`deleted=True` 的记录在导入合并时不可被源数据覆盖或恢复，同时屏蔽同 key 的源记录写入。已逻辑删除的配队即使出现在外部数据源中也不会被复活。

**2026-10 数据安全加固（3a8845f / 062071a）**：
- **源结构校验**：`_load_source_combos()` 要求源文件顶层为对象（含 `combos` 数组）或数组本身，结构非法直接抛 `ValueError`（带路径），不进入报告；`_load_hero_name_map()` 要求 heroes.json 顶层为对象含 `heroes` 数组或数组本身，映射为空时抛 `ValueError`——空映射若放行，全部源记录会落 unmatched、既有导入记录全判 stale 而被清空，且界面仍显示"导入完成"。
- **输出完整性守卫**：输出文件加载存在 error（`--output` 误指其他数据文件、或混有坏记录）时抛 `ValueError` 中止，禁止在残缺集合上覆盖保存。
- **字段校验失败视同源中存在**：源记录字段非法不代表配对已从源中消失，保留同 key 既有记录（`kept_invalid_keys`），不被判为 stale 而移除。
- **覆盖前快照**：`manager.save()` 前调 `snapshot_to_backups(output_path)`——combos.json 已出库无版本历史，误选源文件/合并缺陷凭 `backups/` 回退。

### 3.11 脚本运行器（script_runner.py）

`ScriptRunner(QObject)` QProcess 异步执行 Python 脚本公共封装（自 `ui/shared/widgets` 迁入）：

```python
class ScriptRunner(QObject):
    output = Signal(bytes)
    finished = Signal(int)

    def run(self, python: str, script: Path | None, args: list[str], working_dir: Path) -> bool:
        """启动脚本；已有任务运行时返回 False。

        返回 True 表示已受理并启动成功，此后进程结束时必收到一次 finished(code)。
        （Linux 上启动失败是异步判定的：先返回 True，随后收到一次
        finished(FAILED_TO_START_EXIT_CODE)，同样兑现"必收到一次 finished"。）
        返回 False 表示本次未启动（已有任务在跑，或启动失败），此时不会发
        finished——调用方应据此自行复位自己的忙碌状态。
        """
        ...
```

- `is_running()` 防并发（同一时刻只允许一个任务）
- `output(bytes)` / `finished(int)` 信号
- `run(python, script, args, working_dir)` 启动；已有任务返回 False；`script=None` 时 `args` 需自带入口参数（如 `["-m", 模块名]`）
- **看门狗（b3327f1 → f1b13f8）**：`DEFAULT_WATCHDOG_MS = 30*60*1000`，`__init__` 接受 `watchdog_ms` 参数；`run()` 启动子进程后开启 QTimer 单发定时器；超时回调记 ERROR（含超时秒数与命令行参数）后 `_kill_process_tree(proc)` + `_schedule_recheck(proc)`（`WATCHDOG_RECHECK_MS = 2000` 延迟复查）；`_on_finished`/`_stop_watchdog` 停表清理。`_stop_watchdog` 对尚处 `Starting` 的进程挂 `started` → `_on_started_after_watchdog`，等进程真正起来后补树杀——Qt6 对 `Starting` 状态 `kill()` 会在事件循环内原生 abort。
- **启动失败终态通知（14bffaf / 07606ce，VUL-B2-001）**：`errorOccurred` 连接 `_on_error_occurred`，`FailedToStart` 时 Qt 不发 `finished`（Qt6 实证），必须在此清理状态，否则僵尸 QProcess 与看门狗会残留（看门狗到期会误杀后续健康任务）。是否补发 `finished` 取决于时序：Windows 的 `FailedToStart` 在 `start()` 内同步触发，此时 `run()` 尚未返回，用返回值 False 表达失败、不发 `finished`；Linux 上错误由事件循环异步回报，`run()` 已返回 True，必须补发 `finished(FAILED_TO_START_EXIT_CODE = -1)` 兑现"返回 True 必收到一次 finished"的契约，否则调用方的忙碌状态永远无法复位。
- **树杀与复查（2b73535）**：`_kill_process_tree()` 在 Windows 上用 `taskkill /PID <pid> /T /F` 终止整棵进程树（子脚本会再拉起浏览器/同步脚本，只杀主进程会把它们孤儿化）；`_schedule_recheck()` 延迟复查绑定具体 proc（超时进程若在延迟窗口内结束、调用方紧接着启动下一个任务，读 `self._proc` 会拿错对象并误杀新进程）。
- **状态提交时序**：`_proc` / `_watchdog` 必须先于 `start()` 提交——`FailedToStart` 在 Windows 上于 `start()` 内部同步发出，若此时状态尚未就位，`_on_finished` 的清理会全部落空，留下僵尸 QProcess 和一个永不停止的看门狗（后者到期时会 kill 掉当前正在跑的另一个健康进程）。
- `_on_finished(code, notify=True)`：置 `_proc = None` 并 `proc.deleteLater()` 后发 `finished(code)`——先清引用再广播，保证 `finished` 回调里再次 `run()` 不会被 `is_running()` 误拦；`notify=False` 用于启动失败路径（那一次 `run()` 尚未返回，用返回值表达失败即可）。

业务层（RuleDocOpsService）与 UI 均可使用，仅依赖 QtCore，无 UI 控件依赖。

### 3.12 卡牌图鉴服务（card_catalog.py）

`CardCatalogService` 跨仓储视图组装，合并基础卡牌（`cards.json`）、字段定义（`card_field_schema.json`）与追加内容（`card_annotations.json`），不依赖 Qt。三个仓储的 `load()` 返回 `DataIssue` 列表，追加内容引用未知卡牌 ID 记为 `orphan_annotation` 警告，追加字段值类型不匹配记为 `invalid_field_value` 警告。

**核心方法**：

- `load_all()` — 加载三个仓储并校验追加内容引用完整性
- `list_views(keyword, card_type, adjustment)` — 组装视图列表（`CardViewModel`），支持关键词搜索、类型筛选、加强/削弱/活跃/待办四态过滤，保持 cards.json 类型分组和同组基础 ID 顺序
- `get_view(card_id)` — 获取单张卡牌视图，含字段值校验（`effect_entries`/`markdown`/`tags`/`boolean`/`number`/`select` 六种类型）
- `save_annotation_fields(card_id, fields)` — 校验字段类型、必填规则后写盘
- `add_field()` / `update_field()` / `archive_field()` — 追加内容写编排

**editable 守卫**：`self.editable = base_available and schema.available and annotations.available`，编辑前检查，不可用时抛 `ValueError`。

**2026-10 修复（87eac8d / 1dc08d5）**：
- **告警累积修复（VUL-B4-002）**：`get_view()` 读路径不再追加 `load_issues`——`list_views` 每次刷新都逐卡调用 `get_view`，追加会让告警无界增长；无效字段的处理是跳过展示，留 `debug` 日志即可排障。
- **注解保存回滚（1dc08d5）**：`save_annotation_fields()` 先快照再改内存，写盘失败经 `_save_or_rollback(snapshot)` 回滚——否则 UI 弹"保存失败"后，切走再切回会从内存读到"已保存"的幻影内容，用户无法判断哪份是真相。

### 3.13 推荐与对局摘要组装（analysis/）

两个纯数据服务，不依赖 Qt，供选将推荐页与对局攻略页读取快照。

`RecommendationService` 把胜率和推荐指数两条持久化数据聚合为一次刷新所需的 `RecommendationData(win_rate_percent, indexes, indexes_stale, load_error)`，构造参数全部可注入（`load_win_rates` / `load_recommendation_indexes` / `refresh_recommendation_indexes` / `is_recommendation_index_stale` / `mark_recommendation_index_stale`）。三个方法：

- `load()` — 只读快照，附带指数是否过期标记；索引读取失败（`OSError`）记入 `load_error` 而不上抛，使界面能区分"读不到"与"确实没数据"；
- `rebuild_indexes()` — 重建推荐指数后**回读**真实过期状态，不自行清标记（标记的清除归数据层 `refresh_recommendation_indexes`，仅在其写入规范快照路径时执行）；
- `mark_indexes_stale()` — 官方榜单导入后标记指数待重建（不经此服务也能由 `mark_recommendation_index_stale(True)` 直接触发）。

**量纲**：`win_rate_percent` 为 0–100 百分数，与 `RecommendationIndex.win_rate` 的 0–1 比率量纲不同，勿混用（混用会静默差 100 倍）。

**胜率名次**统一由 `win_rate_ranking.derive_win_rate_ranks(win_rates, names=None)` 推导：`names` 为 None 时全表排名，给定候选集时只在集内排名（选将推荐页的奖牌只比较本局已识别的武将），同分按武将名稳定排序。推荐页的金/银/铜牌（`MEDAL_LIMIT = 3`）由 UI 侧据此组装，业务值对象不再承载槽位语义。

`MatchAnalysisService(guide_manager, win_rates)` 基于本地攻略与单将胜率生成已确认 2v2 阵容的离线摘要 `MatchAnalysis`，无网络、无 LLM：

- `priorities` — 敌方按胜率降序取攻略中的 `counter_strategy`，上限 `MAX_PRIORITIES`（2v2 下恒不触发）；
- `threats` — 敌方每人的 `key_points` 前 `MAX_ENEMY_THREATS`（2）条；
- `ally_tips` — 我方每人的 `key_points` 前 `MAX_ALLY_KEY_POINTS`（1）条与 `tips_for_beginners`（敌我深度不对称是刻意的）；
- `missing_data` — 收集"暂无攻略"、"暂无历史单将胜率"，以及要点被截断时的"仅展示前 N 条（共 M 条）"，逐条列出；
- 每条提示都带 `source_field`（`counter_strategy` / `key_points[i]` / `tips_for_beginners`）供界面标注来源。

### 3.14 CardSyncService（卡牌百科同步）

`CardSyncService(QObject)` 提供手动"检查卡牌更新"，与 `AnnouncementService` 共享同一设计模式（`_run_check` → `_check_done` → `_finalize_check` 三段式跨线程收尾）：`check_now()` 在后台线程执行检查，受 `CHECK_COOLDOWN_SECONDS=60` 秒最小间隔限制。与公告检查零耦合：不依赖公告时间轴，变更记录独立存储在 `data/card_changes.json`，驱动卡牌 curated 精化时效检查（见 [`./module_rag.md`](./module_rag.md)）。

**冷却闸门收口（d5cceeb）**：忙碌判定与冷却计时收口到 `common/check_cooldown.CheckCooldown`（TD-A2-001），与公告检查共用同一默认值；`check_now()` 经 `_gate.try_acquire()` 原子判定。

**信号**：

| 信号 | 参数 | 说明 |
|------|------|------|
| `check_started` | - | 检查开始 |
| `check_finished(object)` | `CardSyncCheckResult` | 检查结果（`diff` / `official_ok` / `ignored_count` / `error` / `snapshot` / `pending_saves`） |
| `status_changed(str)` | `str` | 状态栏文字 |
| `progress_changed(str)` | `str` | 后台阶段文字 |
| `_check_done(object)` | `object` | **内部信号**：后台线程完成 → GUI 线程做收尾（缓存官网数据 + 持久化首跑基线） |

**`CardSyncCheckResult` 结构**：`diff`（`{added, modified, removed}`）、`official_cards`、`official_ok`、`error`、`snapshot`（官网快照）、`ignored_count`（被忽略名单过滤掉的差异条数）、`pending_saves`（首跑基线初始化产物，GUI 线程统一落盘）、`warning`（收尾阶段非致命问题，如基线落盘失败；None 表示无）。

**工作流程**：

```
check_now() -> bool
  └─ [threading.Thread] _run_check()
       └─ _do_check()
            ├─ fetch_official_cards()                     → 官网卡牌原始数据
            ├─ build_card_snapshot(official_cards)        → 官网快照
            ├─ load_card_snapshot(snapshot_path)          → 本地基线快照
            ├─ [基线空] 首跑初始化
            ├─ diff_cards(snapshot, baseline)             → {added, modified, removed}
            ├─ filter_ignored(diff, hashes, ignores)      → 扣除已忽略条目
            └─ 返回 CardSyncCheckResult
       └─ _check_done(result) 内部信号
            └─ [GUI 线程] _finalize_check()
                  ├─ 缓存官网数据到 _last_official_cards / _last_snapshot
                  ├─ 持久化 pending_saves（首跑基线初始化）
                  └─ check_finished(result)               → 广播到 GUI
```

**首跑基线初始化**：

- 本地 `cards.json` 有数据时，用本地初始化基线，避免官网快照掩盖本地缺失；
- 全新安装（本地无任何卡牌数据）以当前官网数据为基线；
- 本地文件存在但为空则不写快照并记录警告。
- 首跑初始化产物通过 `pending_saves` 带回 GUI 线程统一落盘，避免 worker 线程与 `_last_snapshot` 写操作竞争。

**`apply_updates(modified_ids, added_ids)`**：

1. 检查前置条件：`is_busy`（拒绝并发）+ `_last_official_cards` 非空 + 基础库无 `error` 级加载问题（文件损坏/坏记录时内存库可能残缺甚至为空，继续应用会用残缺库覆盖 `cards.json`，VUL-B4-002）；
2. 写回 `cards.json`——更新卡牌字段（`card_amount` 保留，不随官网覆盖）；
3. 按卡牌增量更新基线快照（新增加入、修改更新、移除删除）；
4. **先追加 `CardChangeRecord` 再落基线**（87eac8d）：基线一旦推进 diff 即消失，变更记录若后写失败将永久缺失（精化时效检查失真）；此序下任一步失败都只是下次检查重复提示，重新应用即可补齐。变更记录写入失败时基线不推进，抛 `RuntimeError` 提示重新应用。
5. 返回 `{"applied": N, "modified": N, "added": N}`。

**`_finalize_check` 基线落盘兜底（87eac8d）**：`pending_saves` 落盘失败计数，全部失败时写 `result.warning` 让对话框显式提示——基线始终为空时每次检查都会重走"首跑初始化"分支用当时的本地 `cards.json` 重建基线，diff 恒空，官网真实变更被逐次就地吸收，用户只会觉得"官网好久没更新"。

**忽略名单（`baike_ignore_store` 集成）**：

- `ignore_card(card_id, name, change)` — 把一张卡的当前差异写入忽略名单（`cards` 命名空间，哈希与官网快照同源）
- `ignored_card_count()` / `list_ignored_cards()` — 查询忽略名单条数与全量条目
- `restore_cards(entry_ids=None)` — 恢复被忽略的卡牌差异（`None` 时全部恢复）
- `_do_check()` 在 `diff_cards()` 之后调用 `filter_ignored()` 过滤差异，`CardSyncCheckResult.ignored_count` 记录被过滤条数供 UI 显示"已忽略 N"

**`is_busy` 防重复**：`check_now()` 返回 `bool`，`False` 表示被忙碌或冷却拦截。主窗口据此弹出提示。

### 3.15 白名单治理与未决错法记录（pending_stats.py）

`pending_stats` 模块提供 OCR 未决错法频次统计与人工确认答案收集服务（9ca1b91）。双事件流汇入同一文件 `data/ocr_name_pending_stats.json`：

- **识别线程** — `record_pending(original_name, candidates, slot)`：OCR 识别未决时记录原始错法名、候选列表与槽位，60 秒节流窗口防虚增（同一错法在节流窗口内不重复计数），原子替换写入。
- **GUI 线程** — `record_confirmation(pending_name, confirmed_name)`：用户人工确认候选时记录确认答案，用于白名单观察清单生成时统计"最终正确名 → 错法名"的映射频次。

`PendingStatsService` 提供以下核心方法：

| 方法 | 说明 |
|------|------|
| `record_pending(original, candidates, slot)` | 识别线程调用，记录未决错法 |
| `record_confirmation(pending_name, confirmed_name)` | GUI 线程调用，记录人工确认 |
| `load_pending_stats()` → `dict[str, PendingRecord]` | 加载全量错法记录 |
| `get_sorted_pending_records(threshold=0)` → `list[PendingRecord]` | 按频次降序获取错法清单（A+/A/B/C 分类由 UI 层处理） |
| `clear_stats()` | 清空统计文件 |

`PendingRecord` 包含 `original`（原文）、`candidates`（候选）、`pending_count`（出现次数）、`confirmed_count`（确认次数）、`confirmed_names`（确认答案列表）。错法观察清单的 A+/A/B/C 分类由 `whitelist_config_dialog.py` 按频次与确认状态计算，业务层只提供数据。

### 3.16 知识库相关功能（已迁出）

元规则维护、知识库审计、索引精化、RAG 语料任务定义、武将分类 LLM 建议已整体迁至 [`./module_rag.md`](./module_rag.md)，此处不再重复。

### 3.17 ProgressReporter（进度出口统一组件）

`ProgressReporter`（`src/ui/app/progress_reporter.py`）是状态栏消息文本与业务进度条的唯一渲染出口，自 `MainWindow` 抽取。业务服务只调 `show_*` API 或把服务信号直接连接到 API，不直接操作状态栏控件；常驻服务状态 chips 在 `status_chips.StatusChips`，与本部件互不覆盖。

**核心方法**：

| 方法 | 说明 |
|------|------|
| `show_message(text, duration_ms=None)` | 渲染全局消息文本；计时消息到期后回落 `set_default_message` 设置的默认文案 |
| `show_event_message(text)` | 完成/事件类消息，停留 5 秒后回落 |
| `show_indeterminate(text)` | 不确定进度（动画），用于联网阶段 |
| `show_progress(current, total, text)` | 确定进度（子进程 `[n/N]` 阶段） |
| `set_progress_text(text)` | 仅更新进度条文字 |
| `hide_progress()` | 隐藏进度条（消息文本保留） |
| `set_default_message(text)` | 记录默认统计文案；消息显示期间只暂存不覆盖 |

与公告/卡牌检查服务的 `status_changed` / `progress_changed` 信号配合，由主窗口统一转发至本组件。

### 3.18 竞态守卫原语（session_guard.py）

`session_guard.py`（81 行，无 Qt 依赖）把全仓 ≥5 次同类竞态 fix（faac3c0/93b54ad/8374acc/e6d67c0/c0f2143/634e027）年代各模块自造的令牌/世代/忙碌守卫（裸 int 比对、裸 bool 旗标）定稿为三个命名原语（8a08d5d，P0-3），调用方按语义显式选择，替换时不得顺手改语义（语义变更 = 行为变更，超出重构范畴）：

| 原语 | 语义 | API |
|------|------|------|
| `SessionGuard` | 世代计数器：生命周期边界（启动/停止）开新世代作废在途工作 | `begin()` 世代 +1 作废旧 token；`current()` 取快照；`is_current(token)` 校验。单次读写依赖 GIL 原子性，调用方在自有锁内或单线程（Qt 主循环）使用 |
| `BusyGate` | 忙碌闸：单飞标记，忙时拒绝新请求 | `acquire()` 锁内检查 + 置位原子（多线程争用下至多一个占用者），忙时返回 False；`release()` 持有者解除占用（幂等）；`reset()` 外部作废占用（幂等，供停止/重启/作废在途等生命周期边界——调用方声明"占用者不是我"，在途持有者不会走到自己的完成路径，不 reset 会把后续请求永久挡在忙态）；`is_busy` 属性。**release 与 reset 语义分立（9ccc2fc，TD-A9-001）**：此前任何线程可无差别 release，语义混同，分立后"谁在释放"可被逐一审计 |
| `OneShotToken` | 一次性令牌：关闭/停机类只进不退的守卫 | `mark()` 幂等置位；`spent` 属性；置位不可复位 |

全仓五个消费点（均为语义不变的纯收口）：

- `ocr_service._poll_gate`（BusyGate，89e4e8d，原 `_poll_in_flight` 裸 bool）— 见 3.3
- `announcement_update_coordinator._phase_gate`（BusyGate，76875dd，原 `_phase_in_flight`；UI 层公告更新阶段推进，见 [`./module_ui.md`](./module_ui.md)）
- `capture_service._shutdown_token`（OneShotToken，118f59c，原 `_closed` 裸 bool）— 见 3.2
- `emulator_operation_service._shutdown_token`（OneShotToken，df019a5，原 `_closed` 裸 bool）— 见 3.4
- `peak_select_watcher._session_guard`（SessionGuard，4542244，原 `_session` 裸 int）— 见 3.7 与 [`./module_peak_combos.md`](./module_peak_combos.md)

语义单测 `tests/test_session_guard.py`（5 用例）：世代 begin 作废旧 token、BusyGate acquire/release 循环、release 幂等、8 线程争用单赢家、OneShotToken 置位后不可复位。

### 3.19 数据新鲜度判定（freshness.py）

`freshness.py`（无 Qt 依赖纯业务层）是状态栏数据新鲜度芯片（`src/ui/app/data_freshness_chip.py`）的业务逻辑核心，判定四类监测对象的健康度（ae92c1d）：

- **武将 heroes.json**：逐事件按武将核对——事件落后 ⇔ 该事件武将在 heroes.json 缺失，或该武将 `last_updated` 早于事件日期；批次按公告 ref 去重（无 ref 每条计一批）。这修复了旧"全局 max 对比"的缺陷：指定获取任一武将即可洗绿、混合批次只推增量致已存在且过期的武将永远漏掉、部分覆盖洗绿、部分攻略缺失未暴露。时间轴不健康（缺失/损坏/超过 `DATA_FRESHNESS_TIMELINE_STALE_DAYS` 天未更新，配置键透传，默认 14）时退化为全局 max 纯年龄兜底并产出 `timeline_warning` 菜单警示行。
- **攻略 guides.json**：逐武将（hero_id）对齐武将数据；攻略是全覆盖物，某武将无攻略 = 未生成缺口（黄档）。
- **相性 synergies.json**：逐对对齐两侧武将数据；相性是精选生成（无全覆盖预期），无 pair 的武将不计任何档位，孤儿 pair（引用不存在武将）忽略。
- **官方榜单 6 CSV**（`RANKING_CSV_PATHS`：2v2 胜率/出场、巅峰赛胜率/出场、武将放逐、武将推荐指数）：纯 mtime 年龄取最旧，任一缺失整项红。

按天计量的档位 `[0,绿) 绿 / [绿,红) 黄 / ≥红 红`。模块级常量 `STALE_GREEN_DAYS=7` / `STALE_RED_DAYS=14` 为默认值，运行时经 `_stale_thresholds()` 读 `config.env` 的 `DATA_FRESHNESS_STALE_GREEN_DAYS` / `DATA_FRESHNESS_STALE_RED_DAYS` 覆盖（1dc08d5，TD-A6-001）：官方榜单约每半月更新，写死 14 天会让榜单在多数时间停在黄/红并盖掉武将信号。红档必须大于绿档，否则 `[绿,红)` 为空区间、黄档永不出现；配置写反时整体回退默认值而非夹逼，避免产生"看着生效实则半套"的混合阈值。周期内小幅落后计绿并在 detail 注明。

**损坏文件留底（1dc08d5）**：`_load_records()` 解析失败先按原字节留底 `backups/`（`preserve_corrupt_file`，与 hero_timeline / baike_ignore_store / card_sync_store 同款保底）——本函数只降级返回 None，若用户随后重新生成或重新抓取，损坏文件会被直接覆盖，没有副本即"读不出来的数据"升级为"不可逆丢失"。`FileNotFoundError`（从未生成）属正常状态，不留底。

`compute_freshness_report()` 返回 `FreshnessReport(items, state, headline, timeline_warning, suggested_actions)`；建议操作按采集模式能力路由：落后事件武将缺失 → `SUGGEST_INCREMENTAL`（增量获取，按 ID 缺失拉新）、已存在且过期 → `SUGGEST_SPECIFIC`（指定获取替换模式刷新），混合批次两者并给。精简版（`full_build=False`）只判武将 + 榜单。`WATCH_DIR` / `WATCHED_FILE_NAMES` 供 UI 芯片的 `QFileSystemWatcher` 监听 data/ 目录（与判定路径同源维护，覆盖终端 pull_data 原子落盘）。

### 3.20 手动检查冷却闸门（check_cooldown.py）

`CheckCooldown`（106 行，无 Qt 依赖）把手动检查的忙碌判定与最小间隔收口为单一事实源（d5cceeb，TD-A2-001）。公告检查与卡牌百科检查此前各自持有一份逐行同构的实现（`CHECK_COOLDOWN_SECONDS = 60`、`_last_check_started_at`、`is_busy`、`cooldown_remaining`、`check_now` 的"忙碌→冷却→置位→起线程"四步），两侧阈值一旦分叉，用户看到的"60 秒冷却"提示会与实际行为不符。

**线程模型**：闸门自身加锁，使"检查忙碌→检查冷却→置位时间戳"成为原子操作（`try_acquire()`）。此前该四步是无锁 check-then-act，安全性完全依赖"所有调用方都在 GUI 主线程"这一只写在注释里的约定；一旦未来从 worker/定时器触发，双击即可起两个线程并发写同一份数据文件（VUL-B3-001）。

| 方法/属性 | 说明 |
|------|------|
| `CheckCooldown(is_busy: Callable[[], bool], cooldown_seconds=60, *, label="检查")` | 构造；不持有线程对象本身，只持有"是否忙碌"的判定函数——公告侧有检查与候选准备两条线程，卡牌侧只有一条，由调用方决定忙碌口径 |
| `try_acquire() -> bool` | 原子地"检查忙碌 + 检查冷却 + 占用本次检查窗口"；返回 False 表示被忙碌或冷却拦截（由调用方提示用户）；返回 True 时调用方必须随后启动线程，否则本次窗口白占一次冷却 |
| `is_busy` / `cooldown_remaining` / `cooldown_seconds` | 只读属性 |
| `backdate(seconds)` | 把上次启动时刻前移（模拟冷却已过），仅供测试与"手动解除限制"场景 |
| `reset()` | 清空冷却计时（测试与"手动解除限制"用） |

### 3.21 备份恢复服务（backup_restore_service.py）

`backup_restore_service.py`（247 行，无 Qt 依赖）消费 `data/backups/` 自动快照（eead74c）。此前备份的产生方已覆盖全部数据写入路径（编辑事务、配队导入覆盖、坏文件留底、AI 生成与官网采集覆盖前快照），但没有任何入口能消费这些备份——数据出事后最后一道防线只有会手工翻目录的用户够得着，且 keep=10 轮转会在继续使用中把救命快照挤出窗口。本模块补上消费端，不改动任何产生方。

**安全语义**：
- 恢复前先把当前文件快照一份（`snapshot_to_backups`）：恢复到更坏的状态也能再恢复回来，双向可回退；
- 恢复用原子写替换目标文件（`atomic_write_text`），失败时原文件保持不变，不产生半截文件；
- 坏备份（结构损坏或含校验失败记录）在预览与恢复两侧都拒绝，禁止把坏备份覆盖到好数据上；
- 武将/相性/攻略存在跨文件引用（相性与攻略引用武将 id），单文件恢复会造成引用失配，随后启动校验弹出的"失效关联修复"会诱导用户删数据；`select_group_entries` 提供按时间点整组选择的口径。

**刻意不用 DataManager.load 做预览/校验**：加载失败会触发坏文件留底副作用，把备份文件再快照进 `backups/backups/`，这里是纯只读视角。

| 接口 | 说明 |
|------|------|
| `restore_targets(data_dir)` / `default_targets()` | 四类核心数据文件的恢复目标注册表（`RestoreTarget`：key/label/unit/file_path/model/key_of/backup_source）；rag_corpus 等内部快照不纳入恢复范围 |
| `list_backups(target, *, limit=10)` | 列出同 stem 自动快照，新→旧至多 limit 份；手工抢救件（`*_polluted_*`、`*_backup_日期` 等）不匹配时间戳命名，与 `snapshot_to_backups` 的轮转 glob 保持同一口径，不进入可恢复列表 |
| `select_group_entries(targets, anchor_key, anchor)` | 按时间点整组选择：组内每个文件取 `created_at` 不晚于锚点的最近一份备份；`GROUP_KEYS = ("heroes", "synergies", "guides")` 三者必须同时间点恢复才不失配 |
| `inspect_current(target)` | 当前文件可读性检查，返回 (可读记录数, 坏记录数)；结构损坏返回 None |
| `preview_backup(target, entry)` | 解析备份并与当前文件做键级 diff；坏备份返回 `valid=False` |
| `restore_backup(target, entry)` | 把备份原子写回目标文件，返回 `RestoreResult(preview, snapshot_path)`；恢复前先快照当前状态，失败则中止恢复、原文件不动 |

### 3.22 模拟器分辨率体检（layout_resolution.py）

`compare_resolution(actual, reference)`（52 行，纯函数、无 Qt 依赖）是首启向导段 2 的画面体检判定（spec_onboarding §3.4 判定表）。等比缩放属识别链路的设计内行为（recognizer 按参考尺寸换算 ROI），故 1920×1080 等等比分辨率必须判 WARN 而非 FAIL，仅竖屏 / 非等比 / 过低才 FAIL。

返回 `{"status": "pass"|"warn"|"fail", "detail": str, "hint": str}`；`hint` 为建议动作方向（如「去重新框选」「打开模拟器配置」），由 UI 渲染成按钮。

判定规则：
- **pass**：实际分辨率与参考一致；
- **warn**：等比缩放（`abs(actual_ratio - reference_ratio) / reference_ratio <= 0.01`），名称框位置可能偏移（可用，识别率或下降）；
- **fail**：竖屏（`ah > aw`）、分辨率过低（`aw < rw * 0.5`，文字过糊）、或比例不一致（非等比）。

### 3.23 胜率名次单一事实源（win_rate_ranking.py）

`derive_win_rate_ranks(win_rates, names=None)`（31 行，纯函数）是胜率名次的单一事实源（c4a8cf4）。选将推荐页的奖牌与巅峰赛禁选建议都要把"胜率"换算成"名次"，此前两处各写一份：一处按武将名稳定排序，另一处按输入槽位稳定排序。同分时两者结论不同，且槽位序随 OCR 排版漂移，用户看到的是无法解释的奖牌归属。此处收敛为一份，同分统一按武将名排序（确定性优先于"看起来随机但稳定"的槽位序）。

`names` 为 None 时对全表排名；给定 names 时只在该候选集内排名——选将推荐页的奖牌只在"本局已识别的武将之间"比较，不能拿全服榜的名次去颁奖。names 中不在 win_rates 里的条目（未上榜/未识别）不参与排名。去重处理：调用方传入的是槽位名列表，空槽与重名会产生重复条目，重复项会被分到相邻名次并互相覆盖。

巅峰赛禁选建议（`peak_ban_advice.py`）与推荐服务（`recommendation_service.py`）均从本模块导入，详见 [`./module_peak_combos.md`](./module_peak_combos.md) 与 3.13。

---

## 四、关键代码片段

### 4.1 QProcess 参数构建与启动

```python
def _start_process(self, args: list[str]) -> None:
    """启动子进程并连接信号"""
    self._cancel_requested = False
    self._cancel_cleanup_process = None
    self._cancel_cleanup_polls = 0
    self._stdout_buffer.clear()          # 上一次任务的缓冲不能串到新任务
    self._stdout_line_buffer.clear()
    self._stderr_buffer.clear()
    self._failed_items.clear()
    self._release_process()              # 释放上一次已结束的 QProcess（TD-A8-001）
    self._process = QProcess(self)
    self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    self._process.setWorkingDirectory(str(PROJECT_ROOT))  # -m 相对模块名须锚定项目根（TD-A6-001）
    self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
    self._process.readyReadStandardError.connect(self._on_stderr_ready)
    self._process.finished.connect(self._on_finished)
    self._process.errorOccurred.connect(self._on_error)
    process_env = QProcessEnvironment.systemEnvironment()
    process_env.insert("MJS_QPROCESS_CHILD", "1")
    self._process.setProcessEnvironment(process_env)
    logger.info("启动子进程: python %s", " ".join(args))
    self._process.start(sys.executable, args)
    self._task_started = time.monotonic()
    self._start_watchdog()
```

> **设计思路：** `SeparateChannels` 确保 stdout 和 stderr 不混在一起。信号连接在 start 之前绑定，避免丢失启动瞬间的事件。`sys.executable` 保证与父进程使用同一 Python 解释器。`setWorkingDirectory(PROJECT_ROOT)` 使子进程以 `-m 相对模块名` 启动时按项目根解析，快捷方式/打包态从其他目录启动不会报"找不到模块"。启动前清空全部缓冲与失败项列表，并释放上一次已结束的 QProcess 对象（`deleteLater`），避免对象与信号连接随任务轮次持续累积。`MJS_QPROCESS_CHILD=1` 使子进程不再直写日志文件，全部输出经父进程收集。

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

### 4.4 手动检查冷却闸门（CheckCooldown）

```python
class CheckCooldown:
    def try_acquire(self) -> bool:
        """原子地"检查忙碌 + 检查冷却 + 占用本次检查窗口"。"""
        with self._lock:
            if self._is_busy():
                logger.warning("%s正在进行，忽略重复请求", self._label)
                return False
            remaining = self._remaining_locked()
            if remaining > 0:
                logger.warning("%s冷却中，剩余 %.1f 秒", self._label, remaining)
                return False
            self._last_started_at = time.monotonic()
            return True
```

> **设计思路（TD-A2-001 / VUL-B3-001）：** 公告检查与卡牌检查此前各自持有一份逐行同构的无锁 check-then-act 实现，两侧阈值一旦分叉，用户看到的"60 秒冷却"提示会与实际行为不符；且安全性完全依赖"所有调用方都在 GUI 主线程"这一注释约定，一旦从 worker/定时器触发，双击即可起两个线程并发写同一份数据文件。此处收口为共享类，`try_acquire()` 内部加锁使"检查忙碌→检查冷却→置位时间戳"成为原子操作。`_is_busy` 由调用方注入（公告侧有检查与候选准备两条线程，卡牌侧只有一条）。

### 4.5 ScriptRunner 状态机与启动失败终态通知

```python
def _on_error(self, error: QProcess.ProcessError) -> None:
    if error == QProcess.ProcessError.FailedToStart:
        # Windows: FailedToStart 在 start() 内同步触发，用返回值 False 表达失败
        # Linux:   错误由事件循环异步回报，run() 已返回 True，须补发 finished 兑现契约
        if self._start_pending:
            self._release_process()
            self.finished.emit(FAILED_TO_START_EXIT_CODE)
    elif self._cancel_requested:
        self._release_process()
        self._finish_cancellation()
    else:
        msg = get_qprocess_error_name(error)
        self.status_changed.emit(f"{self._service_name}出错: {msg}")
        self.error_occurred.emit(msg)
```

> **设计思路（VUL-B2-001）：** Qt6 对 `FailedToStart` 只发 `errorOccurred` 不发 `finished`，看门狗对 `NotRunning` 进程直接返回无法兜底，因此必须在此清理状态并补发终态通知，否则僵尸 QProcess 与看门狗会残留，看门狗到期会误杀后续健康任务。是否补发 `finished` 取决于时序：Windows 的 `FailedToStart` 在 `start()` 内同步触发（`_start_pending=False`，用返回值表达），Linux 异步回报（`_start_pending=True`，须补发 `finished(FAILED_TO_START_EXIT_CODE=-1)` 兑现"返回 True 必收到一次 finished"的契约）。`_proc` / `_watchdog` 必须先于 `start()` 提交——Windows 同步错误时若状态尚未就位，`_on_finished` 的清理会全部落空，留下僵尸 QProcess 和一个永不停止的看门狗。

---

## 五、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.manager` | `DataIssue` 模型（CardCatalogService 的仓储校验结果） |
| 依赖 | `src.scraper.*` | 构建 CLI 参数调用爬虫/AI 脚本（`official` / `incremental` / `ai_batch`） |
| 依赖 | `src.capture.adb_screen` | CaptureService / EmulatorOperationService 持有 AdbCapture 实例；`screencap_raw()` 直接返回 PNG bytes 跳过 PIL 解码 |
| 依赖 | `src.capture.prober` | ADB 路径探测与 MuMu 实例枚举（`probe_mumu_adb` / `test_adb_path` / `probe_all_devices_with_status`） |
| 依赖 | `src.capture.image_utils` / `image_validation` | 截图文件保存与本地图片加载 |
| 依赖 | `src.ocr.*` | 模板管理器、识别器、ROI 布局配置 |
| 依赖 | `src.ocr.engine_loader` | 官方榜单主引擎（`get_primary_ocr_engine()`，v6，与识别管线同源）与复核引擎（`get_recheck_ocr_engine()`，v4）经 `OfficialOcrEngines` 懒加载与跨任务移交；`chinese_cht` 兜底链已退役 |
| 依赖 | `src.ocr.official_board_parser` | 官方榜单图片读取、固定版式切分、横线恢复和胜率数字模板算法 |
| 依赖 | `src.ocr.character_similarity` | 官方榜单复用公开的武将词表纠错服务 |
| 依赖 | `src.data.win_rate_repository` | 胜率 CSV 覆盖后清空读取缓存（`clear_win_rate_cache`） |
| 依赖 | `src.data.peak_win_rate_repository` | 巅峰赛胜率 CSV 覆盖后清空读取缓存（`clear_peak_win_rate_cache`） |
| 依赖 | `src.data.recommendation_index_repository` | 推荐指数加载 / 重建 / 过期标记（`mark_recommendation_index_stale`） |
| 依赖 | `src.data.combo_manager` / `combo_seats` | 实战配队导入合并与 note 座次解析 |
| 依赖 | `src.data.hero_manager` / `guide_manager` / `synergy_manager` | 数据清理、失效关联修复与修改事务 |
| 依赖 | `src.data.card_catalog` | CardCatalogService 的三个仓储（cards/schema/annotations）与基础模型 |
| 依赖 | `src.data.json_repository` | `atomic_write_json()`（RuleDocOpsService，知识库范围）；pending_stats 原子替换写入；`data_management_service._ManagerTransaction._backup` 委托 `snapshot_to_backups()`（备份命名与轮转统一）；`combo_import_service` 导入前 `snapshot_to_backups()`；`backup_restore_service` 恢复前快照与 `atomic_write_text()` 原子恢复；`freshness.py` 损坏文件 `preserve_corrupt_file()` 留底 |
| 依赖 | `src.data.manager` | `DEFAULT_DATA_DIR`（备份恢复目标路径）；`DataIssue` 模型（CardCatalogService 的仓储校验结果） |
| 依赖 | `src.data.models` | 备份恢复服务逐条解析 JSON 列表（`Hero` / `SynergyScore` / `HeroGuide` / `Combo`） |
| 依赖 | `src.ocr.card_grid_detector` | 巅峰赛牌面检测（`detect_selection_cards` / `board_row_sizes` / `layout_is_plausible` / `derive_name_rois`） |
| 被调用方 | `src.business.common.task_ledger` | `base_fetch_service._on_finished` 逐任务写台账（`logs/task_results.jsonl`）；`pull_data` / `maintain_rag` / `indexer` / `ops` 各写入点见 `module_rag.md` / `module_data.md` |
| 依赖 | `src.data.announcement_manager` | AnnouncementService 的公告合并去重与百科快照持久化 |
| 依赖 | `src.data.hero_timeline` | 武将变更时间轴（announcement 同步） |
| 依赖 | `src.scraper.official_source.announcement` | 公告 / 百科拉取、武将快照与更新候选计算 |
| 依赖 | `src.scraper.official_source.card_baike` | CardSyncService 调用 `fetch_official_cards` / `build_card_snapshot` / `diff_cards`，并复用 `CARD_HASH_FIELDS` / `CARD_FIELD_LABELS` / `normalize_text` 口径（diff 摘要 `card_field_diff_summary` 与全文格式化 `format_card_full_text` 位于本模块 card_sync.py，供卡牌同步对话框使用） |
| 依赖 | `src.scraper.ai.*` | 成本估算入口（`estimate_cost` / `estimate_item_cost`）与分类建议的 JSON 解析 |
| 依赖 | `src.config.env` | API 档案解析（`resolve_api_config` / `get_api_config`）、供应商预设（`PROVIDER_PRESETS`）、截图目录与模拟器配置读写；新增 `MUMU_OCR_RECHECK_ENABLED` / `MUMU_OCR_POLL_IDLE_PAUSE` 配置 |
| 被调用方 | `src.ui.app.main_window` | 主窗口连接业务服务的 Signal，UI 操作触发 `fetch_*()`；`PollCoordinator` 编排三板块轮询；闲置暂停恢复交互 |
| 被调用方 | `src.ui.app.poll_coordinator` | 消费 `OcrService.poll_tick`，调用 `CaptureService.capture_for_poll` / `submit_ocr_task`；帧指纹判闲与闲置暂停状态提交 |
| 被调用方 | `src.ui.app.data_freshness_chip` | 状态栏数据新鲜度芯片调 `compute_freshness_report()` 做四项健康判定；`RANKING_CSV_PATHS` / `WATCH_DIR` / `WATCHED_FILE_NAMES` 供其监听 data/ 目录 |
| 被调用方 | `src.ui.configuration.whitelist_config_dialog` | 白名单配置界面调 `find_whitelist_conflicts()` / `reset_ocr_recognizer_cache()` |
| 被调用方 | `src.ui.data_admin.official_data_import_dialog` | 触发后台导入、展示进度，并串联复核会话 |
| 被调用方 | `src.ui.data_admin.official_import_review_dialog` | 调用 `review_candidates()` / `apply_reviewed_records()` 完成人工修正落盘 |
| 知识库相关 | [`./module_rag.md`](./module_rag.md) | 元规则维护、知识库审计、索引精化、语料任务定义、武将分类 LLM 建议的依赖与被调用方 |

---

## 六、代码规模

| 指标 | 数量 |
|------|------|
| 测试模块数 | 153 个含 `test_*` 函数的测试文件（`tests/` 下 py 文件共 157 个） |
| 测试用例数 | 1936 个 `test_*` 函数 |

> 注：grep 静态计数，未计入 `parametrize` 展开；实际收集项以 `pytest --collect-only -q` 为准。2026-10-11 基线为 145 文件 / 1626 函数，本轮（2026-10-23）新增的备份恢复、访问冷却限频、ScriptRunner 状态机与启动失败终态、任务台账、看门狗树杀与无进度上限等回归测试已计入上表。

---

## 七、本轮文档校准（2026-10-06）

自基线 `885ea96`（2026-10-02 校准）以来的变更：

**新增**
- `common/task_ledger.py`（c88bf63，67 行）：任务结果台账，`record_task()` / `read_entries()` API，JSONL 追加 `logs/task_results.jsonl`（跨日志轮转窗口留痕）；写入点五处（base_fetch_service / indexer / maintain_rag / pull_data / ops）
- QProcess 看门狗（b3327f1）：`script_runner.DEFAULT_WATCHDOG_MS = 30min`（QTimer 单发 kill + ERROR）与 `base_fetch_service._WATCHDOG_TIMEOUT_MS` 同规格接线——消除 AI 生成卡网络时 `_is_busy` 永久 True 的挂死
- stderr tqdm 降噪（1426686）：base_fetch stderr 含 `%|` 降 DEBUG，原占 ai_generation.log 89% 的 WARNING 噪声

**修正**
- `ocr_task_coordinator.py`（c0f2143，2026-10-02）：OCR worker 首建接线改同步回调注入（构造参数 `on_worker_created` + `ensure_worker` 双检锁），删除 `worker_created` 信号（2→1）——原跨线程 Queued 投递致 worker 先 start、宿主接线滞后，官方导入 `_pending` 排他集合永不清理、功能永久锁死（本文档 3.2 节已含该修复描述）
- `official_ocr_engines.py`（1694ab7）：`main` 改 `get_primary_ocr_engine()`（v6，与识别管线同源）；`rare_char` 只取复核引擎（v4），chinese_cht 繁体兜底链退役
- `ocr_worker.py`（b3327f1/d1a55e1）：退役 worker 强杀兜底先 `logging.shutdown()`、退出码改 0；预热文案引擎中性化
- `data_management_service.py`（d2c5b83/8a46a43）：`_ManagerTransaction._backup` 委托 `snapshot_to_backups()`，移除失效 timestamp 参数
- `capture_service.py`（80cc75b）：截图存盘失败补 warning（状态栏不再显示"截图已保存："空路径，见 `module_peak_combos.md`）
- 测试规模台账：129 文件 / 1481 个 `test_*` 函数（原 112 / 1350）

---

## 八、本轮文档校准（2026-10-11）

自基线 `0ffed36`（2026-10-06 校准）以来的变更：

**新增**
- `recognition/session_guard.py` 三原语定稿（8a08d5d，P0-3，81 行）：`SessionGuard`（世代计数器：`begin()` 世代 +1 作废旧 token、`current()` 取快照、`is_current(token)` 校验；单次读写依赖 GIL 原子性，调用方在自有锁内或单线程使用）、`BusyGate`（忙碌闸：`acquire()` 锁内检查 + 置位原子、忙时返回 False、`release()` 幂等、`is_busy` 属性）、`OneShotToken`（一次性令牌：`mark()` 幂等置位、`spent` 属性、置位不可复位）。语义单测 `tests/test_session_guard.py` 5 用例（含 8 线程争用单赢家）。全仓五个消费点收口见 3.18
- `emulator/capture_connection.py`（118f59c，P1-8 第三刀，191 行）：`CaptureConnectionManager`（非 QObject 纯 Python 对象）承接 CaptureService 的 ADB 连接域四大职责——配置热更（`update_config()` 按三键 mumu_adb_path / mumu_adb_port / mumu_screenshot_mode 比对重建 AdbCapture、`set_target_port()`）、会话状态（`set_connection_state()` / `connection_state` / `sync_connection_state()` / `sync_poll_connection_state()`——先验 capture is `self._capture` 忽略过期实例）、连接编排（`connect_emulator()` / `connect_capture()` / `disconnect_emulator()`，阻塞 IO 全在 `_adb_io_lock`）、实例引用与锁只读暴露（`capture` / `is_current` / `session_lock` / `adb_io_lock`）。两把锁不嵌套（`_session_lock` 护内存态、锁内只做纯内存操作；`_adb_io_lock` 串行秒级 ADB IO，超时重试最坏约 45 秒）；状态与消息经构造注入回调广播（on_state_change / on_status / on_event_status），emit 留在 QObject 宿主。capture_service.py 575 → 479 行，连接域变一行薄委托，`_config` property 保留直通 `self._connection._config` 的测试兼容缝；关停旗标 `_closed` → OneShotToken；行数棘轮 575 → 479
- `freshness.py`（ae92c1d，449 行，无 Qt 依赖纯业务层）：数据新鲜度判定核心。档位周期写死 `[0,7)` 绿 / `[7,14)` 黄 / `≥14` 红（`STALE_GREEN_DAYS=7` / `STALE_RED_DAYS=14`）；`compute_freshness_report()` 判定武将/攻略/相性/榜单四项——武将项逐事件逐武将核对（事件落后 ⇔ 该武将在 heroes.json 缺失或 `last_updated` 早于事件日期，批次按公告 ref 去重），修复旧全局 max 对比四缺陷（指定获取洗绿 / 混合批次只推增量 / 部分覆盖洗绿 / 部分攻略缺失未暴露）；建议路由 `SUGGEST_INCREMENTAL`（缺失 → 增量获取）/ `SUGGEST_SPECIFIC`（过期 → 指定获取）混合双给；时间轴不健康退化为纯年龄兜底 + `timeline_warning`；精简版 `full_build=False` 只判武将 + 榜单；`RANKING_CSV_PATHS` 六榜单 CSV、`WATCH_DIR` / `WATCHED_FILE_NAMES` 供 UI 芯片监听；配置键 `DATA_FRESHNESS_TIMELINE_STALE_DAYS`（默认 14，透传）。消费者：`src/ui/app/data_freshness_chip.py`（状态栏芯片）

**收口（P0-3 纯重构，语义不变）**
- ocr_service 在途拍忙碌旗标 → `_poll_gate`（BusyGate）（89e4e8d）：`begin_poll` / `complete_poll` / `invalidate_inflight_poll` / `start_poll`（含 `_halt_poll`）五触点，Qt 主循环单线程下严格等价
- emulator_operation_service 关停旗标 `_closed` → `_shutdown_token`（OneShotToken）（df019a5）：`shutdown()` 后拒绝新任务语义不变
- announcement_update_coordinator 阶段推进旗标 `_phase_in_flight` → `_phase_gate`（BusyGate）（76875dd，UI 层，见 [`./module_ui.md`](./module_ui.md)）
- peak_select_watcher 会话世代 `_session` 裸 int → SessionGuard、三处手抄重置统一 `_clear_session_state()`（4542244，P0-1，详见 [`./module_peak_combos.md`](./module_peak_combos.md)）

**修正**
- mumu_config_coordinator shutdown 断连（6ea1866，行为修复）：`shutdown()` 显式 `disconnect` 掉 `CaptureService.connection_changed` → `self.connection_state_changed.emit` 的中继连接——emit 形式的连接无 QObject receiver，不随对话框销毁自动解除，残留连接在下次 ADB 连接状态广播时对已销毁对话框抛 "Signal source has been deleted"；配套回归测试
- match_guide 兜底 OCR 批次零产出短路（6ea1866，recognizer 批次零文本行时短路逐槽双证据与阵营逐槽重试链；详见 [`./module_capture_ocr.md`](./module_capture_ocr.md)，同提交含上述 shutdown 修复）
- 巅峰赛过场坏帧扣住一拍复核（036d0ae，行为修复，详见 [`./module_peak_combos.md`](./module_peak_combos.md)）
- 测试规模台账：145 文件 / 1626 个 `test_*` 函数（原 129 / 1481）

---

## 九、本轮文档校准（2026-10-23）

自基线 `2081b5b`（2026-10-11 校准）以来的变更，代码基线 `a63bc3c`：

**新增文件**
- `maintenance/backup_restore_service.py`（eead74c，247 行，无 Qt 依赖）：备份恢复服务——`data/backups/` 自动快照的列出（`list_backups`，只认时间戳命名的自动快照、新→旧至多 10 份、手工抢救件不进列表）、键级 diff 预览（`preview_backup`，新增/移除/变更条数）、原子恢复（`restore_backup`，恢复前先快照当前状态保证双向可回退、`atomic_write_text` 原子写替换、坏备份两侧拒绝）；`select_group_entries` 提供按时间点整组选择（`GROUP_KEYS = ("heroes", "synergies", "guides")` 三者必须同时间点恢复才不失配）。刻意不用 `DataManager.load` 做预览/校验（加载失败会触发坏文件留底副作用，把备份文件再快照进 `backups/backups/`）
- `common/check_cooldown.py`（d5cceeb，106 行，无 Qt 依赖）：`CheckCooldown` 手动检查的冷却闸门——`try_acquire()` 加锁使"检查忙碌→检查冷却→置位时间戳"成为原子操作（TD-A2-001，原为无锁 check-then-act，双击可起两个线程并发写同一份数据文件，VUL-B3-001）。公告检查与卡牌检查共用同一默认值 `DEFAULT_COOLDOWN_SECONDS = 60`
- `emulator/layout_resolution.py`（52 行，纯函数）：`compare_resolution(actual, reference)` 模拟器画面与 ROI 参考尺寸体检判定（pass/warn/fail，等比缩放判 WARN、竖屏/非等比/过低判 FAIL），首启向导段 2
- `analysis/win_rate_ranking.py`（c4a8cf4，31 行，纯函数）：`derive_win_rate_ranks(win_rates, names=None)` 胜率名次单一事实源——同分统一按武将名排序（确定性优先于槽位序），选将推荐页奖牌只在"本局已识别的武将之间"比较。巅峰赛禁选建议与推荐服务均从本模块导入

**修正（ScriptRunner / TaskLedger / BaseFetchService）**
- `common/script_runner.py`（14bffaf / 07606ce / 2b73535）：启动失败终态通知（`FAILED_TO_START_EXIT_CODE = -1`，Qt6 对 `FailedToStart` 只发 `errorOccurred` 不发 `finished`，须补发终态通知否则面板永久忙死，VUL-B2-001）；跨平台时序修复（Windows 同步 `FailedToStart` 在 `start()` 内触发用返回值表达、Linux 异步回报须补发 `finished`）；状态机加固（`_proc`/`_watchdog` 必须先于 `start()` 提交，`_release_process()` 置空并 `deleteLater()` 释放已结束的 QProcess，`_start_pending` 区分同步/异步启动失败）；树杀与复查（`_kill_process_tree()` Windows `taskkill /PID /T /F` 终止整棵进程树，`WATCHDOG_RECHECK_MS = 2000` 延迟复查绑定具体 proc 防误杀新进程）；看门狗 `Starting` 阶段挂 `started` 等进程起来后补树杀（Qt6 对 `Starting` 状态 `kill()` 原生 abort）
- `common/task_ledger.py`（2b73535）：跨进程文件锁串行化（`_ledger_lock()` 对 `*.lock` 首字节加非阻塞独占锁 `msvcrt.LK_NBLCK`/`fcntl.LOCK_NB`，50 次×20 ms 有界等待，锁机制不可用时降级无锁写入）；`read_entries()` 跳过空行与非对象 JSON 行（外部工具追加的 `123`/`["x"]`），不再因 `entry.get` 抛 `AttributeError` 让整份台账读不出；`record_task()` 入参归一化放 try 内（`None`/字符串等脏值不中断收尾流程）
- `fetching/base_fetch_service.py`（f1b13f8 / f10b477 / 9ccc2fc / 1d8334a）：看门狗改"无进度上限"（`_feed_watchdog()` 在每条进度行重置计时，browser 模式长清单全程超 30 分钟也放行）；超时走树杀而非 `cancel()`（不把"超时失败"记成"用户取消"）；取消 `Starting` 阶段崩溃修复（`cancel()` 在 `Starting` 时只置标记挂 `started`→`_on_started_after_cancel`，`cancel_process` 对 `Starting` 跳过并告警，VUL-B2-001）；取消清理轮询有界（`_CANCEL_CLEANUP_MAX_POLLS = 100`，约 5 秒，超限强制收尾）；子进程工作目录锚定 `PROJECT_ROOT`（`-m` 相对模块名，TD-A6-001）；进程释放（`_release_process()` 置空并 `deleteLater()`，TD-A8-001）；错误路径补台账（`_on_error` 补 `record_task(ok=False)`，`FailedToStart` 不发 `finished` 不在此记则台账缺失该类失败）；取消期间出错锁死修复（`FailedToStart` 先 `_release_process()` 再 `_finish_cancellation()`，TD-A5-001）
- `fetching/fetch_utils.py`：`cancel_process` 对 `Starting` 进程跳过并告警（不调用 kill）
- `fetching/hero_fetch_service.py`（1d8334a）：`fetch_specific()` 空列表直接拒绝（子进程对空目标打印"无目标武将"并以 0 退出，父进程上报成功会致公告更新流程把未采集批次当已完成并 `mark_applied`，VUL-B4-002）
- `fetching/synergy_fetch_service.py`（1d8334a）：相性重载双向互斥（`_is_busy()` 覆写把后台重载与抓取子进程纳入同一把互斥，`_on_reload_loaded` 写回前比对 mtime 防旧快照覆盖新数据 VUL-B3-002，`reloading` 属性供 UI 区分文案，`_on_reload_failed` 对称释放 worker 引用）

**修正（模拟器域）**
- `emulator/capture_service.py`（a2f6ecb / e790a9f）：截图终止信号保证（`_handle_capture_result` 整体包 try/except，漏接会让在途锁永不释放，界面永久停在"正在截图"）；轮询截图等待上界（`_POLL_CAPTURE_TIMEOUT_S = 120.0` 秒，执行器积压时轮询线程不再无限期静默）；重连路径统一（`_reconnect_if_needed()` 手动/轮询共用，TD-A2-001）
- `emulator/ocr_task_coordinator.py`（e790a9f / 0fff5e2）：shutdown 后拒绝复活 worker（`_worker_retired` 锁内复检，消除"Destroyed while thread is still running"崩溃 VUL-B2-001）；预热弃单（`OcrTask.abandon()` 置 `cancelled` 并释放整帧引用，worker 取出弃单任务短路）；`reset_warmup()` 预热悬挂出口（同时复位 worker 的 `_warmup_queued` 标志，VUL-B4-002）
- `emulator/official_import_gateway.py`（cb2b47d）：`cancelled` outcome 静默收尾（应用关闭排空队列时被取消不是失败，避免误导性错误弹窗）
- `emulator/mumu_config_coordinator.py`（7e88f63）：端口保存规则注释补全（显式选择/自动模式/固定端口未显式选择三档）

**修正（识别域）**
- `recognition/ocr_worker.py`（4e0cafe / cb2b47d / 0fff5e2）：白名单缓存失效（`reset_recognizer_cache()` 同步清 `_page_cache`——页面缓存里的结果同样是旧白名单下的纠错结论，不清会让同指纹页面继续复用旧结果）；轮询异常兜底（`OcrTask.abandon()` + `reset_warmup_state()`）；调试产物落盘降级（先挂 `result["ocr_results"]` 再 `try/except` 写 `latest.json`，磁盘满/目录不可写只损失调试产物、不连坐识别结果）
- `recognition/official_ocr_engines.py`（cb2b47d）：罕见字计数熔断（`note_rare_char_failure()`/`note_rare_char_success()`，连续达 `_RARE_CHAR_FAIL_LIMIT = 3` 才熔断，`_RARE_CHAR_COOLDOWN_SECONDS = 60.0` 冷却期满自动恢复，单次瞬时异常只损失当格兜底不连坐整批，TD-A5-001）
- `recognition/official_data_import_service.py`（cb2b47d）：罕见字兜底调引擎前先判 `rare_char_engine is not None`（不再对 None 引擎调 `status_callback`）；降级时补状态提示
- `recognition/session_guard.py`（9ccc2fc）：`BusyGate.reset()` 外部作废占用（与 `release()` 语义分立，TD-A9-001——此前任何线程可无差别 release、语义混同）
- `recognition/peak_select_watcher.py`（62c5c23 / 8020da8 / 67a67ef）：缺席阈值放宽至 4 拍（`BOARD_EXIT_TICKS = 4`，约 6 秒——禁将揭晓/转场连续 2 拍无卡致误退出）；漏检补齐（`_restore_missing_cards()` 按上一拍卡位补齐）；排布可信校验（`layout_is_plausible()`，不可信拍跳过不发）；退出衔接收敛至 PollCoordinator（详见 [`./module_peak_combos.md`](./module_peak_combos.md)）

**修正（公告/卡牌域）**
- `announcement/announcement_service.py`（d5cceeb / 801c9f1）：官网访问冷却限频与并发守卫加固（`CheckCooldown` 收口，`_compute_busy()` 判定检查与候选准备任一在途即忙碌，VUL-B3-001）；忽略名单名字兜底（条目键改武将名，原 entry_id 在官网改版后可能漂移）；首跑语义修正（`_is_first_run()` 不再只看文件是否存在，文件存在但加载报错且内存为空也按首跑处理——否则全部历史公告被当新公告重新入库，用户"已处理"进度丢失）；快照落盘兜底（`_finalize_check` 与 `mark_applied` 的 `save_baike_snapshot` 包 try/except，收尾中断会让 `check_finished` 不发射、进度条永久停留）
- `card_sync.py`（d5cceeb / 87eac8d）：`CheckCooldown` 收口；`apply_updates` 前置完整性守卫（基础库加载 `error` 时拒绝应用，防空库被官网数据覆盖 VUL-B4-002）；变更记录先写后落基线（基线推进后 diff 即消失，变更记录后写失败将永久缺失）；`CardSyncCheckResult.warning` 字段（基线落盘失败写入 warning 让对话框显式提示，否则官网真实变更被逐次就地吸收）
- `card_catalog.py`（87eac8d / 1dc08d5）：告警累积修复（`get_view` 读路径不再追加 `load_issues`，`list_views` 每次刷新逐卡调用致告警无界增长，VUL-B4-002）；注解保存回滚（`save_annotation_fields` 先快照再改内存，写盘失败 `_save_or_rollback` 回滚——否则 UI 弹"保存失败"后切走再切回会从内存读到幻影内容）
- `ai_cost.py`（87eac8d）：`use_rag=None` 按默认口径显式置 `True`（底层 `if use_rag` 会把 `None` 按 `False` 结算成经典口径）；`kind` 不在 `("guide", "synergy")` 抛 `ValueError` 不静默降级

**修正（数据分析域）**
- `analysis/recommendation_service.py`（c4a8cf4）：`RecommendationData` 字段 `win_rates` → `win_rate_percent`（0–100 百分数，与 `RecommendationIndex.win_rate` 0–1 比率量纲不同）；`load_error` 字段（索引读取失败 `OSError` 记入而不上抛，界面区分"读不到"与"确实没数据"）；`rebuild_indexes` 回读真实过期状态（不再自行 `mark_stale(False)`，过期标记清除归数据层）；删除 `rank_win_rates`（胜率名次收敛至 `win_rate_ranking.derive_win_rate_ranks`）
- `analysis/match_analysis_service.py`（0fff5e2）：攻略读取降级（单将攻略不可用只降级该将，异常上抛会在 Qt 槽内被吞致界面滞留上一帧）；要点截断留痕（`_record_truncation` 避免用户以为攻略本就只有几条）
- `analysis/peak_ban_advice.py`（c4a8cf4）：`derive_win_rate_ranks` 移至 `win_rate_ranking.py` 单一事实源，本模块 re-export；量纲注解（`win_rate` 为 0–100 百分数，勿传 0–1 比率）

**修正（维护域）**
- `maintenance/data_management_service.py`（062071a / 9ccc2fc）：主线程守卫（`_ManagerTransaction.__init__` 非主线程调 `RuntimeError`——快照→变更→落盘窗口不持跨管理器锁，跨线程调用会静默交错）；回滚汇总（`RollbackError(OSError)` 先尝试恢复全部 manager 再抛出，避免"第一个失败就中断"让其余文件停留已变更状态）；`DataManagementService.is_busy` 下沉到服务层复查（UI 确认弹窗停留期间任务可能已启动）；`clear_data` 中途异常回滚（否则"第一个已清空、第二个未清空"的半状态被后续 `save()` 固化）；`repair_missing_references` 前置守卫（武将加载失败或为空时中止，以残缺集合为基准会把全部攻略与相性判成失效关联而清空）
- `maintenance/combo_import_service.py`（3a8845f / 062071a）：源结构校验（`_load_source_combos`/`_load_hero_name_map` 顶层结构非法或映射为空抛 `ValueError`）；输出完整性守卫（加载 error 时中止，禁止在残缺集合上覆盖保存）；字段校验失败视同源中存在（`kept_invalid_keys` 保留同 key 既有记录）；覆盖前快照（`snapshot_to_backups`）

**修正（根域）**
- `freshness.py`（1dc08d5）：损坏文件留底（`_load_records` 解析失败先 `preserve_corrupt_file` 留底 `backups/`——本函数只降级返回 None，若用户随后重新生成或重新抓取，损坏文件被直接覆盖即"读不出来的数据"升级为"不可逆丢失"；`FileNotFoundError` 属正常状态不留底）；阈值可配置（`_stale_thresholds()` 读 `DATA_FRESHNESS_STALE_GREEN_DAYS`/`DATA_FRESHNESS_STALE_RED_DAYS`，默认 7/14，非法组合回退默认而非夹逼，TD-A6-001）
- `recognition/ocr_service.py`（9ccc2fc）：`_poll_gate.release()` → `reset()` 用于生命周期边界（`start_poll`/`stop_poll`/`invalidate_inflight_poll`，非持有者释放）

---

## 十、本轮文档校准（2026-10-25）

自基线 `a63bc3c`（2026-10-23 校准）以来的变更（覆盖 366303c / 28fa11b / 8c0f871 / 8f2e70a / feee42b / 514b4f8 / 4eb3445 / 282ed5b / 79fc4a0 / 39ec62b / 4bd9586 / ed8b4ac 共 12 个提交）：

**连接域双形态（8f2e70a / ed8b4ac）**
- `emulator/capture_connection.py`：`CaptureConnectionManager` 按 `emulator_type` 构建模拟器 `AdbCapture` 或 PC 客户端 `PcClientCapture`（PC 形态不要求 ADB 配置，窗口标题变更或跨形态切换时重建）；连接键读取统一 `_conn_key()` 兼容（`emulator_*` 新键优先、`mumu_*` 旧键回退）；`connect_capture(capture, validate_capture=)` 用户触发路径连通后截帧校验、自动重连等高频路径保持 False 免付一帧；连接/断开事件与失败前缀按形态分立（「ADB 已连接 / PC 客户端已连接」，PC 形态不再出现"ADB"字样，R3.2 显示诚实）；`AdbCapture | PcClientCapture` 类型注解统一放宽（capture_service 会话与轮询路径）
- `emulator/mumu_config_coordinator.py`：设备持久化链路 12 键 → 14 键（新增 `emulator_brand` 品牌记忆与 `pc_window_title`）；重复切换到当前连接类别判为无操作，不触发设备列表作废重建
- `emulator/emulator_operation_service.py`：探测执行器同键任务去重（连点刷新不累积排队）；探测调用透传 `emulator_type`

**官方榜单导入提速（366303c / 28fa11b / 8c0f871）**
- `recognition/official_data_import_service.py`：拼接长图切分归一化（`page_units` 虚拟分页 + `segment_offset` 坐标还原，复用分页链路）；单格识别 2× 快通道（`_fast_path_cell` / `_fast_numeric_cell` / `_fast_name_cell`，`FAST_PATH_MIN_CONFIDENCE = 0.90` 采信纪律，未过门回退 4× 双候选，整板提速约 4.6 倍）；OCR 结果解析收口 `_parse_ocr_lines()`；待复核行截图仅保留武将名称格（`_row` → `_name_cell`）
- 详见 §3.5 新增的三个小节

**抓取链路修复（4bd9586 / 79fc4a0）**
- `fetching/base_fetch_service.py`：完成钩子重入进程保护（`_on_finished` 记录完成时进程引用，钩子内同步发起下一阶段时不误释放新 QProcess，消除 0xF291 强杀，见 §3.1）
- `scraper/official_source/crawler.py`：robots.txt 获取超时 30s → 5s、头像下载 30s → 15s（与主数据超时解耦，站点异常时快速降级，归 `module_scraper.md` 记录）

**公告域（39ec62b）**
- `announcement/announcement_service.py`：公告检查与获取武将变更冷却窗口分立（`_gate` / `_prepare_gate` 各自独立 60 秒窗口，忙碌判定两闸门共用；新增 `prepare_cooldown_remaining` 属性），见 §3.6

**本轮范围外**：`src/capture/`（pc_client.py / probers/ 包）与 `src/ocr/official_board_parser.py` 的变更记录于 [`./module_capture_ocr.md`](./module_capture_ocr.md)；设备配置 UI 变更记录于 [`./module_ui.md`](./module_ui.md)；配置键与日志队列化记录于 [`./module_config.md`](./module_config.md)。
