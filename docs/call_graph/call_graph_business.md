# 调用链路：业务服务层

> 对应源码：`src/business/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 虚线 `───` 表示跨越进程边界（QProcess 子进程）。
> 与巅峰赛识别循环、禁选建议、实战配队相关的调用链路见 [call_graph_peak_combos.md](./call_graph_peak_combos.md)。

---

## 当前实现基线（6cbe8b6 / 2026-09-07）

成功语义以子进程退出码为准，`RESULT: FAIL=` 不再是服务协议。AI CLI 失败时以 `sys.exit(1)` 返回；`GuideFetchService` 和 `SynergyFetchService` 只在 `exit_code == 0` 时发送 `fetch_completed(True, ...)`，非零退出时由基类发射 `error_occurred(msg)`；`HeroFetchService` 无论成败都发 `fetch_completed(exit_code == 0)`。

```
GuideFetchService.fetch_*() / SynergyFetchService.fetch_*() / HeroFetchService.fetch_*()
  -> _is_busy()                                              [忙碌或取消清理中返回 False]
  -> _start_process(args)
    -> QProcess.readyReadStandardOutput -> _on_stdout_ready() -> _read_stdout()
      -> _dispatch_stdout_lines() -> _dispatch_stdout_line(raw_line)
        -> _log_stdout.info(line)
        -> [匹配 [i/N] 名字 FAIL] _failed_items.append(名字) [收集失败项供错误弹窗]
        -> 子类._on_stdout_line(line) -> progress_output/progress_value
    -> QProcess.finished(exit_code) -> _on_finished(exit_code)
      -> _read_stdout() / _read_stderr()
      -> _dispatch_stdout_lines(flush=True)                  [分发末尾残行]
      -> _cleanup_context()                                  [删除临时 JSON]
      -> msg = _process_failure_message(exit_code, stdout, stderr)
         -> [匹配 ^[错误] 生成失败：...] | [含"思考过程耗尽输出额度"] | 退出码
      -> [_cancel_requested] _finish_cancellation()          [等进程树清理完再发 cancelled，且不发射错误信号]
      -> [exit_code == 0] status_changed("<服务名>完成")
      -> [exit_code != 0] status_changed("<服务名>失败") + error_occurred(msg)
      -> 子类._on_process_finished(exit_code)
```

`cancel_process()` 在 Windows 上以 `taskkill /PID <pid> /T /F` 异步结束整棵进程树（返回 `subprocess.Popen` 由 `_cancel_cleanup_process` 持有），其他平台退回 `process.kill()`；两者都不在 GUI 线程执行 `waitForFinished()`。收尾、状态通知和上下文释放由 `finished` 信号统一完成；取消引起的崩溃事件被忽略。

stdout 先累计到字节缓冲，`_dispatch_stdout_lines()` 只分发完整换行行，`_on_finished()` 再 flush 末尾残行，因此 QProcess 分块读取不会破坏 UTF-8 或 `[i/N]` 进度匹配。`_dispatch_stdout_line` 在转发每行时同步用正则 `\[(\d+)/(\d+)\]\s+(.+?)\s+FAIL(?:\s|$)` 收集失败项名到 `_failed_items`，工作流出错弹窗据此列出失败清单。CLI 仍应输出换行并及时 flush，保证进度及时到达。进度白名单（`fetch_utils.is_generation_progress_line`）放行 `[RAG]` 与 `[重试]` 前缀行，后者由 `api_generator` 限流退避时输出，进度窗口显示"重试中"；协议行的结构化解析统一在 `fetch_utils.parse_generation_event()` → `GenerationEvent`，白名单过滤与进度条推进共用同一解析源。

## 一、QProcess 服务通用模式

三个 FetchService（Hero / Guide / Synergy）遵循相同设计模式。通用模式方法由 `BaseFetchService`（`src/business/fetching/base_fetch_service.py`）提供，三个子类继承后各自实现 `fetch_*` 与钩子方法。以下以 HeroFetchService 为例说明通用结构。

### 1.1 通用启动链路

```
[UI 菜单操作]
  -> fetch_all() / fetch_incremental() / fetch_specific()
    -> _is_busy()                                              [并发保护 + 取消清理中拦截]
       -> [_cancel_requested] warning + return False
       -> [process.state() != NotRunning] warning + return False
    -> _start_process(cli_args)                                [构建参数并启动]
       -> _cancel_requested = False; 清空 stdout/stderr/failed_items 缓冲
       -> QProcess(self)                                       [创建 QProcess 对象]
       -> setProcessChannelMode(SeparateChannels)               [分离 stdout/stderr]
       -> readyReadStandardOutput.connect(_on_stdout_ready)     [连接信号]
       -> readyReadStandardError.connect(_on_stderr_ready)
       -> finished.connect(_on_finished)
       -> errorOccurred.connect(_on_error)
       -> process_env.insert("MJS_QPROCESS_CHILD", "1") [所有子进程统一注入（含 AI）；不直写文件，stdout/stderr 交父进程]
       -> setProcessEnvironment(process_env)
       -> QProcess.start(sys.executable, cli_args)             [启动子进程]
         ─────────────────────────────────────────────────────────
         [子进程] python -m src.scraper.xxx [args]
         ─────────────────────────────────────────────────────────
  -> [子进程结束] QProcess.finished 信号触发
    -> _on_finished(exit_code)
      -> _cleanup_context()                                   [删除临时 JSON]
      -> msg = _process_failure_message(exit_code, stdout, stderr)
      -> [_cancel_requested] _finish_cancellation() 后直接返回  [取消优先，不发射错误信号]
      -> [exit_code == 0] status_changed("<服务名>完成")
      -> [exit_code != 0] status_changed("<服务名>失败") + error_occurred(msg)
      -> [Hero] _on_process_finished -> fetch_completed(exit_code == 0)
      -> [Guide/Synergy] _on_process_finished -> [exit_code == 0] fetch_completed(True, ...)
      -> _context = None
```

| 函数 | 说明 |
|------|------|
| `_is_busy()` | 检查 `_cancel_requested` 与 QProcess.state()，不等待直接返回 |
| `_start_process(args)` | 清缓冲 + 创建 QProcess + 信号连接 + 注入 `MJS_QPROCESS_CHILD` + start |
| `failed_items` | 属性：本次任务从 stdout FAIL 行收集的失败项名（武将名/相性对名） |
| `_on_stdout_ready()` | 读取 stdout → 累积缓冲 → 按换行分发完整行 |
| `_dispatch_stdout_line(raw)` | 写日志 + 收集失败项 + 调子类 `_on_stdout_line()` |
| `_on_stderr_ready()` | 读取 stderr → 按 warning 写日志 |
| `_on_finished(code)` | 读残余管道 → 清临时文件 → 失败消息 → 发状态/错误信号 → 调子类钩子 |
| `_finish_cancellation()` | 等进程树清理结束（50 ms 轮询）后发 `cancelled` |
| `_on_error(error)` | QProcess 异常 → 清理 → emit `error_occurred`；取消中的崩溃事件被忽略 |
| `cancel()` | 置 `_cancel_requested` → `cancel_process()`（Windows 清理进程树，返回 Popen） |

> 以上通用方法定义在 `src/business/fetching/base_fetch_service.py` 的 `BaseFetchService` 中，三个子类通过继承复用。

### 1.2 stdout / stderr 分块处理

```
QProcess.readyReadStandardOutput
  -> BaseFetchService._read_stdout()
     -> _stdout_buffer.extend(data)                         [完整 stdout，供失败原因识别]
     -> _stdout_line_buffer.extend(data)                    [实时解析]
     -> _dispatch_stdout_lines()
        -> partition(b"\\n")                               [只取完整行]
        -> _dispatch_stdout_line(raw_line)
           -> raw_line.decode("utf-8", errors="replace").strip()
           -> _log_stdout.info(line)                         [按工作流写日志]
           -> [匹配 [i/N] 名字 FAIL] _failed_items.append(名字) [收集失败项供错误弹窗]
           -> _on_stdout_line(line)                          [子类解析进度]

QProcess.finished
  -> _read_stdout() / _read_stderr()
  -> _dispatch_stdout_lines(flush=True)                      [分发无换行的最后一行]
  -> _cleanup_context()
  -> _process_failure_message(exit_code, stdout, stderr)
  -> [_cancel_requested] _finish_cancellation() 后直接返回
  -> [exit_code == 0] status_changed("<服务名>完成")
  -> [exit_code != 0] status_changed("<服务名>失败") + error_occurred(msg)
  -> 子类._on_process_finished(exit_code)
```

`_stdout_buffer` 仅保存完整 stdout 供失败原因识别，结束后立即清空，不再整体写入业务日志；`_stdout_line_buffer` 保存尚未遇到换行的字节尾部。两个缓冲区职责不同，不能用结束缓冲替代实时解析缓冲。失败消息优先取 CLI 结尾的 `^\s*\[错误\]\s*生成失败：(.+)$` 摘要行（多行搜索，部分成功时比单个错误标记更准确），其次识别"思考过程耗尽输出额度"，其余显示"进程退出码: N"。

---

## 二、HeroFetchService（武将采集）

### 2.1 三种采集模式

```
fetch_all() -> bool
  -> _is_busy()
  -> _start_process(["-m", "src.scraper.official"])

fetch_incremental() -> bool
  -> _is_busy()
  -> _start_process(["-m", "src.scraper.incremental", "--incremental"])

fetch_specific(hero_ids: list[int]) -> bool
  -> _is_busy()
  -> _start_process(["-m", "src.scraper.incremental",
                     "--hero-id", ",".join(str(i) for i in hero_ids)])
```

三者均返回 `bool`（子进程是否成功启动）；忙碌等未启动场景不发任何完成信号。`_on_process_finished()` 无论成败都发 `fetch_completed(exit_code == 0)`。

| 方法 | 调用方 | 子进程模块 | 说明 |
|------|--------|-----------|------|
| `fetch_all()` | `_request_fetch_all()` | `src.scraper.official` | 全量覆盖 |
| `fetch_incremental()` | `_request_fetch_incremental()` | `src.scraper.incremental` | 仅增量 |
| `fetch_specific(ids)` | `_request_fetch_specific()` | `src.scraper.incremental` | 按 ID 覆盖 |

### 2.2 信号拓扑

```
HeroFetchService.status_changed     → MainWindow._on_fetch_status   → status_label.setText()
HeroFetchService.fetch_completed(bool)→ MainWindow._on_fetch_completed → QMessageBox
HeroFetchService.error_occurred     → MainWindow._on_fetch_error    → QMessageBox.warning()
HeroFetchService.progress_updated(int, int, str) → 采集步骤进度（[1/5] / [1/3] 行解析）
```

### 2.3 函数清单

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `__init__(parent)` | `hero_fetch_service.py` | `MainWindow.__init__()` | 初始化 QProcess=None |
| `fetch_all()` | `hero_fetch_service.py` | `MainWindow._request_fetch_all()` | `_is_busy()`, `_start_process()` |
| `fetch_incremental()` | `hero_fetch_service.py` | `MainWindow._request_fetch_incremental()` | `_is_busy()`, `_start_process()` |
| `fetch_specific(ids)` | `hero_fetch_service.py` | `MainWindow._request_fetch_specific()` | `_is_busy()`, `_start_process()` |
| `cancel()` | `base_fetch_service.py` | 外部 UI | `cancel_process()` [fetch_utils] |
| `_is_busy()` | `base_fetch_service.py` | `fetch_*()` | `QProcess.state()` 检查 |
| `_start_process(args)` | `base_fetch_service.py` | `fetch_*()` | `QProcess.start()` |
| `_on_finished(exit_code)` | `base_fetch_service.py` | `QProcess.finished` | 清缓冲/临时文件 → `error_occurred` |
| `_on_process_finished(exit_code)` | `hero_fetch_service.py` | `_on_finished()` | emit `fetch_completed(exit_code == 0)` |
| `_on_error(error)` | `base_fetch_service.py` | `QProcess.errorOccurred` | emit `error_occurred()` |

---

## 三、GuideFetchService（攻略生成）

### 3.1 完整调用链

```
MainWindow._request_guide_all()
  -> AiGenerationWorkflow.request_guide_all()
    -> _get_heroes_as_dicts()                                   [Hero → dict]
    -> estimate_generation_cost(len(heroes), "guide")          [AI 成本估算，业务层入口]
    -> BackendChooseDialog(estimation, title, parent)          [选择 API/浏览器模式 + 语料增强]
     -> [API Tab] 显示 Token/费用估算（切换语料增强时重算）
     -> [浏览器 Tab] 显示 Edge 配置说明
     -> get_selected_backend() + get_selected_rag()          [返回 (backend, use_rag)]
    -> [确认] GuideProgressDialog(hero_count, parent)          [创建进度条对话框]
      -> GuideFetchService.fetch_all(heroes, backend, use_rag) -> bool
       -> _is_busy()
       -> [设置 context = {"mode", "heroes", "backend", "use_rag"}]
       -> execute_with_confirmation()
         -> base_args = ["-m", "src.scraper.ai_batch", "--guide"]
         -> [use_rag=False] 追加 "--no-rag"                    [经典模式，禁用 RAG 注入]
         -> [backend=="browser" 追加 "--browser"]
         -> [增量/指定模式 追加 "--update"]
         -> [增量/指定模式 写入 temp JSON 文件]
         -> _start_process([*base_args, "--heroes-file", tmp_path])
      -> GuideProgressDialog.exec()                            [模态事件循环等待子进程完成]
  -> [子进程结束]
    -> BaseFetchService._on_finished(exit_code)
      -> _cleanup_context() -> _cleanup_tmp_file()             [删除临时文件]
      -> _process_failure_message(exit_code, stdout, stderr)
      -> [exit_code == 0] GuideFetchService._on_process_finished -> emit fetch_completed(True, "攻略生成完成")
      -> [exit_code != 0] emit error_occurred(msg)            [失败摘要 / 思考额度耗尽 / 退出码]
        -> AiGenerationWorkflow._on_guide_completed() / _on_guide_error()
          -> GuideProgressDialog.on_process_finished()
          -> [成功] self._guide_manager.load()                 [刷新内存缓存]
          -> emit guides_changed
            -> MainWindow._on_guides_generated() -> _update_status()
          -> [失败] QMessageBox 详情列出 failed_items 失败武将清单
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `fetch_all(heroes, backend, use_rag=True) -> bool` | `guide_fetch_service.py` | `AiGenerationWorkflow.request_guide_all()` | `_is_busy()`, `execute_with_confirmation()` |
| `fetch_incremental(heroes, backend, use_rag=True) -> bool` | `guide_fetch_service.py` | `AiGenerationWorkflow.request_guide_incremental()` | `_is_busy()`, `guide_mgr.list_guides()`（全有则不发信号）, `execute_with_confirmation()` |
| `fetch_specific(heroes, backend, use_rag=True) -> bool` | `guide_fetch_service.py` | `AiGenerationWorkflow.request_guide_specific()` | `_is_busy()`（空列表则不发信号）, `execute_with_confirmation()` |
| `execute_with_confirmation()` | `guide_fetch_service.py` | `fetch_*()` | 构建参数 → 写临时 JSON → `_start_process()` |
| `cancel()` | `base_fetch_service.py` | 外部 UI | `cancel_process()` [fetch_utils] |
| `_start_process(args)` | `base_fetch_service.py` | `execute_with_confirmation()` | `QProcess.start()` |
| `_on_stdout_line(line)` | `guide_fetch_service.py` | `BaseFetchService._dispatch_stdout_line()` | `is_generation_progress_line()` → `progress_output`；`parse_generation_event()` → `progress_value` |
| `_on_process_finished(code)` | `guide_fetch_service.py` | `_on_finished()` | `[exit_code == 0]` emit `fetch_completed(True, "攻略生成完成")` |
| `_cleanup_context()` | `base_fetch_service.py` | `_on_finished()`, `_on_error()` | `_cleanup_tmp_file()` → `os.unlink(tmp_path)` |

### 3.2 信号拓扑

```
GuideFetchService.status_changed   → AiGenerationWorkflow.status_changed → MainWindow._on_fetch_status
GuideFetchService.fetch_completed  → AiGenerationWorkflow._on_guide_completed → GuideManager.load + guides_changed
GuideFetchService.error_occurred   → AiGenerationWorkflow._on_guide_error → QMessageBox（详情列出 failed_items 失败武将清单）
GuideFetchService.progress_output  → AiGenerationWorkflow._on_guide_progress → GuideProgressDialog.update_status
GuideFetchService.progress_value   → AiGenerationWorkflow._on_guide_progress_value → GuideProgressDialog.update_progress

```

---

## 四、SynergyFetchService（相性获取）

### 4.1 三种配对模式

```
MainWindow._request_synergy_pair()
  -> AiGenerationWorkflow.request_synergy_pair()
  -> _require_heroes()
  -> SynergyPairDialog(hero_manager)                            [选 2-8 武将]
     -> BaseHeroSelectDialog(MULTI_LIMIT, max_selection=8)
     -> 用户勾选 → _on_accept → _set_result_by_ids()
  -> estimate_generation_cost(pair_count, "synergy")          [AI 成本估算，业务层入口]
  -> BackendChooseDialog(estimation, title)                    [选择后端 + 语料增强]
  -> GuideProgressDialog(pair_count, title)
     -> SynergyFetchService.fetch_pair(selected, backend, use_rag) -> bool
       -> _submit("--synergy-pair", heroes, mode="pair", ...)
         -> _is_busy()
         -> [写入 payload 到 temp JSON]
         -> _start_process(["-m", "src.scraper.ai_batch", "--synergy-pair", tmp_path]
                            + [--no-rag / --update / --browser])
  -> GuideProgressDialog.exec()


MainWindow._request_synergy_single()
  -> AiGenerationWorkflow.request_synergy_single()
  -> SynergySingleDialog(hero_manager)                          [选 1 武将]
     -> BaseHeroSelectDialog(SINGLE)
  -> GuideProgressDialog(hero_count, title)
     -> SynergyFetchService.fetch_single(hero, all_heroes, backend, use_rag) -> bool
       -> _submit("--synergy-single", [hero], mode="single", ...)
         -> _is_busy()
         -> [写入 1 个武将到 temp JSON]
         -> _start_process(["-m", "src.scraper.ai_batch", "--synergy-single", tmp_path]
                            + [--no-rag / --update / --browser])


MainWindow._request_synergy_combos()                            [实战配队清单批量]
  -> SynergyFetchService.fetch_pairs_list(pairs, backend, overwrite, use_rag) -> bool
    -> _submit("--synergy-list", [{"hero_a_id", "hero_b_id"}, ...], mode="pairs_list", ...)
      -> _start_process(["-m", "src.scraper.ai_batch", "--synergy-list", tmp_path] + [...])


[取消生成后保住已分批提交的数据]
  -> SynergyFetchService.reload_from_disk() -> bool
    -> SynergyReloadWorker(file_path, parent)                   [QThread]
      -> run() -> SynergyManager(file_path).load()
      -> emit loaded(list[SynergyScore], list[DataIssue])
    -> _on_reload_loaded(synergies, issues)
      -> synergy_manager.replace_loaded_data(synergies, issues) [主线程原子替换内存数据]
      -> emit reload_finished
    -> emit reload_failed(str)                                  [解析异常]
    -> worker.finished -> worker.deleteLater                     [自回收]
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `fetch_pair(heroes, backend, overwrite=False, use_rag=True) -> bool` | `synergy_fetch_service.py` | `AiGenerationWorkflow.request_synergy_pair()` | `_submit()` → `_is_busy()`, 写 temp JSON, `_start_process()` |
| `fetch_single(hero, all, backend, use_rag=True) -> bool` | `synergy_fetch_service.py` | `AiGenerationWorkflow.request_synergy_single()` | `_submit()` |
| `fetch_pairs_list(pairs, backend, overwrite=False, use_rag=True) -> bool` | `synergy_fetch_service.py` | `AiGenerationWorkflow.request_synergy_combos()` | `_submit()`（`--synergy-list`） |
| `reload_from_disk() -> bool` | `synergy_fetch_service.py` | 取消生成后的工作流收尾 | `SynergyReloadWorker.start()` |
| `_submit(args_flag, payload, mode, backend, use_rag, overwrite)` | `synergy_fetch_service.py` | `fetch_*()` | 写 temp JSON → 拼 CLI → `_start_process()` |
| `_on_stdout_line(line)` | `synergy_fetch_service.py` | `BaseFetchService._dispatch_stdout_line()` | `is_generation_progress_line()`；`parse_generation_event()` 且 kind ∈ {ok, fail, skip} 才推进度 |
| `_on_process_finished(code)` | `synergy_fetch_service.py` | `_on_finished()` | `[exit_code == 0]` emit `fetch_completed(True, "相性生成完成")` |
| `cancel()` | `base_fetch_service.py` | 外部 UI | `cancel_process()` [fetch_utils] |

### 4.2 信号拓扑

```
SynergyFetchService.status_changed   → AiGenerationWorkflow.status_changed → MainWindow._on_fetch_status
SynergyFetchService.fetch_completed  → AiGenerationWorkflow._on_synergy_completed → SynergyManager 刷新 + synergies_changed
SynergyFetchService.error_occurred   → AiGenerationWorkflow._on_synergy_error → QMessageBox（详情列出 failed_items 失败配对清单）
SynergyFetchService.progress_output  → AiGenerationWorkflow._on_synergy_progress → GuideProgressDialog.update_status
SynergyFetchService.progress_value   → AiGenerationWorkflow._on_synergy_progress_value → GuideProgressDialog.update_progress
SynergyFetchService.reload_finished  → AiGenerationWorkflow._on_synergy_reload_finished → emit synergies_changed
SynergyFetchService.reload_failed(str) → AiGenerationWorkflow._on_synergy_reload_failed → status_changed（提示重载失败）
SynergyFetchService.cancelled        → _on_synergy_cancelled()，收尾后触发 reload_from_disk()
```

`SynergyReloadWorker` 的 `loaded(object, object)` / `failed(str)` 是 worker → service 的内部信号，service 收到后再转发为业务信号，界面不直接连接 QThread。

---

## 五、CaptureService（截图业务编排）

### 5.1 手动截图链路

```
RecommendationPanel._on_import_from_screenshot()
  -> [无 capture service] _open_mumu_config()                  [先配置模拟器]
  -> CaptureService.do_capture(hero_names, template_name="hero_selection", force_ocr=True)
    -> [adb-capture 单线程] capture_screenshot()                [不阻塞 GUI]
       -> [_session_lock] 取会话；未连接则 _connect_capture()
          -> [_adb_io_lock] AdbCapture.connect()
            -> _check_adb_valid() / _run_adb("connect", target) / _get_devices()
       -> [_adb_io_lock] AdbCapture.screencap_full()            [ADB 截图]
          -> subprocess.run(["adb", "-s", serial, "exec-out", "screencap", "-p"])
          -> Image.open(BytesIO(result.stdout))
       -> future.add_done_callback -> _capture_ready 信号
    -> [GUI 线程] _on_background_capture_ready() -> _handle_capture_result()
       -> should_ocr = perform_ocr 且（force_ocr 或 启用 OCR 或 轮询模式）
       -> _queue_capture_ocr(image.copy(), save_path=None, template_name, match_template=not force_ocr)
       -> _schedule_image_save(image, screenshots/screenshot_<时间戳>.png)
          -> [image-save 单线程] save_image() -> image_saved 信号
       -> [OCR 完成] _on_ocr_task_completed() -> emit capture_completed({image, save_path, ocr_results, ocr_matched})
```

`force_ocr=True` 使截图强制走模板匹配 + OCR，不受"启用 OCR 识别"开关影响；`MatchGuidePanel` 的导入路径使用 `template_name="match_guide"` 并同样强制 OCR。

### 5.2 从文件导入截图链路

```
RecommendationPanel._on_import_from_file()
  -> QFileDialog.getOpenFileName(...)                          [选择图片文件]
  -> CaptureService.capture_completed.connect(...)
  -> CaptureService.do_capture_from_file(file_path, hero_names)
    -> QTimer.singleShot(0, _execute_file_ocr)
       -> load_local_image(file_path)                          [PIL.Image]
       -> [perform_ocr=True] _queue_capture_ocr(match_template=not force_ocr)
          -> submit_ocr_task() -> OcrWorker.submit(OcrTask)
             -> OcrWorker._execute() -> 模板匹配 -> 必要时 OCR
          -> _on_ocr_task_completed() -> emit capture_completed(result)
       -> [perform_ocr=False] 直接 emit capture_completed(image, save_path, ocr_matched=False)
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `do_capture(hero_names, template_name, force_ocr, perform_ocr)` | `capture_service.py` | `RecommendationPanel` / `MatchGuidePanel` / `PeakSelectPanel` | `_adb_executor.submit(capture_screenshot)` → `_capture_ready` → GUI 线程处理 |
| `do_capture_from_file(path, names, template_name, force_ocr, perform_ocr)` | `capture_service.py` | `_on_import_from_file()` | `QTimer.singleShot(0, _execute_file_ocr)` |
| `_handle_capture_result(...)` | `capture_service.py` | `_on_background_capture_ready()` | 按参数决定入 OCR 队列；`_schedule_image_save()` |
| `_execute_file_ocr(...)` | `capture_service.py` | `do_capture_from_file()` 延迟调用 | `load_local_image()`, `_queue_capture_ocr()` |
| `capture_screenshot()` | `capture_service.py` | `EmulatorOperationService` / `_adb_executor` | 共享会话锁 → 必要时连接 → `screencap_full()` |
| `capture_for_poll(capture)` | `capture_service.py` | `PollCoordinator._on_poll_tick()` | `_adb_executor.submit(_capture_for_poll)` → `(ok, image, failure_kind)` |
| `submit_ocr_task(image, hero_names, template_name, recognize, rois, match_template, fallback_on_template_miss, allow_result_reuse)` | `capture_service.py` | 手动导入、轮询、巅峰赛、官方导入 | 构造 `OcrTask` → `OcrWorker.submit()` |
| `_on_ocr_task_completed(task)` | `capture_service.py` | `OcrWorker.task_completed` | 合并待处理截图上下文 → `capture_completed` / 官方导入信号 / 预热状态 |
| `warmup_ocr_model(hero_names)` / `wait_ocr_warmup(timeout_ms=15000)` | `capture_service.py` | 连接成功 / 启动画面 | `OcrWorker.warmup_model()` → `OcrTask` |
| `submit_official_import(paths)` | `capture_service.py` | `OfficialDataImportDialog._start_import()` | `OcrWorker.submit(OfficialImportTask)`；拒绝空选择与重叠任务 |
| `connect_emulator()` / `disconnect_emulator()` | `capture_service.py` | 外部 UI / `EmulatorOperationService` | `_connect_capture()` / `AdbCapture.disconnect()` |
| `update_config(config)` | `capture_service.py` | `MainWindow`, `MumuConfigCoordinator` | 路径/端口变化时重建 AdbCapture |
| `shutdown()` | `capture_service.py` | `MainWindow.closeEvent()` | 停止两个执行器 + `OcrWorker.retire()` |

### 5.3 信号拓扑

```
CaptureService.status_changed           → UI 状态栏
CaptureService.capture_completed(dict)  → RecommendationPanel._on_capture_result
  → load_from_ocr()                    → update_recommendations()
CaptureService.capture_failed           → UI 错误提示
CaptureService.connection_changed(str, str) → 主窗口同步 ADB 状态 → PollCoordinator.sync_with_connection()
CaptureService.image_saved(dict)        → 截图落盘结果（image-save 线程完成回调）
CaptureService.ocr_warmup_state_changed(str, str) → OCR 预热状态（idle/warming/ready/failed）
CaptureService.official_import_progress(str, int, int) → 官方导入进度（current<0 仅更新状态文字）
CaptureService.official_import_completed(object) / official_import_failed(str) → 官方导入结果
```

`_capture_ready` / `_image_save_ready` 为内部信号，用于把后台执行器结果跨线程送回 GUI 线程。

---

### 5.4 EmulatorOperationService（配置页后台操作）

两个单线程执行器互不排队：`emulator-probe`（ADB 路径与实例枚举）、`emulator-adb`（连接、设备测试、模板截图）。

```
MumuConfigDialog
  -> MumuConfigCoordinator.detect_adb()
  -> EmulatorOperationService.detect_adb()
    -> [探测线程] probe_mumu_adb() + test_adb_path()
  -> adb_detected -> 协调器回填 ADB 路径并 refresh_devices() -> 配置页状态

  -> MumuConfigCoordinator.refresh_devices()
  -> EmulatorOperationService.refresh_devices()
    -> [探测线程] probe_all_devices_with_status()            [MuMuManager 查询异常内部重试一次]
  -> devices_refreshed -> MumuConfigCoordinator.devices_changed -> MumuConfigDialog._on_devices_refreshed()
  -> device_refresh_failed -> 保留现有设备选择并显示失败状态

  -> MumuConfigCoordinator.connect(device, selected_explicitly) / disconnect()
    -> [多实例且端口为 0 且未显式选择] 返回提示，不发请求
    -> [显式选择且带端口] CaptureService.set_target_port() 废弃旧会话
  -> EmulatorOperationService.connect() / disconnect()
    -> [ADB 会话线程] CaptureService.connect_emulator() / disconnect_emulator()
  -> connection_finished / disconnection_finished -> 协调器转发 -> 配置页状态与提示

  -> MumuConfigCoordinator.start_template_capture(template_name)   [同模板重复请求忽略]
  -> EmulatorOperationService.capture_template_screenshot(template_name)
    -> [ADB 会话线程] CaptureService.capture_screenshot()
  -> screenshot_ready -> 协调器 template_screenshot_ready -> MumuConfigDialog._on_template_screenshot_ready()
      -> RoiSelectorDialog [UI 鼠标框选]
      -> MumuConfigCoordinator.create_template(image, roi, template_name)
        -> OcrService.create_template(image, roi, template_name)
      -> finish_template_capture() -> template_capture_finished

  -> MumuConfigCoordinator.start_roi_layout_capture(page_type)      [编辑整页 OCR 识别区域]
  -> EmulatorOperationService.capture_template_screenshot(f"roi_layout:{page_type}")
  -> 协调器按 "roi_layout:" 前缀分流 -> roi_layout_screenshot_ready / roi_layout_capture_finished
  -> MumuConfigCoordinator.save_roi_layout(page_type, layout)
    -> CaptureService.roi_config.save_layout()                      [配置页保存后立即生效]
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `detect_adb()` | `emulator_operation_service.py` | 配置页自动探测 | `probe_mumu_adb()`, `test_adb_path()` |
| `refresh_devices()` | `emulator_operation_service.py` | 配置页刷新/初始化 | `probe_all_devices_with_status()` → 成功/失败信号 |
| `connect()` / `disconnect()` | `emulator_operation_service.py` | 配置页连接按钮 | `CaptureService` 共享会话 |
| `test_device(path, port)` | `emulator_operation_service.py` | 配置页测试按钮 | 临时 `AdbCapture.connect()` + `check_device()`，结束后断开 |
| `capture_template_screenshot()` | `emulator_operation_service.py` | 模板制作 / ROI 布局编辑 | `CaptureService.capture_screenshot()` |
| `shutdown()` | `emulator_operation_service.py` | `MumuConfigCoordinator.shutdown()` | 停止两个执行器，不再向已销毁 UI 发射结果 |
| `MumuConfigCoordinator.resume_poll()` / `poll_is_paused()` | `mumu_config_coordinator.py` | 配置页轮询恢复按钮 | `OcrService.resume_poll()`（仅 `paused` 时生效） |
| `MumuConfigCoordinator.template_status()` / `select_template()` / `create_template()` | `mumu_config_coordinator.py` | 配置页模板区 | `OcrService` 同名方法 → 返回 `TemplateStatus(loaded, path)` |

---

## 六、OcrService（OCR 控制服务）

### 6.1 轮询链路

```
[启动/停止]
MumuConfigDialog 保存 / ADB 连接状态变化
  -> PollCoordinator.sync_with_connection()
    -> [未开启轮询 / 无会话 / 未连接] OcrService.stop_poll()
    -> [否则] OcrService.start_poll(mumu_ocr_poll_interval * 1000)
       -> 间隔钳到 max(interval, 1000)
       -> _replace_poll_session()                              [递增会话代数、置位旧取消标记]
       -> 任务状态重置：hero_selection 激活、match_guide 停用
       -> QTimer.setInterval + start()                          [常驻重复定时器]
       -> poll_state = "running"

PollCoordinator._on_poll_tick()                                [poll_tick 信号触发]
  -> CaptureService.start_ocr_worker()                         [首次惰性创建]
  -> OcrService.begin_poll() -> int | None                     [_poll_in_flight 单飞；返回会话代数]
     -> [None] 上一拍仍在执行，直接放弃
  -> OcrService.due_poll_tasks()                                [按各自冷却过滤]
     -> [空] complete_poll(generation, "healthy_no_match")
  -> CaptureService.capture is None -> PollResult("prerequisite_unconfigured")
  -> threading.Lock.acquire(blocking=False)
     -> [失败] complete_poll(generation, "retryable_capture", "上一轮轮询仍在执行")
  -> [后台线程 ocr-poll] do_poll_work()
    -> CaptureService.capture_for_poll(capture)                 [复用 adb-capture 单线程，单次截图]
       -> [ok=False] PollResult("retryable_connection" | "retryable_capture")
    -> 每个到期任务 CaptureService.submit_ocr_task(image, template_name,
              recognize=True, fallback_on_template_miss=(task=="match_guide"),
              allow_result_reuse=True)                          [页面指纹去重]
       -> OcrWorker._execute() -> 模板匹配 -> 指纹命中则复用结果 / 否则 OCR
       -> PollCoordinator.wait_for_ocr_task(ocr_task, cancel_event)
          -> [10 秒超时] PollTaskResult("retryable_ocr")
       -> [match_guide] _validate_match_guide_result()          [确认角色 ≥3 才视为命中]
    -> PollResult(generation, outcome, capture, task_results)
       -> _poll_result_received 信号
  -> finally: _poll_thread_lock.release()

PollCoordinator._consume_poll_result(result)                   [GUI 线程]
  -> [代数过期 / capture 已更换] 丢弃
  -> [连接或截图失败] CaptureService.sync_poll_connection_state(capture, detail)
  -> OcrService.complete_poll(generation, outcome, detail)
     -> matched/healthy_no_match -> 失败计数归零、恢复基础间隔、state="running"
     -> prerequisite_* -> QTimer.stop()、state="paused"
     -> 其他失败 -> 计数+1；≥5 转 "paused"；否则间隔拉长为 max(基础, 2/5/15/30s)、state="backing_off"
  -> poll_result_ready.emit(result)

MainWindow._on_poll_result(result)                             [主线程仅更新界面]
  -> [hero_selection 命中] set_task_cooldown(hero_selection, mumu_hero_selection_cooldown)
       -> clear_task_cooldown(match_guide) -> activate_task(match_guide)  [三板块串联：选将命中解锁对局攻略]
       -> RecommendationPanel.load_from_ocr()
  -> [hero_selection 未命中] _selection_page_active = False
  -> [hero_selection 模板缺失] deactivate_task(hero_selection)
  -> [match_guide 命中] deactivate_task(match_guide) -> MatchGuidePanel.update_block()
  -> [match_guide 未命中且空闲超 90 秒] _deactivate_match_guide_if_idle()  [停止非对局页空转]
  -> [巅峰赛牌面自动退出 board_exited] activate_task(match_guide)          [衔接对局攻略轮询]
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `start_poll(interval_ms)` | `ocr_service.py` | `PollCoordinator.sync_with_connection()` | 重置会话与任务状态 → `QTimer.setInterval` + `start()` |
| `stop_poll()` | `ocr_service.py` | `PollCoordinator` / 官方导入窗口 / `shutdown()` | `QTimer.stop()` + 清任务状态 + 递增会话代数 |
| `resume_poll()` | `ocr_service.py` | `MumuConfigCoordinator.resume_poll()`（仅 `paused` 时） | `start_poll()` |
| `begin_poll()` -> `int \| None` | `ocr_service.py` | `PollCoordinator._on_poll_tick()` | 检查状态与在途标记，置 `_poll_in_flight` |
| `complete_poll(generation, outcome, detail)` | `ocr_service.py` | `PollCoordinator._consume_poll_result()` / `_on_poll_tick()` | 状态迁移 + 动态调整定时器间隔 |
| `invalidate_inflight_poll()` | `ocr_service.py` | `PeakSelectWatcher.start()` | 作废在途会话并复位 `_poll_in_flight` |
| `is_poll_cancelled(generation)` | `ocr_service.py` | 在途轮询线程 | 代数比较 + 取消标记 |
| `activate_task()` / `deactivate_task()` / `set_task_cooldown()` / `clear_task_cooldown()` | `ocr_service.py` | `MainWindow._on_poll_result()` / 巅峰赛 watcher | 按任务独立维护 `PollTaskState` |
| `due_poll_tasks()` | `ocr_service.py` | `PollCoordinator` | 过滤 active 且未冷却的任务 |
| `run_ocr(image, rois)` | `ocr_service.py` | 兼容外部同步调用 | 注入的 `submit_ocr_task()`，等待 `OcrTask.completed`（30 秒超时返回 None） |
| `create_template(image, roi, template_name)` | `ocr_service.py` | `MumuConfigCoordinator` | `get_template_manager(name).set_template()` |
| `select_template(file_path, template_name)` | `ocr_service.py` | `MumuConfigCoordinator` | `shutil.copy2()` + 删元数据, `tm.reload()` |
| `delete_template(template_name)` | `ocr_service.py` | `MumuConfigCoordinator` | `get_template_manager(name).delete_template()` |

### 6.2 信号拓扑

```
OcrService.poll_tick              → PollCoordinator._on_poll_tick → 后台线程截图+OCR
OcrService.poll_state_changed(str, str) → PollCoordinator.poll_state_changed → UI 轮询状态显示
OcrService.template_changed       → UI 模板状态更新
OcrService.ocr_completed          → UI 获取识别结果
OcrService.status_changed         → UI 状态栏
```

`poll_tick` 由常驻重复 `QTimer` 驱动；`PollCoordinator` 的 `poll_result_ready` / `poll_state_changed` 才是对 UI 的结果信号。

---

## 官方榜单数据导入

官方榜单导入不经过 QProcess、ADB 或页面模板匹配，但会作为一个 `OfficialImportTask` 进入通用 `OcrWorker` 队列。worker 在自己的线程中向 `OfficialDataImportService` 注入已预热的 PaddleOCR 引擎，串行处理全部已选图片，并经 `CaptureService` 信号向弹窗报告进度。

```
MainWindow._open_official_data_import()
  -> [轮询活跃] OcrService.stop_poll()
  -> OfficialDataImportDialog.exec()
    -> _start_import()
      -> CaptureService.submit_official_import(paths)
        -> [空选择] raise ValueError | [已有任务在跑] raise RuntimeError
        -> OcrWorker.submit(OfficialImportTask)
        -> [OcrWorker QThread] _execute_official_import(task)
          -> 对每个已选类别 emit official_progress(status, 0, 0)
          -> OfficialDataImportService.import_pages(key, paths, progress_callback, status_callback)
            -> [key 不在 LAYOUTS / 无图片 / 同一张图被重复选择] raise ValueError
            -> official_board_parser.detect_layout(image, key) -> variant（legacy 长图 / paged 分页）
            -> official_board_parser.read_image() -> cv2.imdecode()
            -> official_board_parser.extract_panels() -> 固定版式裁出左右表
            -> [2v2 / peak] 左右榜单行数不一致 -> ValueError
            -> [exile] validate_exile_row_counts() -> ValueError
            -> [同一批次 variant 混用] ValueError
            -> official_board_parser.find_data_boundaries() -> HoughLinesP 横线 -> 行边界
            -> official_board_parser.restore_missing_boundaries() -> 补回漏检横线
            -> 计算 total_steps（胜率表：模板准备 + 行识别；其余表：行识别）
            -> progress_callback(0, total_steps)
            -> [每个面板] official_board_parser.prepare_rate_templates()（仅胜率表）
               -> build_rank_digit_templates()
               -> _recognize_cell() -> 每行完成后推进进度
            -> [每行] _recognize_row()
               -> 排名/普通单元格: _recognize_cell()
               -> 武将单元格: _recognize_name_cell()
                  -> [同首字无法唯一确认] _rare_char_engine（懒加载 chinese_cht）
                  -> _recognize_name_with_engine() -> 仅在当前候选白名单内纠正
                  -> status_callback("正在执行罕见字兜底识别")
               -> 胜率单元格: 预计算 OCR + official_board_parser.recognize_rate_with_templates()
            -> _validate_panel_rank_sequence()                 [排名 OCR 一致证据足够时阻止错序页覆盖]
            -> _review_reasons() -> 必要时 _save_review_crop()
            -> 全部页面结束 -> _resolve_batch_names(batch)     [榜单内部唯一性补全]
            -> _resolve_names_across_outputs(outputs)          [跨榜单候选集交集唯一时统一补全]
            -> _validate_output_names(outputs) -> 未确认/重复/集合不一致错误列表
            -> [始终] 写待复核 CSV + 行截图（screenshot_data/official_import/）
            -> [有校验错误] _save_pending_session() -> data/official_import_pending.json
                                -> raise ValueError("官方榜单名称校验失败：...")
            -> [通过] 每表 _write_csv() -> 同目录临时文件 replace 正式 CSV
            -> [本批未含巅峰赛胜率榜] mark_recommendation_index_stale(True)
            -> [写入 2v2 胜率榜] clear_win_rate_cache()
            -> [写入巅峰赛胜率榜] clear_peak_win_rate_cache()
          -> CaptureService emit official_import_completed(summaries) / official_import_failed(detail)
  -> [finally 且原轮询活跃] PollCoordinator.sync_with_connection()

[人工复核回路，不重新 OCR]
OfficialImportReviewDialog
  -> load_pending_session()                                    [损坏/不存在 -> None]
  -> OfficialDataImportService.review_candidates(ocr_name, current)
     -> 当前值 ∪ 距离 ≤ CANDIDATE_EXPANSION_EDIT_DISTANCE=2 ∪ 歧义候选；空则全表按距离排序
  -> apply_reviewed_records(pending, {(output_name, rank): 武将名})
     -> _validate_output_names()                               [失败即抛错，不写任何文件]
     -> _write_csv() 正式覆盖 + 同样的缓存清理
     -> clear_pending_session()
  -> OfficialDataImportDialog.recommendation_indexes_stale 信号 -> 推荐面板
```

### 名称候选决策

```
_recognize_name_cell(cell)
  -> _recognize_cell_candidates()
     -> 原图放大 OCR + CLAHE/锐化 OCR 的全部文本块
  -> [任一候选精确命中 hero_names] 返回置信度最高的完整名称
  -> [最高候选不是单字] 返回该候选 -> _normalize_name()
  -> [最高候选是单字] _recognize_name_glyphs()
     -> 亮色列分组 -> 2-4 个字形
     -> 保留原始背景与留白 -> 每字 _recognize_cell()
     -> 拼接 -> CharacterSimilarityService.correct_hero_name()
     -> [命中词表] 返回补识别名称
  -> [逐字失败] hero.startswith(单字) 的候选数量
     -> 唯一 -> 返回唯一候选
     -> 多个 -> _rare_char_engine（懒加载繁体模型）
        -> [模型可用] _recognize_name_with_engine()
           -> 只在简体 OCR 候选白名单内精确匹配或唯一纠正
           -> [命中候选] 返回完整名称
        -> [模型不可用或仍不匹配] 返回原结果
  -> [整榜完成] _resolve_batch_names(batch)
     -> 歧义候选减去本榜已确认名称后仅剩一个且无竞争 -> 自动补全并保留复核记录
  -> _resolve_names_across_outputs(outputs)
     -> 各输出表未确认行的候选集交集恰为 1 -> 统一补全
        [避免同一名将在左右榜 OCR 差异下被误判为"集合不一致"]
  -> _validate_output_names(outputs) -> 仍未确认 / 重复 / 行数相同的表之间集合不一致
     -> 写待复核 CSV + 保存复核会话，正式 CSV 不动
```

| 函数/信号 | 调用方 | 关键下游 | 说明 |
|---|---|---|---|
| `CaptureService.submit_official_import(paths)` | `OfficialDataImportDialog._start_import()` | `OcrWorker.submit(OfficialImportTask)` | 空选择/重叠任务抛错；转发进度、完成和失败信号 |
| `OcrWorker._execute_official_import(task)` | worker 队列 | `OfficialDataImportService.import_pages()` | 复用同线程 PaddleOCR 引擎并完整执行整批任务 |
| `import_pages(key, image_paths, progress_callback, status_callback)` | Worker | `official_board_parser`、OCR、复核、CSV 原子写入 | 按列表顺序合并分页，全部校验后一次覆盖 CSV；失败抛错并保存复核会话 |
| `official_board_parser.detect_layout / extract_panels / find_data_boundaries / restore_missing_boundaries / prepare_rate_templates / recognize_rate_with_templates / validate_exile_row_counts` | `import_pages()` | OpenCV、确定性图像与数字模板算法 | 不持有 OCR 模型、词表或输出状态 |
| `_recognize_name_cell()` | `_recognize_row()` | 候选汇总、逐字兜底、受限繁体兜底、词表校正 | 仅官方导入使用，不影响常规 OCR |
| `_validate_panel_rank_sequence()` | `import_pages()` | 排名 OCR 一致证据 | 偏移样本 ≥3 且最频繁偏移覆盖 ≥`max(3, 60%)` 才判定错序并抛错 |
| `_resolve_batch_names()` / `_resolve_names_across_outputs()` | `import_pages()` | 榜单内部唯一性补全、跨榜单交集补全 | 只有唯一证据才自动改名，并保留复核记录 |
| `_validate_output_names()` | `import_pages()` | 未确认名 / 重复名 / 同行数表集合不一致 | 名称完整性门禁；失败则正式 CSV 保持不动 |
| `_review_reasons()` | `import_pages()` | `_save_review_crop()` | 单字、名称置信度 < 0.75、胜率模板 < 0.90 且与 OCR 不一致、胜率模板失败、排名不一致、横线补全、名称重复进入复核 |
| `_write_csv(path, fieldnames, rows)` | `import_pages()` / `apply_reviewed_records()` | 同目录 `NamedTemporaryFile` + `replace()` | 原子覆盖，写入失败不留半成品 |
| `_save_pending_session()` / `load_pending_session()` / `clear_pending_session()` | `import_pages()` / 复核界面 | `data/official_import_pending.json` | 校验失败批次持久化，复核时不重新 OCR；读取损坏返回 None |
| `apply_reviewed_records(pending, corrections)` | `OfficialImportReviewDialog` | `_validate_output_names()` → `_write_csv()` | 人工修正后复用整批结果直接落盘；校验失败抛错且不写文件 |
| `review_candidates(ocr_name, current)` | `OfficialImportReviewDialog` | 词表距离扩展 + 歧义候选 | 为空时全表按编辑距离排序兜底 |
| `official_import_progress(status, current, total)` | `CaptureService` | `OfficialDataImportDialog._on_progress_changed()` | 先显示等待/分析的不定进度，行数确定后显示精确进度；`current < 0` 仅更新状态文字 |
| `official_import_completed(summaries)` / `official_import_failed(detail)` | `CaptureService` | 导入弹窗 | 整批成功/失败通知 |

**输出关系：**`2v2` 左表写 `2v2胜率排行.csv`、右表写 `2v2出场排行.csv`；`peak` 左表写 `巅峰赛胜率排行.csv`、右表写 `巅峰赛出场排行.csv`；`exile` 左右表按视觉行序合并进 `武将放逐.csv`。每份正式 CSV 均有对应 `_待复核.csv`，行截图位于 `screenshot_data/official_import/`，校验失败会话位于 `data/official_import_pending.json`。

## 七、fetch_utils（公共工具）

`is_generation_progress_line()` 的白名单与 `parse_generation_event()` 的解析共用同一份协议正则（`_START_RE` / `_OK_RE` / `_FAIL_RE` / `_SKIP_RE` / `_RETRY_RE` / `_REST_RE`），白名单另外放行 `[...] 开始...`、`[休息]`、`[RAG]` 前缀行——这些行没有 `[i/N]`，不影响进度条解析。

```
_cli 进度行
  -> is_generation_progress_line(line)  [Guide/Synergy 决定是否转发 progress_output]
  -> parse_generation_event(line) -> GenerationEvent(kind, label, current, total,
                                                     retry_round, retry_max, wait_seconds)
      -> Guide: 事件带 (current, total) 即 start/ok/fail/skip -> progress_value
      -> Synergy: 仅 kind ∈ {ok, fail, skip} -> progress_value（START 行不推进度条）
      -> [重试] 行 -> GuideProgressDialog 状态栏"重试中（第 x/y 次，n 秒后重试）"
```

| 函数 | 文件 | 调用方 | 说明 |
|------|------|--------|------|
| `is_generation_progress_line(line)` | `fetch_utils.py` | `GuideFetchService._on_stdout_line()`, `SynergyFetchService._on_stdout_line()` | 白名单放行不含生成正文的进度行 |
| `parse_generation_event(line)` -> `GenerationEvent \| None` | `fetch_utils.py` | 同上 | 协议行结构化解析；非协议行返回 None |
| `is_process_busy(process, name)` | `fetch_utils.py` | `BaseFetchService._is_busy()` | 检查 QProcess 状态并打 warning |
| `cancel_process(process)` -> `subprocess.Popen \| None` | `fetch_utils.py` | `BaseFetchService.cancel()` | Windows 经 `_terminate_process_tree()` 用 `taskkill /PID <pid> /T /F` 异步清理整棵进程树并返回 Popen；其他平台 `process.kill()` 并返回 None |
| `get_qprocess_error_name(error)` | `fetch_utils.py` | `_on_error()` | 错误码→中文描述 |
| `log_process_error(name, process)` | `fetch_utils.py` | `_on_error()` | 日志 + 错误信息拼接 |

---

## 八、外部调用关系总览

### 8.1 本模块被外部调用

```
src.ui.app.main_window
  -> HeroFetchService.*                                      [武将采集]
  -> GuideFetchService.* / SynergyFetchService.*             [攻略生成 / 相性获取]
  -> CaptureService.update_config / shutdown / start_ocr_worker / warmup_ocr_model / wait_ocr_warmup
  -> OcrService.start_poll / stop_poll / set_hero_names     [OCR 控制]
  -> PollCoordinator.*                                       [轮询调度入口]
  -> OfficialDataImportService.apply_reviewed_records 经对话框

src.ui.app.poll_coordinator
  -> OcrService.begin_poll / complete_poll / due_poll_tasks / poll_tick 连接
  -> CaptureService.capture_for_poll / submit_ocr_task / start_ocr_worker

src.ui.recommendation.recommendation_panel
  -> CaptureService.do_capture(force_ocr=True)                [手动截图 + 强制 OCR]
  -> CaptureService.do_capture_from_file()                    [文件导入]

src.ui.match.match_guide_panel
  -> CaptureService.do_capture(template_name="match_guide", force_ocr=True)
  -> CaptureService.do_capture_from_file(template_name="match_guide")

src.ui.match.peak_select_panel
  -> CaptureService.do_capture(perform_ocr=False)             [仅取图，不跑 OCR]
  -> CaptureService.submit_ocr_task(..., allow_result_reuse=False)

src.ui.configuration.mumu_config_dialog
  -> MumuConfigCoordinator.*                                  [配置草稿 / 模板 / 轮询恢复]
     -> EmulatorOperationService.*                            [探测 / 连接 / 测试 / 截图]
     -> CaptureService.update_config / set_target_port / capture_screenshot
     -> OcrService.create_template / select_template / is_template_loaded / resume_poll

src.ui.data_admin.official_data_import_dialog
  -> CaptureService.submit_official_import()                  [整批官方榜单导入]
  -> load_pending_session()                                   [待复核会话入口]

src.ui.data_admin.official_import_review_dialog
  -> OfficialDataImportService.review_candidates()            [候选名]
  -> OfficialDataImportService.apply_reviewed_records()       [人工修正落盘]
```

### 8.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.capture.adb_screen.AdbCapture` | CaptureService / EmulatorOperationService 直接持有 |
| `src.capture.prober.probe_mumu_adb / test_adb_path / probe_all_devices_with_status` | ADB 路径探测与 MuMu 实例枚举 |
| `src.capture.image_utils.save_image()` | 截图文件保存（image-save 线程） |
| `src.capture.image_validation.load_local_image()` | 本地图片导入 |
| `src.ocr.ocr_loader.get_template_manager()` | 模板管理器单例 |
| `src.ocr.roi_config.OcrRoiConfig` | 每模板 ROI 布局，配置页保存后立即生效 |
| `src.business.recognition.ocr_worker.OcrWorker` | 唯一后台队列，执行模板匹配、OCR、预热与官方榜单导入 |
| `src.ocr.recognizer.GeneralRecognizer` | 由 OcrWorker 缓存和调用 |
| `src.ocr.official_board_parser` | 官方榜单版式切分、横线检测、胜率数字模板 |
| `src.ocr.character_similarity.CharacterSimilarityService` | 官方榜单武将词表纠错 |
| `src.ocr.paddle_loader.create_paddle_ocr` | 官方榜单按需创建简体 / 繁体引擎 |
| `src.config.env.get_mumu_config() / save_env_file() / get_api_config() / PROVIDER_PRESETS` | 模拟器配置与 API 档案读写 |
| `src.scraper.ai.prompt_utils.estimate_cost / estimate_item_cost` | `ai_cost.estimate_generation_cost()` 成本估算 |
| `src.scraper.ai.json_extract.extract_json` | 分类建议 JSON 解析（知识库范围） |
| `src.scraper.ai_batch` / `src.scraper.official` / `src.scraper.incremental` | QProcess 子进程 CLI |
| `src.scraper.official_source.announcement` | 公告 / 百科拉取、武将快照与更新候选计算 |
| `src.data.win_rate_repository.clear_win_rate_cache()` | 2v2 胜率榜写入后清缓存 |
| `src.data.peak_win_rate_repository.clear_peak_win_rate_cache()` | 巅峰赛胜率榜写入后清缓存 |
| `src.data.recommendation_index_repository.mark_recommendation_index_stale()` | 官方榜单导入后标记推荐指数待重建 |
| `src.data.combo_manager.ComboManager` / `src.data.combo_seats.parse_seats` | 实战配队导入合并与座次解析 |
| `src.data.hero_manager / guide_manager / synergy_manager` | 数据清理、失效关联修复与修改事务 |
| `src.data.announcement_manager` / `src.data.hero_timeline.append_announcement_events` | 公告合并去重、百科快照与武将变更时间轴 |
| `src.data.guide_manager.GuideManager` | GuideFetchService / MatchAnalysisService 构造时注入 |
| `src.data.manager.DataIssue` | CardCatalogService 的仓储校验结果模型 |

---

## 九、函数清单总表

### HeroFetchService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `fetch_all() -> bool` | `MainWindow._request_fetch_all()` | `_is_busy()`, `_start_process()` |
| `fetch_incremental() -> bool` | `MainWindow._request_fetch_incremental()` | `_is_busy()`, `_start_process()` |
| `fetch_specific(ids) -> bool` | `MainWindow._request_fetch_specific()` | `_is_busy()`, `_start_process()` |
| `cancel()` | 外部 UI | `cancel_process()` [fetch_utils] |
| `_is_busy()` | `fetch_*()` | `_cancel_requested` / `QProcess.state()` |
| `_start_process(args)` | `fetch_*()` | 清缓冲 + 注入环境变量 + `QProcess.start()` |
| `_on_finished(code)` | `QProcess.finished` → slot | 清缓冲/临时文件 → `error_occurred()`（失败时） |
| `_on_process_finished(code)` | `_on_finished()` | emit `fetch_completed(exit_code == 0)` |
| `_on_error(error)` | `QProcess.errorOccurred` → slot | emit `error_occurred()` |

### GuideFetchService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `fetch_all(heroes, backend, use_rag=True) -> bool` | `AiGenerationWorkflow.request_guide_all()` | `_is_busy()`, `execute_with_confirmation()` |
| `fetch_incremental(heroes, backend, use_rag=True) -> bool` | `AiGenerationWorkflow.request_guide_incremental()` | `_is_busy()`, `guide_mgr.list_guides()`, `execute_with_confirmation()` |
| `fetch_specific(heroes, backend, use_rag=True) -> bool` | `AiGenerationWorkflow.request_guide_specific()` | `_is_busy()`, `execute_with_confirmation()` |
| `execute_with_confirmation()` | `fetch_*()` | 构建参数 → 写临时 JSON → `_start_process()` |
| `cancel()` | 外部 UI | `cancel_process()` [fetch_utils] |
| `_on_stdout_line(line)` | `_dispatch_stdout_line()` | `is_generation_progress_line()`, `parse_generation_event()` |
| `_on_process_finished(code)` | `_on_finished()` | `[code == 0]` emit `fetch_completed(True, ...)` |
| `_cleanup_context()` | `_on_finished()`, `_on_error()` | `_cleanup_tmp_file()` → `os.unlink()` |

### SynergyFetchService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `fetch_pair(heroes, backend, overwrite=False, use_rag=True) -> bool` | `AiGenerationWorkflow.request_synergy_pair()` | `_submit()` → 写 temp JSON → `_start_process()` |
| `fetch_single(hero, all, backend, use_rag=True) -> bool` | `AiGenerationWorkflow.request_synergy_single()` | `_submit()` |
| `fetch_pairs_list(pairs, backend, overwrite=False, use_rag=True) -> bool` | `AiGenerationWorkflow.request_synergy_combos()` | `_submit()`（`--synergy-list`） |
| `reload_from_disk() -> bool` | 取消生成后的收尾 | `SynergyReloadWorker.start()` |
| `_on_reload_loaded(synergies, issues)` | `SynergyReloadWorker.loaded` | `synergy_manager.replace_loaded_data()` → `reload_finished` |
| `cancel()` | 外部 UI | `cancel_process()` [fetch_utils] |

### CaptureService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `do_capture(hero_names, template_name, force_ocr, perform_ocr)` | `RecommendationPanel` / `MatchGuidePanel` / `PeakSelectPanel` | `_adb_executor.submit(capture_screenshot)` → `_capture_ready` |
| `do_capture_from_file(path, names, template_name, force_ocr, perform_ocr)` | `RecommendationPanel` / `MatchGuidePanel` | `QTimer.singleShot(0, _execute_file_ocr)` |
| `capture_screenshot()` | `_adb_executor` / `EmulatorOperationService` | 会话锁 → 必要时连接 → `AdbCapture.screencap_full()` |
| `capture_for_poll(capture)` | `PollCoordinator` | `_adb_executor.submit(_capture_for_poll)` → `(ok, image, failure_kind)` |
| `submit_ocr_task(image, hero_names, template_name, recognize, rois, match_template, fallback_on_template_miss, allow_result_reuse)` | 文件导入、轮询、巅峰赛、官方导入 | 构造 `OcrTask` → `OcrWorker.submit()` |
| `submit_official_import(paths)` | `OfficialDataImportDialog` | `OcrWorker.submit(OfficialImportTask)`；空选择/重叠任务抛错 |
| `warmup_ocr_model(hero_names)` / `wait_ocr_warmup(timeout_ms=15000)` | `MainWindow` / ADB 连接成功 | `OcrWorker.warmup_model()` |
| `connect_emulator()` / `disconnect_emulator()` | `EmulatorOperationService` | `AdbCapture.connect()` / `disconnect()` |
| `update_config(config)` / `set_target_port(port)` | `MainWindow`, `MumuConfigCoordinator` | 路径或端口变化时重建 AdbCapture |
| `shutdown()` | `MainWindow.closeEvent()` | 停止两个执行器 + `OcrWorker.retire()` |

### OcrService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `start_poll(interval_ms)` | `PollCoordinator.sync_with_connection()` | 重置会话与任务状态 → `QTimer.setInterval` + `start()` |
| `stop_poll()` | `PollCoordinator` / 官方导入窗口 | `QTimer.stop()` + 清任务状态 + 递增会话代数 |
| `resume_poll()` | `MumuConfigCoordinator.resume_poll()` | `start_poll()`（仅 `paused` 时） |
| `begin_poll()` / `complete_poll(generation, outcome, detail)` | `PollCoordinator` | 单飞标记、状态迁移、动态调整定时器间隔 |
| `invalidate_inflight_poll()` | `PeakSelectWatcher.start()` | 作废在途会话 + 复位在途标记 |
| `activate_task()` / `deactivate_task()` / `set_task_cooldown()` / `clear_task_cooldown()` / `due_poll_tasks()` | `MainWindow._on_poll_result()` / `PollCoordinator` | 按任务独立维护 `PollTaskState` |
| `create_template(image, roi, template_name)` | `MumuConfigCoordinator` | `get_template_manager(name).set_template()` |
| `select_template(file_path, template_name)` / `delete_template(template_name)` | `MumuConfigCoordinator` | `shutil.copy2()` + `tm.reload()` / `tm.delete_template()` |
| `template_path(name)` / `is_template_loaded(name)` | `MumuConfigCoordinator.template_status()` | 模板管理器只读查询 |
| `set_hero_names(names)` | `MainWindow.__init__()` | 存储 hero_names 供 `run_ocr()` |
| `run_ocr(image, rois)` | 兼容外部同步调用 | 注入的 `submit_ocr_task()`，等待 `OcrTask.completed`（30 秒超时返回 None） |

### 其他业务服务

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `estimate_generation_cost(items, kind, model, use_rag)` | `ai_cost.py` | 生成工作流 / 后端选择对话框 | `estimate_cost()`（guide）/ `estimate_item_cost(use_rag=...)`（synergy） |
| `recommendation_service.RecommendationService.load() / rebuild_indexes() / mark_indexes_stale()` | `analysis/recommendation_service.py` | 推荐面板 / 官方导入后 | 胜率与推荐指数仓储；`rank_win_rates(names)` 取前三 |
| `match_analysis_service.MatchAnalysisService.analyze()` | `analysis/match_analysis_service.py` | 对局攻略面板 | `guide_manager` 攻略 + 单将胜率 → `MatchAnalysis` |
| `combo_import_service.run_import(source, heroes, output)` | `maintenance/combo_import_service.py` | 导入对话框 / CLI 脚本 | 武将名→ID 映射、`parse_seats()`、`ComboManager` 幂等合并 |
| `data_management_service.clear_data()` / `repair_missing_references()` / `update_*` / `delete_*` | `maintenance/data_management_service.py` | 数据管理面板 | `_ManagerTransaction` 备份 + 写入失败回滚 |
| `official_data_import_service.OfficialDataImportService.*` | `recognition/official_data_import_service.py` | `OcrWorker` / 复核界面 | 见"官方榜单数据导入"章节 |


## 十、AnnouncementService（公告更新检查）链路

### 10.1 检查链路与信号拓扑

```
MainWindow._check_announcements()
  -> 忙碌/冷却判断（is_busy / cooldown_remaining）-> QMessageBox 提示弹窗
  -> AnnouncementService.check_now() -> bool
    -> is_busy 检查（_thread 或 _prepare_thread 存活即忙碌）
    -> 冷却检查（CHECK_COOLDOWN_SECONDS = 60 秒最小间隔）
    -> 记 _last_check_started_at -> check_started / status_changed 信号
    -> threading.Thread(_run_check) -> _do_check()
      -> fetch_latest_announcements() + classify_hero_related()
      -> AnnouncementManager.merge_new()
      -> fetch_baike_heroes() -> build_hero_snapshot() -> diff_heroes()
      -> AnnouncementManager.mark_ready_if_updated()
      -> _sync_timeline() -> append_announcement_events()
      -> 阶段令牌 snapshot / pending_saves 挂到 AnnouncementCheckResult
    -> _check_done(object) 内部信号（跨线程排队到 GUI 线程）
    -> _finalize_check() -> 写共享状态 + 持久化快照 -> check_finished(object)
  -> MainWindow._on_announcement_check_finished()

[更新候选准备，独立线程]
  -> collect_base_candidates(local_heroes, announcements, diff)   [纯内存、无网络，UI 预判]
  -> prepare_update_candidates(local_heroes, announcements, diff) -> bool
    -> [is_busy] return False（调用方提示用户）
    -> threading.Thread("announcement-prepare-update") -> _run_prepare()
       -> fetch_baike_heroes()（失败则 official_ok=False）
       -> build_update_candidates(...)
       -> update_candidates_prepared({"candidates", "official_ok", "error"?: str})
```

失败边界：公告/百科拉取异常只记日志并放入 `result.error` / `baike_ok=False`，不覆盖旧快照、不中断应用；`_run_prepare` 的异常被兜底为 `official_ok=False` + `error`，避免 UI 进度条永不消失。
"更新武将数据"由主窗口编排（候选组装 → `HeroUpdateConfirmDialog` 用户确认 → 指定获取/增量链式执行），Service 只负责公告检查、`mark_applied()` 快照刷新与候选准备。

### 10.2 信号与函数清单

```
AnnouncementService.check_started                 -> 主窗口开始检查状态
AnnouncementService.check_finished(object)        -> _on_announcement_check_finished()
AnnouncementService.status_changed(str) / progress_changed(str) -> UI 状态与阶段文字
AnnouncementService.update_candidates_prepared(object) -> 更新候选弹窗
AnnouncementService._check_done(object)           -> _finalize_check()（内部，仅 GUI 线程写共享状态）
```

| 函数 | 职责 |
|------|------|
| `check_now()` -> `bool` | 手动触发一次检查（busy + 冷却防重），未启动返回 False |
| `collect_base_candidates(local, announcements, diff)` | 纯内存候选，供 UI 预判是否有可更新项 |
| `prepare_update_candidates(local, announcements, diff)` -> `bool` | 独立后台线程拉官网百科算字段级差异 |
| `_run_check()` / `_do_check()` | 后台执行检查并返回 `AnnouncementCheckResult` |
| `_finalize_check()` | GUI 线程统一写共享状态、持久化 `pending_saves`、广播 `check_finished` |
| `mark_applied()` | 采集完成后公告置已处理 + 写回 `_last_snapshot` |
| `_sync_timeline()` -> `int` | 武将变更时间轴同步，失败仅记日志返回 0 |

## 十一、知识库相关服务（已迁出）

`RuleDocService`（元规则 T0 文档维护）、`AuditService`（知识库审计）、`RefinementService` / `RefinementSession` / `SuggestController`（索引精化三层架构：对话框 / 纯 Python 状态层 / 线程编排）的调用链已整体迁至 [./call_graph_rag.md](./call_graph_rag.md)，此处不再重复。相关 `scripts/` 协作（`audit_rule_doc.py` / `sync_rule_stats.py` / `propose_rule_changes.py` / `apply_rule_proposal.py` / `eval_rule_faqs.py` / `maintain_rag.py`）亦以该文档为准。

