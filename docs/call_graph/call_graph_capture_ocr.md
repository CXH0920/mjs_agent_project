# 调用链路：屏幕采集与 OCR 识别

> 对应源码：`src/capture/` + `src/ocr/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> `[性能标注]` 标注了可能影响 UI 响应速度的关键路径。

---

## 当前实现基线（2026-07-22）

模板匹配和 OCR 由唯一 `OcrWorker` 串行执行；`OcrService` 管理模板和轮询状态，`CaptureService` 提交实际任务。

```
CaptureService.do_capture() / do_capture_from_file()
  -> _execute_capture() / _execute_file_ocr()
  -> CaptureService.submit_ocr_task()
    -> OcrWorker.submit(OcrTask)
       -> OcrWorker._execute()
          -> TemplateManager(template_name).match()
          -> GeneralRecognizer.recognize()                      [命中且需要识别时]
  -> CaptureService._on_ocr_task_completed()
  -> capture_completed -> RecommendationPanel / MainWindow
```

轮询：`OcrService.start_poll()` -> `_schedule_poll()` -> `poll_tick` -> `PollCoordinator._on_poll_tick()`。协调器在短生命周期后台线程执行 `AdbCapture.screencap_full()`，随后为每个到期页面提交 `CaptureService.submit_ocr_task()`；其在 GUI 线程过滤过期结果、调用 `complete_poll()`，再通过 `poll_result_ready` 通知主窗口更新界面。`hero_selection` 命中会重置并激活一次 `match_guide`；后者命中后立即停用，直到下次选将命中才可再次执行。前置条件缺失会暂停，其他失败指数退避。

## 一、ADB 连接与截图链路

### 1.1 连接模拟器

```
AdbCapture.connect()
  -> _resolve_target()                                          [解析本次精确目标]
     -> [device_serial 已设置] return device_serial
     -> [端口 > 0] return "127.0.0.1:port"
     -> [否则] probe_running_devices()                          [无显式目标时探测运行实例]
        -> [恰好 1 个] return "127.0.0.1:<adb_port>"
        -> [0 个] return error "未检测到运行中的 MuMu 实例"
        -> [多个] return error "请…选择设备"
  -> [已连接] check_device()                                    [复用会话前先验证]
     -> [在线] return (True, "已处于连接状态")
     -> [失效] _invalidate_connection() 后继续重连
  -> _check_adb_valid()                                       [校验 adb.exe 存在性]
     -> Path.exists() and Path.is_file()                      [文件系统检查]
  -> _run_adb("connect", target)                               [adb connect 命令，默认 10s 超时]
     -> subprocess.run([adb_path, "connect", target], timeout=10)
  -> [失败] return (False, "ADB 连接失败: ...")
  -> _get_device_state(target)                                 [精确校验目标设备]
     -> _run_adb("-s", serial, "get-state")
     -> [state != "device"] _disconnect_safe() + return (False, ...)
  -> self._connected = True
  -> self._device_serial = target
  -> return (True, "连接成功 (设备: ...)")
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `connect()` | `adb_screen.py` | `CaptureService` | `_resolve_target()`, `check_device()`, `_check_adb_valid()`, `_run_adb(connect)`, `_get_device_state()` |
| `_resolve_target()` | `adb_screen.py` | `connect()` | `probe_running_devices()`（延迟导入，避免循环依赖） |
| `_check_adb_valid()` | `adb_screen.py` | `connect()` | `Path.exists()`, `Path.is_file()` |
| `_get_device_state(serial)` | `adb_screen.py` | `connect()`, `check_device()` | `_run_adb("-s", serial, "get-state")` |
| `check_device()` | `adb_screen.py` | `connect()`、截图前会话校验 | `_get_device_state()`, `_invalidate_connection()` |
| `_disconnect_safe()` | `adb_screen.py` | `disconnect()`, `connect()` 失败分支 | `_run_adb("disconnect", timeout=5)` |
| `_run_adb(*args, timeout)` | `adb_screen.py` | `connect()`, `_get_device_state()`, `_disconnect_safe()` | `subprocess.run([adb, *args])` |

### 1.2 全屏截图

```
AdbCapture.screencap_full(log_success=True)
  -> [未连接] return (False, "尚未连接，请先连接模拟器")
  -> for attempt in 1..3:                                     [最多 3 次尝试，间隔 0.15s]
     -> subprocess.run([adb, "-s", serial, "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=15)        [ADB 截图命令]
     -> [returncode != 0]
        -> _is_device_unavailable(stderr)                      [offline/transport closed 等标记]
        -> [命中] _invalidate_connection()                     [清除失效会话]
        -> return (False, "screencap 失败: ...")                [不重试]
     -> [stdout 为空] error = "截图返回空数据"
     -> load_png_image_bytes(stdout)                          [格式/体积/像素校验 + 强制解码]
        -> 6 MiB 上限 → 实际 PNG 格式 → 4,000,000 像素上限
        -> Image.verify() → 重新打开 → load() → copy()
     -> [解析成功] log_success 时记录 ADB 命令耗时与 PNG 解码耗时
        -> return (True, image)
     -> [解析失败或空] 未到上限则 warning + sleep(0.15) 后重试
  -> return (False, error)
```

| 函数 | 文件 | 调用方 | 说明 |
|------|------|--------|------|
| `screencap_full(log_success=True)` | `adb_screen.py` | `CaptureService._execute_capture()`、轮询线程 | ADB 截屏→PIL Image；关键字参数，轮询传 `False` 抑制成功日志 |
| `load_png_image_bytes(data)` | `image_validation.py` | `screencap_full()` | ADB 返回数据的格式、体积、像素校验 |

> **说明：** 使用 `exec-out` 模式而非 `shell screencap`，直接输出二进制到 stdout，不经过设备 shell 解析。

---

## 二、MuMu 设备探测链路

### 2.1 自动探测 ADB 路径

```
probe_mumu_adb()
  -> shutil.which("adb")                                      [PATH 查找]
  -> [找到] return path
  -> _get_mumu_candidates()                                   [收集候选安装根]
     -> [环境变量] os.environ.get("MUMU_HOME")
     -> [注册表] _probe_mumu_registry()
        -> 依次读取 HKLM\SOFTWARE\Netease\MuMuPlayer12 与
           HKLM\SOFTWARE\WOW6432Node\Netease\MuMuPlayer12 的 InstallDir
     -> [硬编码] 8 个常见安装路径
  -> [逐个候选根] 依次尝试 nx_main\adb.exe、
     emulator\nemu\EmulatorShell\adb.exe                      [命中即 resolve() 返回]
  -> _get_legacy_candidates()                                 [备选旧版本路径，同样两个子路径]
  -> return ""  (未找到)
```

### 2.2 探测 MuMu 实例

```
probe_all_devices_with_status(retries=1)
  -> _find_mumu_root()                                        [查找 MuMu 安装目录]
     -> _get_mumu_candidates() + _get_legacy_candidates()
     -> [检查 nx_main 子目录存在性]
  -> [根目录或 MuMuManager.exe 缺失] return ([], error)
  -> for attempt in 0..retries:
     -> subprocess.run([MuMuManager.exe, "info", "--vmindex", "all"],
                       capture_output=True, timeout=10)        [列出所有实例]
     -> [非零退出] error = "MuMuManager 查询失败（退出码 N）"
     -> json.loads(stdout)                                    [解析实例列表]
     -> [JSON/超时/OSError 异常] error = "MuMuManager 查询异常：..."
     -> [失败且未到最后一次] sleep(0.2) 后重试
  -> return ([MuMuDeviceInfo(index, name, adb_port,
              is_running, is_main), ...], "")

probe_all_devices()
  -> probe_all_devices_with_status() -> 丢弃 error，仅返回实例列表

probe_running_devices()
  -> probe_all_devices()
  -> filter: is_running and adb_port > 0
  -> return 运行中实例列表
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `probe_mumu_adb()` | `prober.py` | `EmulatorOperationService.detect_adb()` | `_get_mumu_candidates()`, `_get_legacy_candidates()`, `shutil.which()` |
| `probe_all_devices_with_status(retries=1)` | `prober.py` | `EmulatorOperationService.refresh_devices()` | `_find_mumu_root()`, `subprocess.run(MuMuManager info --vmindex all)`, `json.loads()` |
| `probe_all_devices()` | `prober.py` | `probe_running_devices()` | `probe_all_devices_with_status()` |
| `probe_running_devices()` | `prober.py` | `AdbCapture._resolve_target()` | `probe_all_devices()` |
| `_probe_mumu_registry()` | `prober.py` | `_get_mumu_candidates()` | `winreg.OpenKey` / `QueryValueEx` |
| `_get_mumu_candidates()` / `_get_legacy_candidates()` | `prober.py` | `probe_mumu_adb()`, `_find_mumu_root()` | `os.environ.get()`, 注册表读取 |
| `_find_mumu_root()` | `prober.py` | `probe_all_devices_with_status()` | `_get_mumu_candidates()`, `_get_legacy_candidates()`, `Path.exists()` |
| `test_adb_path(adb_path)` | `prober.py` | `EmulatorOperationService.detect_adb()` | `subprocess.run(adb version)` |

---

## 三、模板匹配链路

### 3.1 制作模板

```
TemplateManager.set_template(image, roi)
  -> 校验: w >= 10 且 h >= 10（过小抛 ValueError）
  -> 校验: roi 在图像范围内（超出抛 ValueError）
  -> [PIL 输入] cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
  -> cropped = image[y:y+h, x:x+w]                            [OpenCV 裁剪]
  -> cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)                [转灰度]
  -> cv2.imencode(ext, gray) -> open("wb").write(buf.tobytes())
     [cv2.imwrite 用 ANSI fopen 不支持中文路径，改 imencode + 二进制写入]
  -> 内存置 _template = gray, _reference_size = (img_w, img_h),
     _template_roi = (x, y, w, h)
  -> 写入 template_path.with_suffix(".json")
     -> reference_width / reference_height / x / y / w / h
```

模板元数据包含 `reference_width`、`reference_height` 和原始框选坐标 `x/y/w/h`。旧模板没有元数据（或仅有尺寸、无坐标）时，`TemplateManager` 使用兼容默认值 2560×1440 且不做局部区域匹配。用户模板文件缺失时，加载会自动回退到随包只读默认模板。

### 3.2 模板匹配

```
TemplateManager.match(image_screenshot, threshold=0.8)
  -> [模板未加载] return (False, 0.0)
  -> screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
  -> base_scale = min(current_width/reference_width,
                      current_height/reference_height)
  -> _candidate_scales(base_scale)
     -> base_scale × [0.85, 0.925, 1.0, 1.075, 1.15] + 1.0，去重排序
  -> _local_search_region(gray, base_scale)
     -> [有框选坐标] 原始位置按 base_scale 缩放 + 20% 内边距裁局部
     -> [无坐标（旧模板）] return None
  -> _match_at_scale(gray, base_scale, region)
     -> cv2.resize(template, INTER_AREA) -> cv2.matchTemplate(TM_CCOEFF_NORMED)
     -> cv2.minMaxLoc(result)                                  [该比例最佳匹配]
  -> [base >= threshold] return (True, base_value)
     -> 策略 base_local（局部）/ base_full（旧模板全屏）
  -> [未命中] 对剩余比例逐个 _match_at_scale 全屏匹配
     -> 取所有比例中最高 max_val，并记录对应 scale
     -> 策略 fallback_full_multiscale（新模板）/ fallback_multiscale（旧模板）
  -> [全部比例大于截图] return (False, 0.0)
  -> [max_val >= threshold] return (True, max_val)
  -> [default] return (False, max_val)
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `set_template(image, roi)` | `template_manager.py` | `OcrService.create_template()` | `cv2.cvtColor()`, `cv2.imencode()` + 二进制写入, 元数据 JSON 写入 |
| `match(image, threshold)` | `template_manager.py` | `OcrWorker._execute()` | `_candidate_scales()`, `_local_search_region()`, `_match_at_scale()` |
| `_match_at_scale(gray, scale, region=None)` | `template_manager.py` | `match()` | `cv2.resize()`, `cv2.matchTemplate()`, `cv2.minMaxLoc()` |
| `_load()` / `reload()` | `template_manager.py` | 构造、`OcrService.select_template()` | `_load_internal()`、随包默认模板回退、`_load_metadata()` |
| `delete_template()` | `template_manager.py` | `OcrService.delete_template()` | `Path.unlink()`（模板 + 元数据） |
| `is_loaded` / `reference_size` / `last_match_scale` / `last_match_confidence` / `last_match_strategy` | `template_manager.py` | `OcrWorker`、任务日志 | 内存属性 |

> **性能标注：** 局部区域匹配通常 < 50ms（新模板 roi 约 50×145px），作为 OCR 的前置过滤器，先低成本过滤非武将选择页画面。该数值是实测经验值，代码中没有对应的时间预算常量；只有基础比例未命中时才会走全屏多尺度回退，耗时明显更高。

---

## 四、OCR 识别链路（最复杂的调用链）

### 4.1 顶级识别入口

```
GeneralRecognizer.recognize(image)                            [PIL Image]
  -> cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)        [PIL → OpenCV 格式]
  -> 读取 image.shape 与 reference_size
  -> scale_x = image_width / reference_width
  -> scale_y = image_height / reference_height
  -> [裁剪并预处理同类 ROI]
     -> ImagePreprocessor.preprocess_roi(roi)
  -> _recognize_prepared_batch(prepared_slots, "name",
                               evidence_by_slot=batch_evidence)  [名称横向拼图检测]
     -> _build_batch_canvas(prepared_slots)                    [横向拼接，间隙 _BATCH_SLOT_GAP=30]
     -> self._engine.ocr(canvas, cls=False)
     -> [逐检测框] 用 box 中心 x 落在槽位区间内映射回槽位
     -> [仅单候选且 confidence >= 0.5 且未触发回退门槛] 采纳为 batch 结果
        -> _requires_name_batch_fallback(text)                 [截断文本风险判定]
     -> [拼接 OCR 异常] 记录 warning 并返回空结果，全部走逐槽回退
  -> [逐槽解析 batch_enhanced 证据]
     -> _resolve_name_evidence(index, evidence)
     -> [_requires_slot_recheck]
        -> _append_single_name_evidence(...)
           -> single_enhanced 逐槽识别
           -> single_plain 仅放大原图逐槽识别
     -> _resolve_name_evidence(index, evidence)               [合并全部证据]
  -> _resolve_page_names(results)                             [页面唯一性与重复名约束]
  -> return [{index, raw_name, name, candidates,
              resolution, length_mode, confidence, evidence}, ...]
```

对局攻略复用同一名称链路；阵营 ROI 另做一张拼图，缺失时才逐槽回退，不参与名称候选评分。

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `recognize(image)` | `recognizer.py` | `OcrWorker._execute()` | ROI 缩放裁剪、`_recognize_prepared_batch()`、`_resolve_name_evidence()`、`_resolve_page_names()` |
| `_recognize_match_guide(image)` | `recognizer.py` | `recognize()` | 名称/阵营分开批量识别、逐槽回退、`_normalize_team()` |
| `_recognize_prepared_batch(slots, kind, evidence_by_slot=None)` | `recognizer.py` | 两类页面入口 | `_build_batch_canvas()`、`_engine.ocr()`、框中心映射、`_requires_name_batch_fallback()` |
| `_append_single_name_evidence(...)` | `recognizer.py` | 两类页面入口 | `_recognize_prepared_single()`、`_preprocess_plain_roi()` |
| `_requires_slot_recheck(result, text, confidence)` | `recognizer.py` | 两类页面入口 | 空文本 / 置信度 < 0.8 / 未确认状态判定 |
| `_resolve_name_evidence(index, evidence)` | `recognizer.py` | 两类页面入口 | `_parse_name_evidence()`、`_resolve_multi_candidate_similarity()` |
| `_resolve_page_names(results)` | `recognizer.py` | 两类页面入口 | 页面候选排除、重复确认结果回退 |
| `warmup()` / `warmup_inference()` | `recognizer.py` | 应用启动时的 `OcrWorker` 预热任务 | `_engine`、`_similarity_service.warmup()`、代表性拼图检测与识别 |
| `adopt_engine(engine)` / `shared_engine()` / `ensure_engine()` | `recognizer.py` | `OcrWorker` | 跨识别器共享同一 PaddleOCR 实例 |
| `preprocess_roi(roi)` | `image_preprocessor.py` | `GeneralRecognizer` | `cv2.resize()`、`cv2.cvtColor()`、`cv2.createCLAHE()`、`cv2.filter2D()` |
| `_engine` (property) | `recognizer.py` | 批量/逐槽识别 | `create_paddle_ocr()` 延迟初始化，失败后熔断 |
| `create_paddle_ocr(**kwargs)` | `paddle_loader.py` | 常规识别、官方榜单识别 | Windows 首次加载子进程隐藏、打包态模型路径、`PaddleOCR()` |

### 4.2 图像预处理流水线

```
GeneralRecognizer 裁剪名称或阵营 ROI
  -> ImagePreprocessor.preprocess_roi(roi)
     -> cv2.resize(roi, None, fx=3, fy=3, INTER_CUBIC)       [放大 3×]
     -> cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)                  [转 LAB 色彩空间]
     -> lab[..., 0] = clahe.apply(lab[..., 0])                [CLAHE 自适应直方图均衡]
     -> cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)                  [转回 BGR]
     -> kernel = np.array([[-1,-1,-1],[-1,9,-1],[-1,-1,-1]])  [锐化核]
     -> cv2.filter2D(roi, -1, kernel)                         [锐化]
     -> cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)                 [转灰度]
     -> return roi_gray                                       [预处理完成]
  -> [正常路径] 加入同类 ROI 拼图后批量检测
  -> [回退路径] _recognize_prepared_single(preprocessed_roi) [逐槽直接识别]
```

> **重要：** 预处理顺序不可调换。选将页默认名称 ROI 为 50×145px（对局攻略为 55×140px），放大让 PaddleOCR 对小字符识别率更高；CLAHE 处理渐变背景；锐化强化边缘；最后灰度化是 OCR 引擎期望输入。

### 4.3 OCR 名称候选确认链路（核心逻辑）

```
GeneralRecognizer._resolve_name_evidence(index, evidence)
  -> raw_name / confidence = 最高置信度那一路证据
  -> _parse_name_evidence(evidence) 逐路重复                    [逐路字数门禁]
     -> [精确命中] exact（length_mode=complete）
     -> [严格前缀] missing，只保留前缀候选
        -> [唯一且已识别至少 2 字] unique_prefix
     -> [等长且编辑距离 <= 1] complete
        -> [唯一错字候选且字形分 >= 0.55] unique_similarity
        -> [多候选] unresolved
     -> [严格前缀与等长候选同时存在] uncertain，合并候选
     -> [其他增删字且编辑距离 <= 1] uncertain / unresolved
  -> length_mode = 各非空路的长度模式一致则沿用，否则 uncertain
  -> [恰好 1 路 exact] 校验其它路候选集是否兼容
     -> [不兼容] conflict
     -> [兼容] exact
  -> [各路已确认名称（exact / unique_prefix / unique_similarity /
      multi_similarity / slot_unique / manual）]
     -> [唯一] 取最高优先级 resolution 采纳
     -> [多个或与其它路候选冲突] conflict
  -> 取全部非空候选集合的交集 common
     -> [交集为空] conflict
  -> _resolve_multi_candidate_similarity(evidence, parsed, common)
     -> 仅对 length_mode=complete 且 confidence >= 0.7 的证据族评分
     -> 每族保留最高置信度那一路
     -> CharacterSimilarityService.rank_single_substitution_candidates(...)
     -> [每族] 最高分 >= 0.35、与第二名分差 >= 0.15 才计入 supported
     -> [enhanced + plain 两个证据族同选一名] 返回该名称 → multi_similarity
     -> [否则] 返回空 → unresolved
  -> _resolve_page_names(results)
     -> 仅候选数 > 1 且 length_mode 为 missing/complete 的 unresolved 槽位消歧
     -> uncertain 和未过安全阈值的单候选不提升
     -> 重复确认名称按证据等级保留或回退 conflict
```

| 组件/函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `_parse_name_evidence(evidence)` | `recognizer.py` | `_resolve_name_evidence()` | 前缀/等长/其他增删字分流、编辑距离候选 |
| `_resolve_multi_candidate_similarity(...)` | `recognizer.py` | `_resolve_name_evidence()` | 证据族门槛、`rank_single_substitution_candidates()` |
| `is_safe_single_substitution(text, candidate)` | `character_similarity.py` | `_parse_name_evidence()` | 唯一错字字形分与 0.55 门槛 |
| `rank_single_substitution_candidates(text, candidates)` | `character_similarity.py` | `_resolve_multi_candidate_similarity()` | 候选内唯一错字评分排序 |
| `correct_hero_name(text, names)` | `character_similarity.py` | `OfficialDataImportService`、兼容单槽接口 | `_levenshtein_distance()`、视觉评分 |
| `get_value(char, key)` | `character_feature_repository.py` | `CharacterSimilarityService` | `get_feature()` |

> **边界：** 当前字数门禁比较 OCR 原文与候选名称长度。名称 ROI 受卡框和底部定位字干扰，视觉字符分割暂不作为硬门禁。势力关联尚未接入；未来只能过滤已有候选，不能扩展候选集合。

### 4.4 汉字特征补齐链路（性能关键路径）

```
CharacterFeatureRepository.get_feature(char)
  -> load()                                                   [基线 + 用户层合并，仅首次]
     -> _read_entries(src/data/char_info_cache.json)          [随包只读基线]
     -> _read_entries(data/char_info_cache.json)              [用户层，异常时忽略]
  -> [缓存命中] return entry
  -> [缓存未命中] _build_feature(char)
     -> cnradical                                             [部首]
        -> [单字失败] warning 记录具体字符，部首降级为空
     -> unihan_etl.Options().destination                      [已有 CSV 直接读取；缺失时 Packager.export()]
        -> kCangjie / kFourCornerCode                         [仓颉码、四角号码取前 4 位数字]
     -> pypinyin                                              [拼音]
        -> [失败] warning 并置 _pinyin_available=False，后续降级为空值
     -> Options().work_dir / Unihan_IRGSources.txt            [kTotalStrokes 笔画]
     -> src/data/wubi86.txt                                   [五笔 86 全码离线码表]
  -> 写入进程内存；_persist_user_entries() 落盘用户层缓存
     -> [user_cache_path 为 None 或写入失败] 仅降级，不影响识别
```

> **性能标注：** 默认缓存包含 365 个常见字，覆盖当前武将名用字和已知 OCR 误识字。缓存未命中时的原始库查询仍可能约 1 秒，因此由 `GeneralRecognizer.warmup()` / `warmup_hero_names()` 在显式预热时提前加载与批量补齐。

### 4.5 汉字特征评分详情

```
各维度评分方法:
------------------
four_corner_score(c1, c2):
  提取四角号码前 4 个有效数字
  同位置匹配数 / 4；不足 4 位时返回 0

cangjie_score(c1, c2):
  仓颉码为字母序列（如 "BCM" → "月金一"）
  1 - Levenshtein / 较长码长度

wubi_score(c1, c2):
  五笔 86 全码为字母序列（如 "AQJF"）
  1 - Levenshtein / 较长码长度；码缺失 → 0

综合评分 = four_corner × 0.3 + cangjie × 0.3 + wubi × 0.4
```

常规截图只把该综合分用于候选闭包内“等长且恰好一个错字”的字符比较；缺字和其他增删字不调用此评分决胜。
任一侧特征缺失时对应维度记 0 分，四角码不足四位不补零，缺失维度不触发权重重归一。

---

## 五、官方榜单固定版式解析链路

不走 `TemplateManager` 与 `GeneralRecognizer`：版式由固定常量决定，OCR 只在版式切出的单元格上执行（业务层注入的 `recognize_cell` 用 `det=False` 跳过检测网络）。

```
OfficialDataImportService.import_file()
  -> official_board_parser.read_image(path)
     -> cv2.imdecode(np.fromfile(path))                        [规避非 ASCII 路径]
  -> detect_layout(image, key)                                 [key = 2v2 / peak / exile]
     -> [height/width >= 4] 旧版长图候选优先，否则分页候选优先
     -> for layout in candidates:
        -> extract_panels(image, layout)
           -> top = width * layout.top（分页，top_reference="width"）
                  或 height * layout.top（旧版）
           -> 按 panel_ranges 横向裁出左右面板
        -> find_data_boundaries(panel, image_height, layout, panel_index)
           -> [separator_mode="bounded"（旧版）]
              cv2.cvtColor(BGR2GRAY) -> cv2.Canny(40, 120)
              -> cv2.HoughLinesP(threshold=80, minLineLength=面板宽/3, maxLineGap=12)
              -> [仅 |y1-y3| <= 2 的近水平线段] 取线段中点 y
              -> 间距 <= 6 聚类取中心 -> 行高区间 [0.002h, 0.011h] 分 run
              -> 取最长 run，截掉 header_lines[panel_index] 个表头行
           -> [separator_mode="between_rows"（分页）]
              _find_paged_data_boundaries(panel, layout, panel_index)
                -> 排名列白像素行投影（active_rows 阈值 max(3, 0.006w)）
                -> 行带中心（带高 >= 0.014w，中心 >= 0.25w）
                -> 直连行高区间 [0.075w, 0.125w] 取中位数为行高
                -> 按行高倍数（1..4 倍）插值恢复漏行并向前补齐
                -> 边界 = 首行上沿、相邻中心中点、末行下沿
     -> [2v2 / peak] 左右栏行数必须一致
     -> [exile] validate_exile_row_counts()：左栏 >= 10 且右栏 <= 左栏
     -> [两候选都失败] raise ValueError（携带各候选失败原因）
  -> restore_missing_boundaries(boundaries)
     -> 间隔 > 1.5 倍中位行高的位置按整数段插值补线
     -> 返回 (完整边界, 被修复排名集合)
  -> split_row_cells(row, columns, column_breaks)
     -> 按列分界比例切列；胜率列左内缩 -4px（防截断首位数字）
  -> prepare_rate_templates(panel, boundaries, columns, column_breaks, recognize_cell)
     -> build_rank_digit_templates(...)                        [已知视觉行序的排名格取样]
     -> for each row: recognize_cell(胜率单元格)                [业务层注入，det=False]
        -> re.fullmatch(r"\d{2}\.(\d{2})%?") 命中则用小数位补字形样本
     -> 返回 (各排名胜率预计算结果, 数字模板)
  -> recognize_rate_with_templates(rate_cell, templates)
     -> segment_glyphs(cell)                                   [亮列连通切分]
     -> normalize_glyph()                                      [等比缩放居中到 40×28 画布]
     -> match_digit(glyph, templates)                          [Dice 分数]
        -> [单字分数 < 0.72] 丢弃该字
        -> 凑足 4 位 -> "xx.xx%"，置信度取各字最低分
```

| 函数 | 文件 | 说明 |
|------|------|------|
| `LAYOUTS` / `PAGED_LAYOUTS` | `official_board_parser.py` | `2v2`、`peak`、`exile` 三种版式的比例常量；分页变体改以宽度定位顶部 |
| `detect_layout(image, key)` | `official_board_parser.py` | 纵横比候选顺序 + 行数校验确认版式 |
| `extract_panels(image, layout)` | `official_board_parser.py` | 按版式比例切出左右面板 |
| `find_data_boundaries(...)` | `official_board_parser.py` | 横线检测或排名列行投影两种数据行边界算法 |
| `restore_missing_boundaries(boundaries)` | `official_board_parser.py` | 按中位行高补回漏检横线 |
| `split_row_cells(row, columns, column_breaks)` | `official_board_parser.py` | 按列比例切单元格 |
| `prepare_rate_templates(...)` | `official_board_parser.py` | 排名格 + 胜率小数位数字模板与胜率预计算 |
| `recognize_rate_with_templates(...)` | `official_board_parser.py` | 字形切分与 Dice 分数数字匹配 |

候选词表约束、繁体兜底、整榜唯一性与写入门禁由 `src.business.recognition.official_data_import_service` 负责，不在本节。

## 六、OCR 加载与执行边界

### 6.1 模板管理器单例

```
get_template_manager(template_name)
  -> [按 hero_selection / match_guide 分别缓存]
  -> [首次] TemplateManager(template_name)                    [构造 + 自动加载]
  -> return 对应模板管理器
```

### 6.2 OCR 识别器单例

```
OcrWorker._get_recognizer(rois, hero_names, reference_size)
  -> [worker 私有缓存命中] return
  -> [签名变更] GeneralRecognizer(...) -> 更新 worker 私有缓存
```

| 函数 | 文件 | 说明 |
|------|------|------|
| `get_template_manager(template_name)` | `ocr_loader.py` | 按页面模板名称惰性缓存，供配置页管理模板 |
| `OcrWorker._get_recognizer(...)` | `ocr_worker.py` | 以 ROI、武将列表、参考尺寸为签名，在唯一 worker 内重建识别器 |

### 6.3 PaddleOCR 引擎构造与加载熔断

```
OcrWorker 预热 / GeneralRecognizer._engine（首次识别）
  -> paddle_loader.create_paddle_ocr(use_angle_cls=False, lang="ch", show_log=False)
     -> get_mumu_config() 读取 MUMU_OCR_USE_GPU / MUMU_OCR_CPU_THREADS
        -> GPU=false（默认）: kwargs 设 use_gpu=False + cpu_threads=6 + enable_mkldnn=True
        -> 调用方显式传 use_gpu 时优先尊重显式值
     -> _hide_windows_child_consoles()                        [仅本线程子进程加 CREATE_NO_WINDOW]
     -> _frozen_ocr_model_dirs()
        -> frozen 且随包含 paddleocr_models/ 且 %TEMP% 为纯 ASCII
           -> 复制 det/rec/cls 到 %TEMP%\mjs_ocr_models（.synced 跳过重复复制）
           -> 注入 det_model_dir / rec_model_dir / cls_model_dir
        -> 开发态或 %TEMP% 含中文 -> 返回空，沿用 PaddleOCR 默认路径
     -> 模块级 _LOAD_LOCK 内 from paddleocr import PaddleOCR + PaddleOCR(**kwargs)
  -> 加载失败：recognizer._engine 置熔断标记 self._ocr=False
     -> 后续识别立即抛 RuntimeError（重启应用后可重试），不再重复加载
```

同步等待路径（`CaptureService.run_ocr_if_matched()` / `OcrService.run_ocr()`）对 `OcrTask.completed` 做 30 秒有限等待，超时返回空结果，防止引擎异常（如 GPU 驱动问题）时调用线程无限阻塞。

---

## 七、外部调用关系总览

### 7.1 本模块被外部调用

```
src.business.emulator.capture_service
  -> AdbCapture.connect() / screencap_full()                   [截图]
  -> OcrWorker.submit(OcrTask)                                 [提交，不直接匹配]

src.business.recognition.ocr_service
  -> get_template_manager().set_template() / reload() / delete_template()
  -> 注入 CaptureService.submit_ocr_task()                     [兼容同步 run_ocr]

src.ui.app.main_window
  -> 后台线程：AdbCapture.screencap_full()
  -> CaptureService.submit_ocr_task() -> OcrWorker             [轮询]

src.ui.configuration.mumu_config_dialog
  -> EmulatorOperationService                                [后台探测/连接/测试/截图]
  -> RoiSelectorDialog                                       [UI 鼠标框选]
  -> OcrService.create_template() / select_template()        [模板持久化]
```

### 7.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| Python `subprocess` | ADB 命令执行 |
| Python `PIL.Image` | 图片解析/处理 |
| Python `cv2` (OpenCV) | 图像预处理、模板匹配 |
| Python `io.BytesIO` | 二进制流处理 |
| `paddleocr.PaddleOCR` | OCR 推理引擎（推理设备/线程由 `MUMU_OCR_USE_GPU` / `MUMU_OCR_CPU_THREADS` 控制，CPU 模式启用 MKLDNN） |
| `cnradical.Radical` | 部首查询（汉字特征） |
| `unihan_etl.Packager` | UNIHAN 数据查询（四角号码、仓颉码） |
| `pypinyin.pinyin` | 拼音查询 |

---

## 八、函数清单总表

### ADB 层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `AdbCapture.__init__(adb_path, adb_port=7555)` | `adb_screen.py` | `CaptureService` | 存储路径/端口 |
| `AdbCapture.connect()` | `adb_screen.py` | `CaptureService` | `_resolve_target()`, `_check_adb_valid()`, `_run_adb()`, `_get_device_state()` |
| `AdbCapture.check_device()` | `adb_screen.py` | `connect()`、截图前会话校验 | `_get_device_state()`, `_invalidate_connection()` |
| `AdbCapture.screencap_full(log_success=True)` | `adb_screen.py` | `CaptureService._execute_capture()`、轮询线程 | `subprocess.run()`, `load_png_image_bytes()` |
| `AdbCapture.disconnect()` | `adb_screen.py` | `CaptureService` | `_disconnect_safe()` |
| `AdbCapture.device_serial` (property) | `adb_screen.py` | `CaptureService` | 切换目标设备；`IP:port` 时同步内部端口 |
| `AdbCapture._resolve_target()` | `adb_screen.py` | `connect()` | `probe_running_devices()` |
| `AdbCapture._run_adb(*args, timeout=10)` | `adb_screen.py` | 内部 | `subprocess.run()` |
| `AdbCapture._get_device_state(serial)` | `adb_screen.py` | `connect()`, `check_device()` | `_run_adb("-s", serial, "get-state")` |
| `AdbCapture._is_device_unavailable(msg)` | `adb_screen.py` | `screencap_full()` | 错误标记匹配 |
| `load_local_image(path)` | `image_validation.py` | 本地导入路径 | 大小/格式/像素校验 + `Image.verify()` |
| `load_png_image_bytes(data)` | `image_validation.py` | `AdbCapture.screencap_full()` | 同上，仅接受实际 PNG |

### 探测层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `probe_mumu_adb()` | `prober.py` | `EmulatorOperationService` | `_get_mumu_candidates()`, `_get_legacy_candidates()`, `shutil.which()` |
| `probe_all_devices_with_status(retries=1)` | `prober.py` | `EmulatorOperationService` | `_find_mumu_root()`, `subprocess.run(MuMuManager info --vmindex all)` |
| `probe_all_devices()` | `prober.py` | `probe_running_devices()` | `probe_all_devices_with_status()` |
| `probe_running_devices()` | `prober.py` | `AdbCapture._resolve_target()` | `probe_all_devices()` |
| `test_adb_path(adb_path)` | `prober.py` | `EmulatorOperationService` | `subprocess.run(adb version)` |
| `_find_mumu_root()` | `prober.py` | `probe_all_devices_with_status()` | `_get_mumu_candidates()`, `_get_legacy_candidates()`, `Path.exists()` |
| `_probe_mumu_registry()` | `prober.py` | `_get_mumu_candidates()` | `winreg` 读取 InstallDir |

### 图像工具层

| 函数 | 文件 | 调用方 | 说明 |
|------|------|--------|------|
| `pil_to_qpixmap(image)` | `image_utils.py` | `MumuConfigDialog` | PIL → QPixmap |
| `save_image(image, path)` | `image_utils.py` | `CaptureService._execute_capture()` | 截图保存到磁盘 |
| `copy_image_to_clipboard(image)` | `image_utils.py` | 外部 UI | 复制到剪贴板 |

### 模板匹配层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `TemplateManager.set_template(image, roi)` | `template_manager.py` | `OcrService.create_template()` | `cv2.imencode()` + 二进制写入 + 参考尺寸/坐标 JSON |
| `TemplateManager.match(image, threshold)` | `template_manager.py` | `OcrWorker._execute()` | `_candidate_scales()`, `_local_search_region()`, `_match_at_scale()` |
| `TemplateManager._match_at_scale(gray, scale, region=None)` | `template_manager.py` | `match()` | `cv2.resize()`, `cv2.matchTemplate()`, `cv2.minMaxLoc()` |
| `TemplateManager._load()` / `reload()` | `template_manager.py` | 构造、`select_template()` | `_load_internal()`、随包默认模板回退、`_load_metadata()` |
| `TemplateManager.delete_template()` | `template_manager.py` | `OcrService.delete_template()` | `Path.unlink()`（模板 + 元数据） |
| `TemplateManager.is_loaded` / `reference_size` / `last_match_*` | `template_manager.py` | `OcrWorker`、任务日志 | 内存属性 |

### OCR 识别层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `GeneralRecognizer.recognize(image)` | `recognizer.py` | `OcrWorker._execute()` | ROI 缩放、同类拼图识别、多路证据解析、页面约束 |
| `GeneralRecognizer._recognize_match_guide(image)` | `recognizer.py` | `recognize()` | 名称/阵营分开拼图、名称证据解析 |
| `GeneralRecognizer._recognize_prepared_batch(slots, kind, evidence_by_slot=None)` | `recognizer.py` | 两类页面入口 | `_build_batch_canvas()`、`_engine.ocr()`、检测框中心映射、`_requires_name_batch_fallback()` |
| `GeneralRecognizer._append_single_name_evidence(...)` | `recognizer.py` | 两类页面入口 | `single_enhanced`、`single_plain` 逐槽复核 |
| `GeneralRecognizer._requires_slot_recheck(...)` | `recognizer.py` | 两类页面入口 | 空文本 / 置信度 < 0.8 / 未确认状态判定 |
| `GeneralRecognizer._resolve_name_evidence(index, evidence)` | `recognizer.py` | 两类页面入口 | 字数门禁、已确认名称聚合、候选交集、多候选评分 |
| `GeneralRecognizer._resolve_page_names(results)` | `recognizer.py` | 两类页面入口 | 页面唯一性、重复名称回退 |
| `GeneralRecognizer.warmup()` / `warmup_inference()` | `recognizer.py` | 应用启动时的 `OcrWorker` 预热任务 | 模型、字符特征、代表性拼图推理 |
| `GeneralRecognizer.adopt_engine()` / `shared_engine()` / `ensure_engine()` | `recognizer.py` | `OcrWorker` | 跨识别器共享 PaddleOCR 实例 |
| `create_paddle_ocr(**kwargs)` | `paddle_loader.py` | `GeneralRecognizer`、`OfficialDataImportService` | Windows 依赖探测短命令隐藏、打包态模型路径、`PaddleOCR()` |
| `GeneralRecognizer.save_results()` | `recognizer.py` | `OcrWorker._execute()` | JSON 序列化 |
| `OcrRoiConfig.layout_for()` / `save_layout()` / `reset_layout()` / `reload()` | `roi_config.py` | `GeneralRecognizer`、`CaptureService`、`OcrWorker`、配置协调器 | 默认布局加载、本地覆盖原子写盘、页面要求校验 |
| `ImagePreprocessor.preprocess_roi()` | `image_preprocessor.py` | `GeneralRecognizer` | 放大、CLAHE、锐化、灰度 |
| `official_board_parser.read_image()` / `detect_layout()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 非 ASCII 路径读图、按纵横比与行数校验选版式 |
| `official_board_parser.extract_panels()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 按版式比例切分面板 |
| `official_board_parser.find_data_boundaries()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 旧版 Canny + HoughLinesP 横线检测 / 分页排名列行投影恢复 |
| `official_board_parser.restore_missing_boundaries()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 按中位行高补回漏检横线 |
| `official_board_parser.split_row_cells()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 按列分界比例切单元格（胜率列左内缩 -4px） |
| `official_board_parser.prepare_rate_templates()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 排名格 + 胜率小数位数字模板与胜率 OCR 预计算 |
| `official_board_parser.recognize_rate_with_templates()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 字形切分、归一化到 40×28、Dice 分数匹配 |
| `CharacterSimilarityService.correct_hero_name()` | `character_similarity.py` | 官方榜单导入、兼容单槽接口 | 编辑距离、视觉评分；调用方约束候选范围 |
| `CharacterSimilarityService.is_safe_single_substitution()` | `character_similarity.py` | `GeneralRecognizer._parse_name_evidence()` | 唯一错字字形分与 0.55 门槛 |
| `CharacterSimilarityService.rank_single_substitution_candidates()` | `character_similarity.py` | `GeneralRecognizer._resolve_multi_candidate_similarity()` | 候选内唯一错字评分排序 |
| `CharacterSimilarityService.warmup()` / `warmup_hero_names()` | `character_similarity.py` | `GeneralRecognizer.warmup()` | 缓存与拼音库预热、词表字符补齐 |
| `CharacterFeatureRepository.get_feature()` | `character_feature_repository.py` | `CharacterSimilarityService` | 缓存加载、动态补齐、用户层持久化 |
| `CharacterFeatureRepository.get_value()` | `character_feature_repository.py` | `CharacterSimilarityService` | `get_feature()` |
