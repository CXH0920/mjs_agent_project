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
  -> [已连接] return (True, "已处于连接状态")
  -> _check_adb_valid()                                       [校验 adb.exe 存在性]
     -> Path.exists() and Path.is_file()                      [文件系统检查]
  -> target = device_serial or "127.0.0.1:port"
  -> _run_adb("connect", target, timeout=10)                  [adb connect 命令]
     -> subprocess.run([adb_path, "connect", target], timeout=10)
  -> [失败] return (False, "ADB 连接失败: ...")
  -> _get_devices()                                           [验证设备在线]
     -> _run_adb("devices")
     -> 解析 "List of devices attached\nserial\tdevice\n..."
     -> _check_device_serial_safe(serial)                     [格式校验: IP:port + port 1-65535]
  -> [无设备] _disconnect_safe() + return (False, ...)
  -> self._connected = True
  -> return (True, "连接成功")
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `connect()` | `adb_screen.py` | `CaptureService` | `_check_adb_valid()`, `_run_adb(connect)`, `_get_devices()` |
| `_check_adb_valid()` | `adb_screen.py` | `connect()` | `Path.exists()`, `Path.is_file()` |
| `_check_device_serial_safe(serial)` | `adb_screen.py` | `_get_devices()` | `str.split()`, `int()` 端口校验 |
| `_run_adb(*args, timeout)` | `adb_screen.py` | `connect()`, `_get_devices()` | `subprocess.run([adb, *args])` |
| `_get_devices()` | `adb_screen.py` | `connect()` | `_run_adb(devices)`, `_check_device_serial_safe()` |

### 1.2 全屏截图

```
AdbCapture.screencap_full()
  -> [未连接] return (False, "尚未连接")
  -> subprocess.run([adb, "-s", serial, "exec-out", "screencap", "-p"],
                    capture_output=True, timeout=15)          [ADB 截图命令]
  -> [returncode != 0] return (False, "screencap 失败: ...")
  -> [stdout 为空] return (False, "截图返回空数据")
  -> Image.open(io.BytesIO(result.stdout))                    [PIL 解析 PNG]
  -> image.load()                                             [解码像素数据]
  -> return (True, image)
```

| 函数 | 文件 | 调用方 | 说明 |
|------|------|--------|------|
| `screencap_full()` | `adb_screen.py` | `CaptureService._execute_capture()` | ADB 截屏→PIL Image |
| `list_connected_devices(adb_path)` | `adb_screen.py` | 外部（静态） | 查询所有 ADB 设备 |

> **说明：** 使用 `exec-out` 模式而非 `shell screencap`，直接输出二进制到 stdout，不经过设备 shell 解析。

---

## 二、MuMu 设备探测链路

### 2.1 自动探测 ADB 路径

```
probe_mumu_adb()
  -> shutil.which("adb")                                      [PATH 查找]
  -> [找到] return path
  -> _get_mumu_candidates()                                   [收集候选路径]
     -> [环境变量] os.environ.get("MUMU_HOME")
     -> [注册表] _probe_mumu_registry()
        -> 读取 HKLM\Netease\MuMuPlayer12 注册表项
     -> [硬编码] 8 个常见安装路径
  -> [合并候选] 搜索 EmulatorShell\adb.exe
  -> _get_legacy_candidates()                                 [备选旧版本路径]
  -> return ""  (未找到)
```

### 2.2 探测 MuMu 实例

```
probe_all_devices()
  -> _find_mumu_root()                                        [查找 MuMu 安装目录]
     -> _get_mumu_candidates() + _get_legacy_candidates()
     -> [检查 nx_main 子目录存在性]
  -> subprocess.run([MuMuManager.exe, "list"])                [列出所有实例]
  -> json.loads(output)                                       [解析实例列表]
  -> return [MuMuDeviceInfo(...), ...]

probe_mumu_port()
  -> probe_all_devices()
  -> filter: is_running == True
  -> return running_device.adb_port or 0
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `probe_mumu_adb()` | `prober.py` | `EmulatorOperationService.detect_adb()` | `_get_mumu_candidates()`, `shutil.which()` |
| `probe_all_devices()` | `prober.py` | `probe_mumu_port()`, `EmulatorOperationService.refresh_devices()` | `_find_mumu_root()`, `subprocess.run(MuMuManager)` |
| `probe_mumu_port()` | `prober.py` | 兼容外部调用 | `probe_all_devices()` |
| `_probe_mumu_registry()` | `prober.py` | `_get_mumu_candidates()` | 注册表读取 |
| `_find_mumu_root()` | `prober.py` | `probe_all_devices()` | `_get_mumu_candidates()`, `Path.exists()` |
| `test_adb_path(adb_path)` | `prober.py` | `EmulatorOperationService.detect_adb()` | `subprocess.run(adb version)` |

---

## 三、模板匹配链路

### 3.1 制作模板

```
TemplateManager.set_template(image, roi)
  -> 校验: roi 在图像范围内
  -> cropped = image[y:y+h, x:x+w]                            [OpenCV 裁剪]
  -> cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)                [转灰度]
  -> cv2.imwrite(template_path, gray)                         [保存模板图片]
  -> 写入 template_path.with_suffix(".json")                 [保存参考截图宽高]
```

模板元数据包含 `reference_width` 和 `reference_height`。旧模板没有元数据时，
`TemplateManager` 使用兼容默认值 2560×1440。

### 3.2 模板匹配

```
TemplateManager.match(image_screenshot, threshold=0.8)
  -> [模板未加载] return (False, 0.0)
  -> screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
  -> base_scale = min(current_width/reference_width,
                      current_height/reference_height)
  -> 生成 base_scale × [0.85, 0.925, 1.0, 1.075, 1.15]
  -> 每个比例 resize(template) -> cv2.matchTemplate(...)
  -> cv2.minMaxLoc(result)                                   [获取该比例最佳匹配]
  -> 选择所有比例中最高的 max_val
  -> [max_val >= threshold] return (True, max_val)
  -> [default] return (False, max_val)
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `set_template(image, roi)` | `template_manager.py` | `OcrService.create_template()` | `cv2.cvtColor()`, `cv2.imwrite()`, 元数据 JSON 写入 |
| `match(image, threshold)` | `template_manager.py` | `OcrWorker._execute()` | 多尺度 `cv2.matchTemplate()`, `cv2.minMaxLoc()` |
| `reload()` | `template_manager.py` | `OcrService.select_template()` | `_load_internal()` |
| `is_loaded` (property) | `template_manager.py` | 外部 UI | `self._template is not None` |

> **性能标注：** 模板匹配耗时通常 < 50ms（roi 约 40x140px），作为 OCR 的前置过滤器，先低成本过滤非武将选择页画面。

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
  -> _recognize_prepared_batch(prepared_slots, "name")       [名称横向拼图检测]
     -> _build_batch_canvas(..., slot_gap=30)
     -> self._engine.ocr(canvas, cls=False)
     -> _extract_batch_detections(...)                        [检测框映射回槽位]
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
| `recognize(image)` | `recognizer.py` | `OcrWorker._execute()` | `_recognize_prepared_batch()`、`_resolve_name_evidence()`、`_resolve_page_names()` |
| `_recognize_match_guide(image)` | `recognizer.py` | `recognize()` | 名称/阵营批量识别与逐槽回退 |
| `_recognize_prepared_batch(slots, kind)` | `recognizer.py` | 两类页面入口 | `_build_batch_canvas()`、`_engine.ocr()`、`_extract_batch_detections()` |
| `_append_single_name_evidence(...)` | `recognizer.py` | 两类页面入口 | `_recognize_prepared_single()`、`_preprocess_plain_roi()` |
| `_resolve_name_evidence(index, evidence)` | `recognizer.py` | 两类页面入口 | `_parse_name_evidence()`、`_resolve_multi_candidate_similarity()` |
| `_resolve_page_names(results)` | `recognizer.py` | 两类页面入口 | 页面候选排除、重复确认结果回退 |
| `preprocess_roi(roi)` | `image_preprocessor.py` | `GeneralRecognizer` | `cv2.resize()`、`cv2.cvtColor()`、`cv2.createCLAHE()`、`cv2.filter2D()` |
| `_engine` (property) | `recognizer.py` | 批量/逐槽识别 | `create_paddle_ocr()` 延迟初始化 |
| `create_paddle_ocr(**kwargs)` | `paddle_loader.py` | 常规识别、官方榜单识别 | Windows 首次加载子进程隐藏、`PaddleOCR()` |

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

> **重要：** 预处理顺序不可调换。原始 ROI 约 40×140px，放大让 PaddleOCR 对小字符识别率更高；CLAHE 处理渐变背景；锐化强化边缘；最后灰度化是 OCR 引擎期望输入。

### 4.3 OCR 名称候选确认链路（核心逻辑）

```
GeneralRecognizer._resolve_name_evidence(index, evidence)
  -> _parse_name_evidence(evidence)                           [逐路字数门禁]
     -> [精确命中] exact
     -> [严格前缀] missing，只保留前缀候选
        -> [唯一且已识别至少 2 字] unique_prefix
     -> [等长且编辑距离 <= 1] complete
        -> [唯一错字候选且字形分 >= 0.55] unique_similarity
        -> [多候选] unresolved
     -> [严格前缀与等长候选同时存在] uncertain，合并候选
     -> [其他增删字且编辑距离 <= 1] uncertain / unresolved
  -> 取全部非空候选集合的交集
     -> [交集为空或确认名不兼容任一路候选] conflict
  -> _resolve_multi_candidate_similarity(..., common)
     -> 仅评分等长且恰好一个错字的候选
     -> CharacterSimilarityService.rank_single_substitution_candidates(...)
     -> [每路] confidence >= 0.7、最高分 >= 0.35、领先 >= 0.15
     -> [enhanced + plain 两个证据族同选一名] multi_similarity
     -> [否则] unresolved
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
  -> load() -> 读取可配置的 char_info_cache.json
  -> [缓存命中] return entry
  -> [缓存未命中] _build_feature(char)
     -> unihan_etl.Options().destination                      [已有 CSV 直接读取；缺失时导出]
     -> cnradical                                             [部首]
     -> pypinyin                                              [拼音]
     -> Options().work_dir / Unihan_IRGSources.txt            [笔画]
     -> [pypinyin 失败] warning + 标记不可用，后续拼音降级为空值
     -> [cnradical 单字失败] warning + 当前字符部首降级为空值
  -> 写入进程内存；save() 时 UTF-8/LF 原子落盘
```

> **性能标注：** 默认缓存包含 314 个常见字，覆盖当前武将名用字和已知 OCR 误识字。缓存未命中时的原始库查询仍可能约 1 秒，因此由 `GeneralRecognizer.warmup()` 在显式预热时提前加载。

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

## 五、OCR 加载与执行边界

### 5.1 模板管理器单例

```
get_template_manager(template_name)
  -> [按 hero_selection / match_guide 分别缓存]
  -> [首次] TemplateManager(template_name)                    [构造 + 自动加载]
  -> return 对应模板管理器
```

### 5.2 OCR 识别器单例

```
OcrWorker._get_recognizer(rois, hero_names, reference_size)
  -> [worker 私有缓存命中] return
  -> [签名变更] GeneralRecognizer(...) -> 更新 worker 私有缓存

ocr_loader.get_recognizer(...)
  -> 仅兼容旧调用，不是活动截图、文件导入或轮询的执行入口
```

| 函数 | 文件 | 说明 |
|------|------|------|
| `get_template_manager(template_name)` | `ocr_loader.py` | 按页面模板名称惰性缓存，供配置页管理模板 |
| `OcrWorker._get_recognizer(...)` | `ocr_worker.py` | 以 ROI、武将列表、参考尺寸为签名，在唯一 worker 内重建识别器 |
| `get_recognizer(...)` | `ocr_loader.py` | 兼容旧调用；活动识别路径不使用 |

---

## 六、外部调用关系总览

### 6.1 本模块被外部调用

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

### 6.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| Python `subprocess` | ADB 命令执行 |
| Python `PIL.Image` | 图片解析/处理 |
| Python `cv2` (OpenCV) | 图像预处理、模板匹配 |
| Python `io.BytesIO` | 二进制流处理 |
| `paddleocr.PaddleOCR` | OCR 推理引擎 |
| `cnradical.Radical` | 部首查询（汉字特征） |
| `unihan_etl.Packager` | UNIHAN 数据查询（四角号码、仓颉码） |
| `pypinyin.pinyin` | 拼音查询 |

---

## 七、函数清单总表

### ADB 层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `AdbCapture.__init__(adb_path, port)` | `adb_screen.py` | `CaptureService` | 存储路径/端口 |
| `AdbCapture.connect()` | `adb_screen.py` | `CaptureService` | `_check_adb_valid()`, `_run_adb()`, `_get_devices()` |
| `AdbCapture.screencap_full()` | `adb_screen.py` | `CaptureService._execute_capture()` | `subprocess.run()`, `Image.open()` |
| `AdbCapture.disconnect()` | `adb_screen.py` | `CaptureService` | `_disconnect_safe()` |
| `AdbCapture.list_connected_devices(adb)` | `adb_screen.py` | 外部（静态） | `subprocess.run(adb devices)` |
| `AdbCapture._run_adb(*args)` | `adb_screen.py` | 内部 | `subprocess.run()` |
| `AdbCapture._get_devices()` | `adb_screen.py` | `connect()` | `_run_adb(devices)` |
| `AdbCapture._check_device_serial_safe()` | `adb_screen.py` | `_get_devices()` | 格式校验 |

### 探测层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `probe_mumu_adb()` | `prober.py` | `EmulatorOperationService` | `_get_mumu_candidates()`, `shutil.which()` |
| `probe_all_devices()` | `prober.py` | `EmulatorOperationService`, `probe_mumu_port()` | `_find_mumu_root()`, `subprocess.run()` |
| `probe_mumu_port()` | `prober.py` | 兼容外部调用 | `probe_all_devices()` |
| `test_adb_path(adb_path)` | `prober.py` | `EmulatorOperationService` | `subprocess.run(adb version)` |
| `_find_mumu_root()` | `prober.py` | `probe_all_devices()` | `_get_mumu_candidates()`, `Path.exists()` |

### 图像工具层

| 函数 | 文件 | 调用方 | 说明 |
|------|------|--------|------|
| `pil_to_qpixmap(image)` | `image_utils.py` | `MumuConfigDialog` | PIL → QPixmap |
| `save_image(image, path)` | `image_utils.py` | `CaptureService._execute_capture()` | 截图保存到磁盘 |
| `copy_image_to_clipboard(image)` | `image_utils.py` | 外部 UI | 复制到剪贴板 |

### 模板匹配层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `TemplateManager.set_template(image, roi)` | `template_manager.py` | `OcrService.create_template()` | `cv2.imwrite()` + 参考尺寸 JSON |
| `TemplateManager.match(image, threshold)` | `template_manager.py` | `OcrWorker._execute()` | 多尺度 `cv2.matchTemplate()`, `cv2.minMaxLoc()` |
| `TemplateManager.reload()` | `template_manager.py` | `select_template()` | `_load_internal()` |
| `TemplateManager.delete_template()` | `template_manager.py` | `OcrService.delete_template()` | `Path.unlink()` |

### OCR 识别层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `GeneralRecognizer.recognize(image)` | `recognizer.py` | `OcrWorker._execute()` | ROI 缩放、同类拼图识别、多路证据解析、页面约束 |
| `GeneralRecognizer._recognize_match_guide(image)` | `recognizer.py` | `recognize()` | 名称/阵营分开拼图、名称证据解析 |
| `GeneralRecognizer._recognize_prepared_batch(slots, kind)` | `recognizer.py` | 两类页面入口 | `_build_batch_canvas()`、`_engine.ocr()`、检测框映射 |
| `GeneralRecognizer._append_single_name_evidence(...)` | `recognizer.py` | 两类页面入口 | `single_enhanced`、`single_plain` 逐槽复核 |
| `GeneralRecognizer._resolve_name_evidence(index, evidence)` | `recognizer.py` | 两类页面入口 | 字数门禁、候选交集、多候选评分 |
| `GeneralRecognizer._resolve_page_names(results)` | `recognizer.py` | 两类页面入口 | 页面唯一性、重复名称回退 |
| `GeneralRecognizer.warmup()` / `warmup_inference()` | `recognizer.py` | 应用启动时的 `OcrWorker` 预热任务 | 模型、字符特征、代表性拼图推理 |
| `create_paddle_ocr(**kwargs)` | `paddle_loader.py` | `GeneralRecognizer`、`OfficialDataImportService` | Windows 依赖探测短命令隐藏、`PaddleOCR()` |
| `GeneralRecognizer.save_results()` | `recognizer.py` | `OcrWorker._execute()` | JSON 序列化 |
| `ImagePreprocessor.preprocess_roi()` | `image_preprocessor.py` | `GeneralRecognizer` | 放大、CLAHE、锐化、灰度 |
| `official_board_parser.find_data_boundaries()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | OpenCV 横线检测与视觉行边界选择 |
| `official_board_parser.prepare_rate_templates()` | `official_board_parser.py` | `OfficialDataImportService.import_file()` | 行切分、数字字形模板与胜率 OCR 预计算 |
| `CharacterSimilarityService.correct_hero_name()` | `character_similarity.py` | 官方榜单导入、兼容单槽接口 | 编辑距离、视觉评分；调用方约束候选范围 |
| `CharacterFeatureRepository.get_feature()` | `character_feature_repository.py` | `CharacterSimilarityService` | 缓存加载、动态补齐 |
