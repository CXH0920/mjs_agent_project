# 调用链路：屏幕采集与 OCR 识别

> 对应源码：`src/capture/` + `src/ocr/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> `[性能标注]` 标注了可能影响 UI 响应速度的关键路径。

---

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
| `probe_mumu_adb()` | `prober.py` | `MumuConfigDialog._on_auto_detect()` | `_get_mumu_candidates()`, `shutil.which()` |
| `probe_all_devices()` | `prober.py` | `probe_mumu_port()`, `MumuConfigDialog` | `_find_mumu_root()`, `subprocess.run(MuMuManager)` |
| `probe_mumu_port()` | `prober.py` | `MumuConfigDialog` | `probe_all_devices()` |
| `_probe_mumu_registry()` | `prober.py` | `_get_mumu_candidates()` | 注册表读取 |
| `_find_mumu_root()` | `prober.py` | `probe_all_devices()` | `_get_mumu_candidates()`, `Path.exists()` |
| `test_adb_path(adb_path)` | `prober.py` | `MumuConfigDialog` | `subprocess.run(adb version)` |

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
| `match(image, threshold)` | `template_manager.py` | `CaptureService._run_ocr()`, `MainWindow` 轮询 | 多尺度 `cv2.matchTemplate()`, `cv2.minMaxLoc()` |
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
  -> [对 8 个 ROI 逐一处理]
     -> 将参考 ROI 的 x/y/w/h 分别乘以 scale_x/scale_y
     -> image[y:y+h, x:x+w]                                  [当前截图坐标裁剪]
     -> self._recognize_single(roi, slot_index)                [单 ROI 识别]
       -> self._preprocess_roi(roi)                           [图像预处理]
       -> self._engine.ocr(preprocessed_roi)                  [PaddleOCR 推理]
          -> PaddleOCR.__call__(img)                          [首次调用时延迟初始化]
       -> self._extract_text(ocr_result)                      [解析 PaddleOCR 输出]
       -> _correct_with_hero_list(text, self._hero_names)      [编辑距离矫正]
     [性能标注：8 个 ROI 依次处理，PaddleOCR 每次约 0.5-3s]
  -> return [{"index": 1-8, "name": ..., "confidence": ...}, ...]
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `recognize(image)` | `recognizer.py` | `CaptureService._run_ocr()`, `OcrService.run_ocr()` | `_recognize_single()` ×8 |
| `_recognize_single(roi, slot)` | `recognizer.py` | `recognize()` | `_preprocess_roi()`, `_engine.ocr()`, `_correct_with_hero_list()` |
| `_preprocess_roi(roi)` | `recognizer.py` | `_recognize_single()` | `cv2.resize()`, `cv2.cvtColor()`, `cv2.createCLAHE()`, `cv2.filter2D()` |
| `_extract_text(result)` | `recognizer.py` | `_recognize_single()` | 解析 PaddleOCR 输出格式 |
| `_engine` (property) | `recognizer.py` | `_recognize_single()` | `PaddleOCR()` 延迟初始化 |

### 4.2 图像预处理流水线

```
_recognize_single(roi, slot)
  -> self._preprocess_roi(roi)
     -> cv2.resize(roi, None, fx=3, fy=3, INTER_CUBIC)       [放大 3×]
     -> cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)                  [转 LAB 色彩空间]
     -> lab[..., 0] = clahe.apply(lab[..., 0])                [CLAHE 自适应直方图均衡]
     -> cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)                  [转回 BGR]
     -> kernel = np.array([[-1,-1,-1],[-1,9,-1],[-1,-1,-1]])  [锐化核]
     -> cv2.filter2D(roi, -1, kernel)                         [锐化]
     -> cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)                 [转灰度]
     -> return roi_gray                                       [预处理完成]
  -> self._engine.ocr(preprocessed_roi)                       [PaddleOCR 推理]
```

> **重要：** 预处理顺序不可调换。原始 ROI 约 40×140px，放大让 PaddleOCR 对小字符识别率更高；CLAHE 处理渐变背景；锐化强化边缘；最后灰度化是 OCR 引擎期望输入。

### 4.3 OCR 结果矫正链路（核心逻辑）

```
_correct_with_hero_list(text, hero_names)
  -> [文本为空] return text                                    [跳过]
  -> [文本已在武将名列表中] return text                         [完全匹配]
  -> [对 155 个武将名逐项计算]
     -> _levenshtein_distance(text, hero_name) ×155           [编辑距离]
        -> [len(s1) < len(s2)] s1→s2  确保 m ≥ n
        -> dp[i][j] 矩阵: O(len(s1) * len(s2))
        -> return dp[n][m]
  -> 收集候选: [name for name, dist in candidates if dist <= 1]
  -> [无候选] return text                                      [无法纠正]
  -> [唯一候选] return candidate_name                          [直接采纳]
  -> [多候选] _pick_visually_similar(text, candidates)        [视觉相似度决胜]
     -> _load_char_info()                                     [加载汉字特征缓存]
     -> [对每对不同字符]
        -> _multi_dim_similarity(tc, cc, char_db)             [多维度加权评分]
           -> _four_corner_score(c1, c2, char_db) × 0.4       [四角号码 40%]
              -> _hc(char_db, char, "four_corner")
                 -> _ensure_char_in_cache(char)                [缓存未命中→动态补齐]
                    -> _query_char_from_unihan(char)           [unihan_etl CSV 查询]
                    -> _get_radical_client().radical(char)     [cnradical 部首查询]
                    -> _get_pinyin_of(char)                    [pypinyin 拼音查询]
                    -> _get_stroke(char)                       [笔画数查询]
                    -> 写入 char_info_cache + 进程内存
              -> 比较四角号码逐位匹配率
           -> _cangjie_score(c1, c2, char_db) × 0.4           [仓颉码 40%]
              -> _hc() + SequenceMatcher.ratio()
           -> _radical_score(c1, c2, char_db) × 0.2           [部首 20%]
              -> _hc() + 相等检查
        -> score += multi_dim_score / max_len
     -> score -= 0.5 * abs(len(text) - len(candidate))        [长度差惩罚]
     -> 取最高分候选
     -> [平分] _pinyin_similarity(c1, c2, char_db) tiebreak   [拼音相似度]
     -> [仍平分] _stroke_diff(c1, c2, char_db) tiebreak        [笔画差]
  -> return best_candidate
```

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `_correct_with_hero_list(text, names)` | `recognizer.py` | `_recognize_single()` | `_levenshtein_distance()` ×155, `_pick_visually_similar()` |
| `_levenshtein_distance(s1, s2)` | `recognizer.py` | `_correct_with_hero_list()` | DP O(n×m) |
| `_pick_visually_similar(text, candidates)` | `recognizer.py` | `_correct_with_hero_list()` | `_multi_dim_similarity()`, `_pinyin_similarity()`, `_stroke_diff()` |
| `_multi_dim_similarity(c1, c2, db)` | `recognizer.py` | `_pick_visually_similar()` | `_four_corner_score()`, `_cangjie_score()`, `_radical_score()` |
| `_four_corner_score(c1, c2, db)` | `recognizer.py` | `_multi_dim_similarity()` | `_hc()` |
| `_cangjie_score(c1, c2, db)` | `recognizer.py` | `_multi_dim_similarity()` | `_hc()`, `SequenceMatcher.ratio()` |
| `_radical_score(c1, c2, db)` | `recognizer.py` | `_multi_dim_similarity()` | `_hc()` |
| `_pinyin_similarity(c1, c2, db)` | `recognizer.py` | `_pick_visually_similar()` (tiebreak) | `_hc()` |
| `_stroke_diff(c1, c2, db)` | `recognizer.py` | `_pick_visually_similar()` (tiebreak) | `_hc()`, `_get_stroke()` |
| `_hc(db, char, key, default)` | `recognizer.py` | 各评分函数 | `_ensure_char_in_cache()` |
| `_ensure_char_in_cache(char)` | `recognizer.py` | `_hc()` | `_query_char_from_unihan()`, `_get_radical_client()`, `_get_pinyin_of()`, `_get_stroke()` |

### 4.4 汉字特征补齐链路（性能关键路径）

```
_ensure_char_in_cache(char)
  -> [char 已在 char_info_cache 中] return                    [缓存命中]
  -> [char 不在缓存中]
     -> _query_char_from_unihan(char)                         [首次加载耗时较大]
        -> unihan_etl.Packager().export()                     [读取 UNIHAN CSV 文件]
        -> 提取 four_corner 和 cangjie 字段
     -> _get_radical_client()                                 [cnradical 首次初始化]
        -> Radical("chinese")                                  [加载部首数据库]
     -> _get_pinyin_of(char)                                  [pypinyin 查询]
        -> pypinyin.pinyin(char)                               [拼音库查询]
     -> _get_stroke(char)                                     [笔画数查询]
        -> _load_strokes()                                     [读取 Unihan_IRGSources.txt]
     -> char_info_cache[char] = {four_corner, cangjie, radical, pinyin, total_strokes}
     -> 写入进程内存字典
```

> **性能标注：** 首次加载 `char_info_cache.json` 中包含 223 个常见字（覆盖武将名用字的 99.6%）。缓存未命中触发 unihan_etl CSV 读取 + cnradical 部首库加载 + pypinyin 查询 + 笔画数查询，总耗时约 1 秒。在 Qt 主线程上执行时会冻结 UI。

### 4.5 汉字特征评分详情

```
各维度评分方法:
------------------
four_corner_score(c1, c2):
  四角号码为 5 位数字码（如 "4490"）
  逐位比较，返回匹配位数 / 总位数（max 1.0）

cangjie_score(c1, c2):
  仓颉码为字母序列（如 "BCM" → "月金一"）
  SequenceMatcher.ratio() 比较序列相似度

radical_score(c1, c2):
  部首字符串相等 → 1.0；不等 → 0.0

综合评分 = four_corner × 0.4 + cangjie × 0.4 + radical × 0.2
```

---

## 五、ocrc_loader（单例加载器）

### 5.1 模板管理器单例

```
get_template_manager()
  -> [单例] module-level 变量 _template_manager
  -> [首次] TemplateManager()                                 [构造 + 自动加载]
  -> return _template_manager
```

### 5.2 OCR 识别器单例

```
get_recognizer(rois, hero_names, reference_size)
  -> [单例] module-level 变量 _recognizer
  -> [首次或 rois/hero_names/reference_size 变更] 重新创建
     -> GeneralRecognizer(rois, hero_names, reference_size)
  -> return _recognizer
```

| 函数 | 文件 | 说明 |
|------|------|------|
| `get_template_manager()` | `ocr_loader.py` | 惰性初始化，首次调用时构造 |
| `get_recognizer(rois, names, reference_size)` | `ocr_loader.py` | ROI、武将列表或参考尺寸变更时自动重建 |

---

## 六、外部调用关系总览

### 6.1 本模块被外部调用

```
src.business.capture_service
  -> AdbCapture.connect() / screencap_full()                   [截图]
  -> get_template_manager().match()                            [模板匹配]
  -> get_recognizer(rois, hero_names, tm.reference_size)
  -> recognize()                                               [按当前截图缩放 ROI 后 OCR]

src.business.ocr_service
  -> get_template_manager().set_template() / reload() / delete_template()
  -> get_recognizer().recognize()

src.ui.mumu_config_dialog
  -> probe_mumu_adb() / probe_all_devices()                    [设备探测]
  -> AdbCapture 连接管理
  -> RoiSelectorDialog → set_template()                        [模板制作]
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
| `probe_mumu_adb()` | `prober.py` | `MumuConfigDialog` | `_get_mumu_candidates()`, `shutil.which()` |
| `probe_all_devices()` | `prober.py` | `MumuConfigDialog`, `probe_mumu_port()` | `_find_mumu_root()`, `subprocess.run()` |
| `probe_mumu_port()` | `prober.py` | `MumuConfigDialog` | `probe_all_devices()` |
| `test_adb_path(adb_path)` | `prober.py` | `MumuConfigDialog` | `subprocess.run(adb version)` |
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
| `TemplateManager.match(image, threshold)` | `template_manager.py` | `_run_ocr()`, `MainWindow` 轮询 | 多尺度 `cv2.matchTemplate()`, `cv2.minMaxLoc()` |
| `TemplateManager.reload()` | `template_manager.py` | `select_template()` | `_load_internal()` |
| `TemplateManager.delete_template()` | `template_manager.py` | `OcrService.delete_template()` | `Path.unlink()` |

### OCR 识别层

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `GeneralRecognizer.recognize(image)` | `recognizer.py` | `CaptureService._run_ocr()`, `OcrService.run_ocr()` | 参考 ROI 缩放 + `_recognize_single()` ×8 |
| `GeneralRecognizer._recognize_single(roi, slot)` | `recognizer.py` | `recognize()` | `_preprocess_roi()`, `_engine.ocr()`, `_correct_with_hero_list()` |
| `GeneralRecognizer._preprocess_roi(roi)` | `recognizer.py` | `_recognize_single()` | `cv2.resize()`, CLAHE, 锐化, 灰度 |
| `GeneralRecognizer._extract_text(result)` | `recognizer.py` | `_recognize_single()` | PaddleOCR 解析 |
| `GeneralRecognizer.warmup()` | `recognizer.py` | `main.py` 启动预热 | `self._engine`, `_load_char_info()`, `pypinyin.pinyin()` |
| `GeneralRecognizer.save_results()` | `recognizer.py` | `CaptureService._run_ocr()` | JSON 序列化 |
| `_correct_with_hero_list(text, names)` | `recognizer.py` | `_recognize_single()` | `_levenshtein_distance()` ×155, `_pick_visually_similar()` |
| `_levenshtein_distance(s1, s2)` | `recognizer.py` | `_correct_with_hero_list()` | DP O(n×m) |
| `_pick_visually_similar(text, candidates)` | `recognizer.py` | `_correct_with_hero_list()` | `_multi_dim_similarity()`, 拼音/笔画 tiebreak |
| `_multi_dim_similarity(c1, c2, db)` | `recognizer.py` | `_pick_visually_similar()` | 四角×0.4 + 仓颉×0.4 + 部首×0.2 |
| `_four_corner_score(c1, c2, db)` | `recognizer.py` | `_multi_dim_similarity()` | `_hc()` |
| `_cangjie_score(c1, c2, db)` | `recognizer.py` | `_multi_dim_similarity()` | `_hc()`, `SequenceMatcher` |
| `_radical_score(c1, c2, db)` | `recognizer.py` | `_multi_dim_similarity()` | `_hc()` |
| `_hc(db, char, key, default)` | `recognizer.py` | 各评分函数 | `_ensure_char_in_cache()` |
| `_ensure_char_in_cache(char)` | `recognizer.py` | `_hc()` | unihan_etl, cnradical, pypinyin |
| `_query_char_from_unihan(char)` | `recognizer.py` | `_ensure_char_in_cache()` | unihan_etl CSV |
| `_get_radical_client()` | `recognizer.py` | `_ensure_char_in_cache()` | `cnradical.Radical()` |
| `_get_pinyin_of(char)` | `recognizer.py` | `_ensure_char_in_cache()` | `pypinyin.pinyin()` |
| `_get_stroke(char)` | `recognizer.py` | `_ensure_char_in_cache()` | `_load_strokes()` |
| `_load_strokes()` | `recognizer.py` | `_get_stroke()` | 读取 Unihan_IRGSources.txt |
| `_load_char_info()` | `recognizer.py` | `_pick_visually_similar()`, `warmup()` | 读取 char_info_cache.json |
| `_pinyin_similarity(c1, c2, db)` | `recognizer.py` | `_pick_visually_similar()` | `_hc()` (tiebreak) |
| `_stroke_diff(c1, c2, db)` | `recognizer.py` | `_pick_visually_similar()` | `_hc()` (tiebreak) |
