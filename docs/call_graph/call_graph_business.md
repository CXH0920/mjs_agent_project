# 调用链路：业务服务层

> 对应源码：`src/business/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 虚线 `───` 表示跨越进程边界（QProcess 子进程）。

---

## 当前实现基线（2026-07-22）

成功语义以子进程退出码为准，`RESULT: FAIL=` 不再是服务协议。AI CLI 失败时以 `sys.exit(1)` 返回，`GuideFetchService` 和 `SynergyFetchService` 只在 `exit_code == 0` 时发送 `fetch_completed(True, ...)`。

```
GuideFetchService.fetch_*() / SynergyFetchService.fetch_*()
  -> BaseFetchService._start_process(args)
    -> QProcess.readyReadStandardOutput -> _on_stdout_ready()
      -> 子类._on_stdout_line(line) -> progress_output/progress_value
    -> QProcess.finished(exit_code) -> _on_finished(exit_code)
      -> _cleanup_context() -> 子类._on_process_finished(exit_code)
```

`cancel_process()` 当前只调用 `kill()`，不在 GUI 线程执行 `waitForFinished()`；进程结束后的临时文件清理、状态通知和上下文释放由 `finished` 信号统一完成。stdout 先累计到字节缓冲，`_dispatch_stdout_lines()` 只分发完整换行行，`_on_finished()` 再 flush 末尾残行，因此 QProcess 分块读取不会破坏 UTF-8 或 `[i/N]` 进度匹配。CLI 仍应输出换行并及时 flush，保证进度及时到达。

## 一、QProcess 服务通用模式

三个 FetchService（Hero / Guide / Synergy）遵循相同设计模式。通用模式方法由 `BaseFetchService`（`src/business/base_fetch_service.py`）提供，三个子类继承后各自实现 `fetch_*` 和 `cancel` 方法。以下以 HeroFetchService 为例说明通用结构。

### 1.1 通用启动链路

```
[UI 菜单操作]
  -> fetch_all() / fetch_incremental() / fetch_specific()
    -> _is_busy()                                              [并发保护]
       -> [process.state() != NotRunning] emit warning + return
    -> _start_process(cli_args)                                [构建参数并启动]
       -> QProcess(self)                                       [创建 QProcess 对象]
       -> setProcessChannelMode(SeparateChannels)               [分离 stdout/stderr]
       -> readyReadStandardOutput.connect(_on_stdout_ready)     [连接信号]
       -> readyReadStandardError.connect(_on_stderr_ready)
       -> finished.connect(_on_finished)
       -> errorOccurred.connect(_on_error)
       -> QProcess.start(sys.executable, cli_args)             [启动子进程]
         ─────────────────────────────────────────────────────────
         [子进程] python -m src.scraper.xxx [args]
         ─────────────────────────────────────────────────────────
  -> [子进程结束] QProcess.finished 信号触发
    -> _on_finished(exit_code)
      -> [exit_code == 0] emit fetch_completed(True, msg)
      -> [exit_code != 0] emit fetch_completed(False, msg)
      -> [Guide/Synergy] 清理临时 JSON 文件
```

| 函数 | 说明 |
|------|------|
| `_is_busy()` | 检查 QProcess.state()，不等待直接返回 |
| `_start_process(args)` | 创建 QProcess + 信号连接 + start |
| `_on_stdout_ready()` | 读取 stdout → emit progress_output + 解析 [i/N] 进度 |
| `_on_stderr_ready()` | 读取 stderr → emit progress_output |
| `_on_finished(code)` | 检查退出码 → emit fetch_completed |
| `_on_error(error)` | QProcess 异常 → emit error_occurred |
| `cancel()` | `cancel_process()`；仅 `process.kill()`，由 `finished` 信号异步收尾 |

> 以上通用方法定义在 `src/business/base_fetch_service.py` 的 `BaseFetchService` 中，三个子类通过继承复用。

### 1.2 stdout / stderr 分块处理

```
QProcess.readyReadStandardOutput
  -> BaseFetchService._read_stdout()
     -> _stdout_buffer.extend(data)                         [完整日志]
     -> _stdout_line_buffer.extend(data)                    [实时解析]
     -> _dispatch_stdout_lines()
        -> partition(b"\\n")                               [只取完整行]
        -> _dispatch_stdout_line(raw_line)
           -> raw_line.decode("utf-8", errors="replace")
           -> _on_stdout_line(line)                          [子类解析进度]

QProcess.finished
  -> _read_stdout() / _read_stderr()
  -> _dispatch_stdout_lines(flush=True)                      [分发无换行的最后一行]
  -> _cleanup_context()
  -> 子类._on_process_finished(exit_code)
```

`_stdout_buffer` 仅保存完整 stdout 供失败日志使用；`_stdout_line_buffer` 保存尚未遇到换行的字节尾部。两个缓冲区职责不同，不能用结束日志缓冲替代实时解析缓冲。

---

## 二、HeroFetchService（武将采集）

### 2.1 三种采集模式

```
fetch_all()
  -> _start_process(["-m", "src.scraper.official"])

fetch_incremental()
  -> _start_process(["-m", "src.scraper.incremental", "--incremental"])

fetch_specific(hero_ids: list[int])
  -> _start_process(["-m", "src.scraper.incremental",
                     "--hero-id", ",".join(str(i) for i in hero_ids)])
```

| 方法 | 调用方 | 子进程模块 | 说明 |
|------|--------|-----------|------|
| `fetch_all()` | `_request_fetch_all()` | `src.scraper.official` | 全量覆盖 |
| `fetch_incremental()` | `_request_fetch_incremental()` | `src.scraper.incremental` | 仅增量 |
| `fetch_specific(ids)` | `_request_fetch_specific()` | `src.scraper.incremental` | 按 ID 覆盖 |

### 2.2 信号拓扑

```
HeroFetchService.status_changed    → MainWindow._on_fetch_status   → status_label.setText()
HeroFetchService.fetch_completed   → MainWindow._on_fetch_completed → QMessageBox
HeroFetchService.error_occurred    → MainWindow._on_fetch_error    → QMessageBox.warning()
```

### 2.3 函数清单

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `__init__(parent)` | `fetch_service.py` | `MainWindow.__init__()` | 初始化 QProcess=None |
| `fetch_all()` | `fetch_service.py` | `MainWindow._request_fetch_all()` | `_is_busy()`, `_start_process()` |
| `fetch_incremental()` | `fetch_service.py` | `MainWindow._request_fetch_incremental()` | `_is_busy()`, `_start_process()` |
| `fetch_specific(ids)` | `fetch_service.py` | `MainWindow._request_fetch_specific()` | `_is_busy()`, `_start_process()` |
| `cancel()` | `fetch_service.py` | 外部 UI | `QProcess.kill()` |
| `_is_busy()` | `fetch_service.py` | `fetch_*()` | `QProcess.state()` 检查 |
| `_start_process(args)` | `fetch_service.py` | `fetch_*()` | `QProcess.start()` |
| `_on_finished(exit_code)` | `fetch_service.py` | `QProcess.finished` | emit `fetch_completed()` |
| `_on_error(error)` | `fetch_service.py` | `QProcess.errorOccurred` | emit `error_occurred()` |

---

## 三、GuideFetchService（攻略生成）

### 3.1 完整调用链

```
MainWindow._request_guide_all()
  -> AiGenerationWorkflow.request_guide_all()
    -> _get_heroes_as_dicts()                                   [Hero → dict]
    -> estimate_cost(hero_count, "guide")                      [AI 成本估算]
    -> BackendChooseDialog(estimation, title, parent)          [选择 API/浏览器模式]
     -> [API Tab] 显示 Token/费用估算
     -> [浏览器 Tab] 显示 Edge 配置说明
    -> [确认] GuideProgressDialog(hero_count, parent)          [创建进度条对话框]
      -> GuideFetchService.fetch_all(heroes, backend)
       -> _is_busy()
       -> [设置 context = {"mode": "all"}]
       -> execute_with_confirmation()
         -> base_args = ["-m", "src.scraper.ai_batch", "--guide"]
         -> [backend=="browser" 追加 "--browser"]
         -> [增量/指定模式 追加 "--update"]
         -> [增量/指定模式 写入 temp JSON 文件]
         -> _start_process([*base_args, "--heroes-file", tmp_path])
      -> GuideProgressDialog.exec()                            [模态事件循环等待子进程完成]
  -> [子进程结束]
    -> GuideFetchService._on_finished(exit_code)
      -> _cleanup_tmp()                                        [删除临时文件]
      -> emit fetch_completed(success, message)
        -> AiGenerationWorkflow._on_guide_completed()
          -> GuideProgressDialog.on_process_finished()
          -> self._guide_manager.load()                        [刷新内存缓存]
          -> emit guides_changed
            -> MainWindow._on_guides_generated() -> _update_status()
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `fetch_all(heroes, backend)` | `guide_fetch_service.py` | `AiGenerationWorkflow.request_guide_all()` | `_is_busy()`, `execute_with_confirmation()` |
| `fetch_incremental(heroes, backend)` | `guide_fetch_service.py` | `AiGenerationWorkflow.request_guide_incremental()` | `_is_busy()`, `guide_mgr.list_guides()`, `execute_with_confirmation()` |
| `fetch_specific(heroes, backend)` | `guide_fetch_service.py` | `AiGenerationWorkflow.request_guide_specific()` | `_is_busy()`, `execute_with_confirmation()` |
| `execute_with_confirmation()` | `guide_fetch_service.py` | `fetch_*()` | 构建参数 → `_start_process()` |
| `cancel()` | `guide_fetch_service.py` | 外部 UI | `cancel_process()` [fetch_utils] |
| `_start_process(args)` | `guide_fetch_service.py` | `execute_with_confirmation()` | `QProcess.start()` |
| `_on_stdout_ready()` | `guide_fetch_service.py` | `QProcess.readyReadStandardOutput` | emit `progress_output`, `progress_value` |
| `_on_stderr_ready()` | `guide_fetch_service.py` | `QProcess.readyReadStandardError` | emit `progress_output` |
| `_on_finished(code)` | `guide_fetch_service.py` | `QProcess.finished` | `_cleanup_tmp()`, emit `fetch_completed()` |
| `_on_error(error)` | `guide_fetch_service.py` | `QProcess.errorOccurred` | `_cleanup_tmp()`, emit `error_occurred()` |
| `_cleanup_tmp()` | `guide_fetch_service.py` | `_on_finished()`, `_on_error()` | `os.unlink(tmp_path)` |

### 3.2 信号拓扑

```
GuideFetchService.status_changed   → AiGenerationWorkflow.status_changed → MainWindow._on_fetch_status
GuideFetchService.fetch_completed  → AiGenerationWorkflow._on_guide_completed → GuideManager.load + guides_changed
GuideFetchService.error_occurred   → AiGenerationWorkflow._on_guide_error → QMessageBox
GuideFetchService.progress_output  → AiGenerationWorkflow._on_guide_progress → GuideProgressDialog.update_status
GuideFetchService.progress_value   → AiGenerationWorkflow._on_guide_progress_value → GuideProgressDialog.update_progress

`cost_estimated` 与 `CostConfirmDialog` 仍保留在代码中，但不属于当前 AI 生成 UI 路径；费用估算由工作流传入 `BackendChooseDialog` 展示。
```

---

## 四、SynergyFetchService（相性获取）

### 4.1 两种配对模式

```
MainWindow._request_synergy_pair()
  -> AiGenerationWorkflow.request_synergy_pair()
  -> _require_heroes()
  -> SynergyPairDialog(hero_manager)                            [选 2-8 武将]
     -> BaseHeroSelectDialog(MULTI_LIMIT, max_selection=8)
     -> 用户勾选 → _on_accept → _set_result_by_ids()
  -> BackendChooseDialog(title)                                [选择后端]
  -> GuideProgressDialog(pair_count, title)
     -> SynergyFetchService.fetch_pair(selected, backend)
       -> _is_busy()
       -> [写入选中武将到 temp JSON]
       -> _start_process(["-m", "src.scraper.ai_batch",
                          "--synergy-pair", tmp_path])
  -> GuideProgressDialog.exec()


MainWindow._request_synergy_single()
  -> AiGenerationWorkflow.request_synergy_single()
  -> SynergySingleDialog(hero_manager)                          [选 1 武将]
     -> BaseHeroSelectDialog(SINGLE)
  -> GuideProgressDialog(hero_count, title)
     -> SynergyFetchService.fetch_single(hero, all_heroes, backend)
       -> _is_busy()
       -> [写入 1 个武将到 temp JSON]
       -> _start_process(["-m", "src.scraper.ai_batch",
                          "--synergy-single", tmp_path])
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `fetch_pair(heroes, backend)` | `synergy_fetch_service.py` | `AiGenerationWorkflow.request_synergy_pair()` | `_is_busy()`, 写入 temp JSON, `_start_process()` |
| `fetch_single(hero, all, backend)` | `synergy_fetch_service.py` | `AiGenerationWorkflow.request_synergy_single()` | `_is_busy()`, 写入 temp JSON, `_start_process()` |
| `cancel()` | `synergy_fetch_service.py` | 外部 UI | `cancel_process()` [fetch_utils] |

---

## 五、CaptureService（截图业务编排）

### 5.1 手动截图链路

```
RecommendationPanel._on_import_from_screenshot()
  -> [无 capture service] _open_mumu_config()                  [先配置模拟器]
  -> CaptureService.do_capture(perform_ocr=False)
    -> QTimer.singleShot(0, self._execute_capture)              [延后回调；ADB 截图仍在 GUI 线程]
       -> self._capture.connect()                              [ADB 连接]
          -> AdbCapture.connect()
            -> _check_adb_valid()
            -> _run_adb("connect", target)
            -> _get_devices()
       -> self._capture.screencap_full()                       [ADB 截图]
          -> subprocess.run(["adb", "-s", serial,
                             "exec-out", "screencap", "-p"])
          -> Image.open(BytesIO(result.stdout))
       -> save_image(image, save_path)                         [保存截图]
       -> emit capture_completed({ocr_results, image, ...})    [发送结果]
```

### 5.2 从文件导入截图链路

```
RecommendationPanel._on_import_from_file()
  -> QFileDialog.getOpenFileName(...)                          [选择图片文件]
  -> CaptureService.capture_completed.connect(...)
  -> CaptureService.do_capture_from_file(file_path, hero_names)
    -> QTimer.singleShot(0, _execute_file_ocr)
       -> PIL.Image.open(file_path)
       -> _queue_capture_ocr()
          -> submit_ocr_task() -> OcrWorker.submit(OcrTask)
             -> OcrWorker._execute() -> 模板匹配 -> OCR
          -> _on_ocr_task_completed() -> emit capture_completed(result)
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `do_capture(hero_names)` | `capture_service.py` | `_on_import_from_screenshot()` | `QTimer.singleShot(0, _execute_capture)` |
| `do_capture_from_file(path, names)` | `capture_service.py` | `_on_import_from_file()` | `QTimer.singleShot(0, _execute_file_ocr)` |
| `_execute_capture(...)` | `capture_service.py` | `do_capture()` 延迟调用 | `connect_emulator()`, `screencap_full()`, `save_image()`；按参数决定是否入 OCR 队列 |
| `_execute_file_ocr(...)` | `capture_service.py` | `do_capture_from_file()` 延迟调用 | `Image.open()`, `_queue_capture_ocr()` |
| `submit_ocr_task(...)` | `capture_service.py` | 手动文件导入、轮询 | 构造 `OcrTask` -> `OcrWorker.submit()` |
| `_on_ocr_task_completed(task)` | `capture_service.py` | `OcrWorker.task_completed` | 合并待处理截图上下文 -> `capture_completed` |
| `connect_emulator()` | `capture_service.py` | 外部 UI | `self._capture.connect()` |
| `disconnect_emulator()` | `capture_service.py` | 外部 UI | `self._capture.disconnect()` |
| `capture_screenshot()` | `capture_service.py` | `EmulatorOperationService` | 共享会话锁 → 必要时连接 → `screencap_full()` |

### 5.3 信号拓扑

```
CaptureService.status_changed      → UI 状态栏
CaptureService.capture_completed   → RecommendationPanel._on_capture_result
  → load_from_ocr()                → update_recommendations()
CaptureService.capture_failed      → UI 错误提示
```

---

### 5.4 EmulatorOperationService（配置页后台操作）

```
MumuConfigDialog
  -> EmulatorOperationService.refresh_devices()
    -> [探测线程] probe_all_devices_with_status() [失败重试一次]
    -> devices_refreshed -> MumuConfigDialog._on_devices_refreshed()
    -> device_refresh_failed -> 保留现有设备选择并显示失败状态

  -> EmulatorOperationService.connect() / disconnect()
    -> [ADB 会话线程] CaptureService.connect_emulator() / disconnect_emulator()
    -> connection_finished / disconnection_finished -> 配置页状态与提示

  -> EmulatorOperationService.capture_template_screenshot(template_name)
    -> [ADB 会话线程] CaptureService.capture_screenshot()
    -> screenshot_ready -> MumuConfigDialog._on_template_screenshot_ready()
      -> RoiSelectorDialog [UI 鼠标框选]
      -> OcrService.create_template(image, roi, template_name)
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `detect_adb()` | `emulator_operation_service.py` | 配置页自动探测 | `probe_mumu_adb()`, `test_adb_path()` |
| `refresh_devices()` | `emulator_operation_service.py` | 配置页刷新/初始化 | `probe_all_devices_with_status()` → 成功/失败信号 |
| `connect()` / `disconnect()` | `emulator_operation_service.py` | 配置页连接按钮 | `CaptureService` 共享会话 |
| `test_device(path, port)` | `emulator_operation_service.py` | 配置页测试按钮 | 临时 `AdbCapture.connect()` + `check_device()` |
| `capture_template_screenshot()` | `emulator_operation_service.py` | 两类模板制作按钮 | `CaptureService.capture_screenshot()` |

---

## 六、OcrService（OCR 控制服务）

### 6.1 轮询链路

```
MainWindow._on_poll_capture()                               [poll_tick 信号触发]
  -> OcrService.begin_poll() -> due_poll_tasks()              [会话与独立任务冷却]
  -> threading.Lock.acquire(blocking=False)                  [防并发]
  -> [后台线程] _do_poll_work()
    -> AdbCapture.connect()                                   [必要时]
    -> AdbCapture.screencap_full()
    -> 每个到期页面调用 CaptureService.submit_ocr_task()
       -> OcrWorker._execute() -> 模板匹配 -> 必要时 OCR
    -> self._poll_result_ready.emit(result)                   [信号]

MainWindow._on_poll_result(result)                            [主线程接收]
  -> OcrService.complete_poll(generation, outcome)
  -> [hero_selection 命中] RecommendationPanel.load_from_ocr()
  -> [match_guide 命中] MatchGuidePanel.update_block()
  -> OcrService.set_task_cooldown(task_name, seconds)
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `start_poll(interval_ms)` | `ocr_service.py` | `MumuConfigDialog` 保存 | `QTimer.start()` |
| `stop_poll()` | `ocr_service.py` | `MumuConfigDialog` 保存 | `QTimer.stop()` |
| `run_ocr(image, rois)` | `ocr_service.py` | 兼容外部同步调用 | 通过注入的 `CaptureService.submit_ocr_task()` 等待 `OcrTask` |
| `create_template(image, roi)` | `ocr_service.py` | `MumuConfigDialog` | `get_template_manager().set_template()` |
| `select_template(file_path)` | `ocr_service.py` | `MumuConfigDialog` | `shutil.copy2()`, `tm.reload()` |
| `delete_template()` | `ocr_service.py` | `MumuConfigDialog` | `get_template_manager().delete_template()` |

### 6.2 信号拓扑

```
OcrService.poll_tick              → MainWindow._on_poll_capture → 后台线程截图+OCR
OcrService.template_changed       → UI 模板状态更新
OcrService.ocr_completed          → UI 获取识别结果
```

---

## 官方榜单数据导入

官方榜单导入不经过 QProcess、ADB、模板匹配或通用 `OcrWorker` 队列。一个 `OfficialDataImportWorker` 串行处理已选图片，并用 Qt 信号向弹窗报告当前文件进度。

```
MainWindow._open_official_data_import()
  -> OfficialDataImportDialog.exec()
    -> _start_import()
      -> OfficialDataImportWorker(paths).start()
        -> [QThread] OfficialDataImportWorker.run()
          -> 对每个已选文件 emit progress_changed(status, 0, 0)
          -> OfficialDataImportService.import_file(key, path, progress_callback, status_callback)
            -> _read_image() -> cv2.imdecode()
            -> _extract_panels() -> 固定版式裁出左右表
            -> _find_data_boundaries() -> HoughLinesP 横线 -> 行边界
            -> 计算 total_steps（胜率表：模板准备 + 行识别；其余表：行识别）
            -> progress_callback(0, total_steps)
            -> [每个面板] _prepare_rate_templates()（仅胜率表）
               -> _build_rank_digit_templates()
               -> _recognize_cell() -> 每行完成后推进进度
            -> [每行] _recognize_row()
               -> 排名/普通单元格: _recognize_cell()
               -> 武将单元格: _recognize_name_cell()
                  -> [同首字无法唯一确认] _rare_char_engine（懒加载 chinese_cht）
                  -> _recognize_name_with_engine() -> 词表校正 -> [精确命中才采用]
                  -> status_callback("正在执行罕见字兜底识别")
               -> 胜率单元格: 预计算 OCR + _recognize_rate_with_templates()
            -> _review_reasons() -> 必要时 _save_review_crop()
            -> _write_csv() -> 临时文件 replace 正式 CSV
            -> [胜率 CSV] clear_win_rate_cache()
          -> emit completed(summaries)
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
     -> 拼接 -> _correct_with_hero_list()
     -> [命中词表] 返回补识别名称
  -> [逐字失败] hero.startswith(单字) 的候选数量
     -> 唯一 -> 返回唯一候选
     -> 多个/零个 -> _rare_char_engine（懒加载繁体模型）
        -> [模型可用] _recognize_name_with_engine()
           -> 整格精确匹配 / 完整候选词表校正 / 逐字校正
           -> [最终命中词表] 返回完整名称
        -> [模型不可用或仍不匹配] 返回原结果 -> _review_reasons() 记录“武将名称疑似缺字”
```

| 函数/信号 | 调用方 | 关键下游 | 说明 |
|---|---|---|---|
| `OfficialDataImportWorker.run()` | `OfficialDataImportDialog._start_import()` | `import_file()`、`progress_changed`、`completed/failed` | 同线程内顺序处理一张或两张图片 |
| `import_file()` | Worker | 图像读取、行检测、识别、CSV 原子写入 | 一张图片可产出一个或两个 CSV |
| `_recognize_name_cell()` | `_recognize_row()` | 候选汇总、逐字兜底、繁体罕见字兜底、词表校正 | 仅官方导入使用，不影响常规 OCR |
| `_review_reasons()` | `import_file()` | `_save_review_crop()` | 单字、低置信度、胜率失败或排名不一致进入复核 |
| `progress_changed(status, current, total)` | Worker | `OfficialDataImportDialog._on_progress_changed()` | 先不定进度，行数确定后显示精确进度；`current < 0` 仅更新罕见字状态 |

**输出关系：**2v2 左表写入 `2v2胜率排行.csv`，右表写入 `2v2出场排行.csv`；放逐图左右表按视觉行序合并为 `武将放逐.csv`。每份正式 CSV 均有对应待复核 CSV；异常截图位于 `screenshot_data/official_import/`。

## 七、fetch_utils（公共工具）

| 函数 | 文件 | 调用方 | 说明 |
|------|------|--------|------|
| `is_process_busy(process, name)` | `fetch_utils.py` | `GuideFetchService._is_busy()`, `SynergyFetchService._is_busy()` | 检查 QProcess 状态 |
| `cancel_process(process)` | `fetch_utils.py` | `BaseFetchService.cancel()` | 仅 kill；由 `finished` 信号触发统一收尾 |
| `get_qprocess_error_name(error)` | `fetch_utils.py` | `_on_error()` | 错误码→中文描述 |
| `log_process_error(name, process)` | `fetch_utils.py` | `_on_error()` | 日志 + 错误信息拼接 |

---

## 八、外部调用关系总览

### 8.1 本模块被外部调用

```
src.ui.main_window
  -> HeroFetchService.*                                      [武将采集]
  -> GuideFetchService.*                                     [攻略生成]
  -> SynergyFetchService.*                                   [相性获取]
  -> CaptureService.*                                        [截图]
  -> OcrService.*                                            [OCR 控制/轮询]

src.ui.recommendation_panel
  -> CaptureService.do_capture()                              [手动截图]
  -> CaptureService.do_capture_from_file()                    [文件导入]
  -> CaptureService.connect_emulator()                        [连接模拟器]

src.ui.mumu_config_dialog
  -> CaptureService.update_config()                           [配置更新]
  -> OcrService.create_template()                             [制作模板]
  -> OcrService.select_template()                             [选择模板]
  -> OcrService.delete_template()                             [删除模板]
```

### 8.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.capture.adb_screen.AdbCapture` | CaptureService 直接持有 |
| `src.capture.image_utils.save_image()` | 截图文件保存 |
| `src.ocr.ocr_loader.get_template_manager()` | 模板管理器单例 |
| `src.business.ocr_worker.OcrWorker` | 唯一后台队列，执行模板匹配与 OCR |
| `src.ocr.recognizer.GeneralRecognizer` | 由 OcrWorker 缓存和调用 |
| `src.config.env.get_mumu_config()` | 读取模拟器配置 |
| `src.config.env.save_env_file()` | 保存模拟器配置 |
| `src.scraper.ai_utils.estimate_cost()` | GuideFetchService 成本估算 |
| `src.data.guide_manager.GuideManager` | GuideFetchService 构造时注入 |

---

## 九、函数清单总表

### HeroFetchService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `fetch_all()` | `MainWindow._request_fetch_all()` | `_is_busy()`, `_start_process()` |
| `fetch_incremental()` | `MainWindow._request_fetch_incremental()` | `_is_busy()`, `_start_process()` |
| `fetch_specific(ids)` | `MainWindow._request_fetch_specific()` | `_is_busy()`, `_start_process()` |
| `cancel()` | 外部 UI | `QProcess.kill()` |
| `_is_busy()` | `fetch_*()` | `QProcess.state()` |
| `_start_process(args)` | `fetch_*()` | `QProcess.start()` |
| `_on_finished(code)` | `QProcess.finished` → slot | emit `fetch_completed()` |
| `_on_error(error)` | `QProcess.errorOccurred` → slot | emit `error_occurred()` |

### GuideFetchService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `fetch_all(heroes, backend)` | `MainWindow._request_guide_all()` | `_is_busy()`, `execute_with_confirmation()` |
| `fetch_incremental(heroes, backend)` | `MainWindow._request_guide_incremental()` | `_is_busy()`, `execute_with_confirmation()` |
| `fetch_specific(heroes, backend)` | `MainWindow._request_guide_specific()` | `_is_busy()`, `execute_with_confirmation()` |
| `execute_with_confirmation()` | `fetch_*()` | 构建参数, `_start_process()` |
| `cancel()` | 外部 UI | `cancel_process()` [fetch_utils] |
| `_cleanup_tmp()` | `_on_finished()`, `_on_error()` | `os.unlink()` |

### SynergyFetchService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `fetch_pair(heroes, backend)` | `MainWindow._request_synergy_pair()` | 写入 temp JSON, `_start_process()` |
| `fetch_single(hero, all, backend)` | `MainWindow._request_synergy_single()` | 写入 temp JSON, `_start_process()` |
| `cancel()` | 外部 UI | `cancel_process()` [fetch_utils] |

### CaptureService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `do_capture(names)` | `RecommendationPanel` | `QTimer.singleShot(0, _execute_capture)` |
| `do_capture_from_file(path, names)` | `RecommendationPanel` | `QTimer.singleShot(0, _execute_file_ocr)` |
| `connect_emulator()` | 外部 UI | `self._capture.connect()` |
| `disconnect_emulator()` | 外部 UI | `self._capture.disconnect()` |
| `run_ocr_if_matched(image, names)` | 非 GUI 调度路径 | `submit_ocr_task()`，等待 `OcrTask.completed` |
| `submit_ocr_task(...)` | 文件导入、轮询 | `OcrWorker.submit()` |
| `update_config(config)` | `MainWindow`, `MumuConfigDialog` | 重建 AdbCapture |

### OcrService

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `start_poll(interval_ms)` | `MumuConfigDialog` | `QTimer.start()` |
| `stop_poll()` | `MumuConfigDialog` | `QTimer.stop()` |
| `create_template(image, roi)` | `MumuConfigDialog` | `get_template_manager().set_template()` |
| `select_template(file_path)` | `MumuConfigDialog` | `shutil.copy2()`, `tm.reload()` |
| `delete_template()` | `MumuConfigDialog` | `get_template_manager().delete_template()` |
| `set_hero_names(names)` | `MainWindow.__init__()` | 存储 hero_names |
| `run_ocr(image, rois)` | 兼容外部同步调用 | 注入的 `submit_ocr_task()`，等待 `OcrTask.completed` |
