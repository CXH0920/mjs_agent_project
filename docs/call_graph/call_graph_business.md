# 调用链路：业务服务层

> 对应源码：`src/business/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 虚线 `───` 表示跨越进程边界（QProcess 子进程）。

---

## 一、QProcess 服务通用模式

三个 FetchService（Hero / Guide / Synergy）遵循相同设计模式。以下以 HeroFetchService 为例说明通用结构。

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
| `cancel()` | `process.kill()` + `waitForFinished(3000)` |

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
  -> self._get_heroes_as_dicts()                               [Hero → dict]
  -> estimate_cost(hero_count, "guide")                        [AI 成本估算]
  -> BackendChooseDialog(estimation, title, parent)            [选择 API/浏览器模式]
     -> [API Tab] 显示 Token/费用估算
     -> [浏览器 Tab] 显示 Edge 配置说明
  -> [确认] GuideProgressDialog(hero_count, parent)            [创建进度条对话框]
     -> GuideFetchService.fetch_all(heroes, backend)
       -> _is_busy()
       -> [设置 context = {"mode": "all"}]
       -> execute_with_confirmation()
         -> base_args = ["-m", "src.scraper.ai_batch", "--guide"]
         -> [backend=="browser" 追加 "--browser"]
         -> [增量/指定模式 追加 "--update"]
         -> [增量/指定模式 写入 temp JSON 文件]
         -> _start_process([*base_args, "--heroes-file", tmp_path])
    -> GuideProgressDialog.exec()                              [阻塞等待子进程完成]
  -> [子进程结束]
    -> GuideFetchService._on_finished(exit_code)
      -> _cleanup_tmp()                                        [删除临时文件]
      -> emit fetch_completed(success, message)
        -> MainWindow._on_guide_fetch_completed()
          -> GuideProgressDialog.on_process_finished()
          -> self._data.guides.load()                          [刷新内存缓存]
          -> self._update_status()
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `fetch_all(heroes, backend)` | `guide_fetch_service.py` | `_request_guide_all()` | `_is_busy()`, `execute_with_confirmation()` |
| `fetch_incremental(heroes, backend)` | `guide_fetch_service.py` | `_request_guide_incremental()` | `_is_busy()`, `guide_mgr.list_guides()`, `execute_with_confirmation()` |
| `fetch_specific(heroes, backend)` | `guide_fetch_service.py` | `_request_guide_specific()` | `_is_busy()`, `execute_with_confirmation()` |
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
GuideFetchService.cost_estimated   → MainWindow._on_guide_cost_estimated
  → CostConfirmDialog → GuideProgressDialog → ...

GuideFetchService.status_changed   → MainWindow._on_fetch_status → status_label
GuideFetchService.fetch_completed  → MainWindow._on_guide_fetch_completed → data.load + status
GuideFetchService.error_occurred   → MainWindow._on_guide_fetch_error → QMessageBox
GuideFetchService.progress_output  → MainWindow._on_guide_progress → GuideProgressDialog.update_status
GuideFetchService.progress_value   → MainWindow._on_guide_progress_value → GuideProgressDialog.update_progress
```

---

## 四、SynergyFetchService（相性获取）

### 4.1 两种配对模式

```
MainWindow._request_synergy_pair()
  -> self._get_heroes_as_dicts()
  -> SynergyPairDialog(self._data.heroes)                      [选 2-8 武将]
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
  -> SynergySingleDialog(self._data.heroes)                    [选 1 武将]
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
| `fetch_pair(heroes, backend)` | `synergy_fetch_service.py` | `_request_synergy_pair()` | `_is_busy()`, 写入 temp JSON, `_start_process()` |
| `fetch_single(hero, all, backend)` | `synergy_fetch_service.py` | `_request_synergy_single()` | `_is_busy()`, 写入 temp JSON, `_start_process()` |
| `cancel()` | `synergy_fetch_service.py` | 外部 UI | `cancel_process()` [fetch_utils] |

---

## 五、CaptureService（截图业务编排）

### 5.1 手动截图链路

```
RecommendationPanel._on_import_from_screenshot()
  -> [无 capture service] _open_mumu_config()                  [先配置模拟器]
  -> self._hero_mgr.list_heroes()                             [获取武将名称列表]
  -> CaptureService.capture_completed.connect(...)              [连接信号]
  -> CaptureService.do_capture(hero_names)
    -> QTimer.singleShot(0, self._execute_capture)              [异步：不阻塞 UI]
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
       -> [OCR 启用 || 轮询模式]
          -> get_template_manager()                            [获取模板单例]
          -> tm.match(image, threshold)                        [模板匹配]
          -> [匹配成功]
             -> get_recognizer(rois, hero_names)               [获取识别器单例]
             -> recognizer.recognize(image)                    [PaddleOCR 识别]
             -> GeneralRecognizer.save_results(results, path)   [保存结果]
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
       -> _run_ocr(image, hero_names)                          [同截图链路]
       -> emit capture_completed(result)
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `do_capture(hero_names)` | `capture_service.py` | `_on_import_from_screenshot()` | `QTimer.singleShot(0, _execute_capture)` |
| `do_capture_from_file(path, names)` | `capture_service.py` | `_on_import_from_file()` | `QTimer.singleShot(0, _execute_file_ocr)` |
| `_execute_capture(names)` | `capture_service.py` | `do_capture()` 延迟调用 | `AdbCapture.connect()`, `screencap_full()`, `_run_ocr()` |
| `_execute_file_ocr(path, names)` | `capture_service.py` | `do_capture_from_file()` 延迟调用 | `Image.open()`, `_run_ocr()` |
| `_run_ocr(image, names)` | `capture_service.py` | `_execute_capture/ocr()` | `get_template_manager()`, `get_recognizer()`, `recognizer.recognize()` |
| `connect_emulator()` | `capture_service.py` | 外部 UI | `self._capture.connect()` |
| `disconnect_emulator()` | `capture_service.py` | 外部 UI | `self._capture.disconnect()` |

### 5.3 信号拓扑

```
CaptureService.status_changed      → UI 状态栏
CaptureService.capture_completed   → RecommendationPanel._on_capture_result
  → load_from_ocr()                → update_recommendations()
CaptureService.capture_failed      → UI 错误提示
```

---

## 六、OcrService（OCR 控制服务）

### 6.1 轮询链路

```
MainWindow._on_poll_capture()                               [poll_tick 信号触发]
  -> [in cooldown] return                                    [冷却期内跳过]
  -> [not configured] return
  -> threading.Lock.acquire(blocking=False)                  [防并发]
  -> [后台线程] _do_poll_work()
    -> CaptureService.is_connected
    -> [not connected] CaptureService.connect_emulator()
    -> AdbCapture.screencap_full()
    -> get_template_manager().is_loaded
    -> [loaded] TemplateManager.match(image, threshold)
    -> [matched] CaptureService.run_ocr_if_matched(image, hero_names)
      -> _run_ocr(image, hero_names)                        [同截图链路]
    -> self._poll_result_ready.emit(results, image, matched) [信号]

MainWindow._on_poll_result(ocr_results, image, matched)     [主线程接收]
  -> RecommendationPanel.load_from_ocr(results)              [更新推荐面板]
  -> [matched] OcrService.set_cooldown(180)                  [3 分钟冷却]
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `start_poll(interval_ms)` | `ocr_service.py` | `MumuConfigDialog` 保存 | `QTimer.start()` |
| `stop_poll()` | `ocr_service.py` | `MumuConfigDialog` 保存 | `QTimer.stop()` |
| `run_ocr(image, rois)` | `ocr_service.py` | 外部 | `get_recognizer()`, `recognizer.recognize()` |
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

## 七、fetch_utils（公共工具）

| 函数 | 文件 | 调用方 | 说明 |
|------|------|--------|------|
| `is_process_busy(process, name)` | `fetch_utils.py` | `GuideFetchService._is_busy()`, `SynergyFetchService._is_busy()` | 检查 QProcess 状态 |
| `cancel_process(process)` | `fetch_utils.py` | `GuideFetchService.cancel()`, `SynergyFetchService.cancel()` | kill + waitForFinished |
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
| `src.ocr.ocr_loader.get_recognizer()` | 识别器单例 |
| `src.ocr.recognizer.GeneralRecognizer` | PaddleOCR 识别 |
| `src.config.env.get_mumu_config()` | 读取模拟器配置 |
| `src.config.env.save_env_file()` | 保存模拟器配置 |
| `src.scraper.ai_utils.estimate_cost()` | GuideFetchService 成本估算 |
| `src.data.manager.GuideManager` | GuideFetchService 构造时注入 |

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
| `run_ocr_if_matched(image, names)` | 外部 UI | `get_template_manager()`, `_run_ocr()` |
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
| `run_ocr(image, rois)` | 外部轮询 | `get_recognizer()`, `recognizer.recognize()` |
