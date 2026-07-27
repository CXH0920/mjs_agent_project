# 模块：屏幕采集与 OCR 识别

> 对应目录：`src/capture/` + `src/ocr/`
> 职责：ADB 连接与截图、MuMu 模拟器探测、图像处理、模板匹配、PaddleOCR 武将名识别

---

## 一、模块职责

本模块连接模拟器屏幕数据和 UI 推荐面板，实现"看到游戏画面 → 识别出武将名"的完整链路：

- **ADB 截图**（`src/capture/`）— 通过 ADB 连接 MuMu 模拟器，执行 `exec-out screencap` 全屏截图，全程内存中处理
- **设备探测** — 自动查找 ADB 路径和 MuMu 实例的 ADB 端口
- **模板匹配**（`src/ocr/`）— OpenCV 模板匹配快速过滤非武将选择页画面
- **OCR 识别**（`src/ocr/`）— PaddleOCR 识别 8 个武将名称区域，编辑距离矫正 + 汉字特征评分提高准确率

---

## 二、文件结构

```
src/capture/
├── __init__.py
├── adb_screen.py          # AdbCapture — ADB 连接与截图
├── prober.py              # MuMu 设备自动探测
└── image_utils.py         # 图像工具（PIL ↔ QPixmap / 剪贴板 / 保存）

src/ocr/
├── __init__.py
├── template_manager.py    # TemplateManager — OpenCV 模板匹配
├── image_preprocessor.py  # ImagePreprocessor — 放大、CLAHE、锐化、灰度
├── character_feature_repository.py  # 汉字特征缓存与动态补齐
├── character_similarity.py # CharacterSimilarityService — 名称纠错
├── recognizer.py          # GeneralRecognizer — ROI、PaddleOCR 与组件编排
└── ocr_loader.py          # 单例延迟加载
```

---

## 三、核心逻辑

### 3.1 ADB 截图链路

```
AdbCapture(adb_path, adb_port)
  ├── connect() → adb connect 127.0.0.1:port
  ├── disconnect() → adb disconnect
  ├── screencap_full() → adb exec-out screencap -p → PIL Image（无效输出最多重试 3 次）
  └── device_serial → 可读写，切换目标设备
```

持续轮询调用 `screencap_full(log_success=False)`，并将模板加载、OCR 完成、冷却等正常高频事件记录为 `DEBUG`。运行日志默认仅保留连接状态及截图/OCR 的警告和错误，避免轮询成功记录持续刷屏。

轮询中，`match_guide` 仅在 `hero_selection` 模板命中后被激活一次；对局攻略识别成功即停用，直到下一次选将模板再次命中才重新激活。

**安全设计：**
- 命令注入防护：`_run_adb(*args)` 使用列表参数而非字符串拼接
- 设备序列号格式校验：`IP:port` 格式 + 端口范围 1-65535
- 超时保护：`subprocess.run` 设置 timeout

**`screencap` 使用 `exec-out` 模式**而非 `shell screencap`：
```python
adb -s 127.0.0.1:16448 exec-out screencap -p
```
`exec-out` 直接输出二进制到 stdout，不经过设备 shell 解析，更快且不会损坏二进制 PNG 数据。

ADB 或模拟器渲染通道偶发繁忙时，`stdout` 可能为空或只返回不完整的 PNG。`screencap_full()` 会先调用 `Image.load()` 验证完整性，并对这两类瞬态结果最多重试 3 次；明确的设备离线错误仍立即失效当前连接。

### 3.2 模板匹配

模板匹配是 OCR 流程的**前置过滤器**，执行在 PaddleOCR 之前：

模板制作时会在 `templates/wujiang_select.json` 保存制作截图的参考尺寸。
旧模板没有元数据时，兼容使用 2560×1440 作为参考尺寸。

匹配时根据当前截图与参考尺寸计算基础缩放比例，并在基础比例附近尝试多个比例，
选择置信度最高的结果：

```
match(image, threshold=0.8)
  ├── 模板未加载 → (False, 0.0)
  ├── 计算当前截图的基础缩放比例
  ├── 尝试 0.85、0.925、1.0、1.075、1.15 倍附近的模板尺寸
  └── cv2.minMaxLoc() → max_val ≥ threshold → (True, confidence)
```

**为什么先做模板匹配：** 模板匹配耗时 < 50ms，PaddleOCR 每次 0.5-3 秒。先低成本过滤掉非武将选择页的画面，只在确认目标页面后才执行昂贵的 OCR。

### 3.3 两段式 OCR 识别

`GeneralRecognizer.recognize()` 对 8 个 ROI 逐一识别。ROI 坐标以参考分辨率保存，
识别前会分别按当前截图宽高进行换算，因此支持页面比例基本不变时的分辨率变化：

```
参考 ROI → 当前截图宽高缩放 → 裁剪 → PaddleOCR
```

换算后的识别流程为：

**第一段：PaddleOCR 全量字典识别**

ROI 裁剪 → 放大 3× → CLAHE 增强对比度 → 锐化 → 灰度 → PaddleOCR

**第二段：武将名库编辑距离矫正**

```
PaddleOCR → 文字 + 置信度
  │
  └── 165 武将库编辑距离匹配
       ├── 距离 ≤ 1 且唯一候选 → 直接采纳
       └── 距离 ≤ 1 且多候选 → 多维汉字特征评分决胜
       └── 无候选且极高置信度（≥99.5%）→ 保留原文，保护新武将
```

官方榜单导入不使用页面模板匹配、通用 `OcrWorker` 队列或 `GeneralRecognizer`。它在 `src.business.official_data_import_service` 中单独创建 PaddleOCR 实例，并依赖公开的 `CharacterSimilarityService.correct_hero_name()` 完成词表纠错；完整词表候选优先和单字逐字兜底均局限在该服务，因而不会改变选将模板 OCR、文件导入或轮询的识别策略。胜率不复用中文 OCR 结果作为最终值，而是通过同一榜单的数字字形模板识别，避免将被裁剪或形近的 `4` 误读为 `1`。

### 3.4 多维汉字特征评分

当编辑距离筛选出多个候选时（如 OCR 输出"王剪" → 候选 ["王翦", "王异"]），逐字符加权评分：

| 维度 | 权重 | 说明 |
|------|------|------|
| 四角号码 | 40% | 反映汉字四角结构 |
| 仓颉码 | 40% | 反映字形编码层次 |
| 部首 | 20% | 直接定位字的大类 |

评分公式（逐字符比较）：
```python
score = 0
for tc, cc in zip(text, candidate):
    if tc == cc:
        score += 1.0                    # 相同字符满分
    else:
        score += multi_dim_similarity   # 四角×0.4 + 仓颉×0.4 + 部首×0.2
score -= 0.5 * length_diff * 2         # 长度惩罚
```

### 3.5 汉字特征缓存

汉字特征数据采用三层策略：

| 层 | 速度 | 覆盖 |
|----|------|------|
| `char_info_cache.json`（223 字） | ~10ms | 武将名 + 常见 OCR 误识字 |
| 运行时原始库（按需补齐） | ~1060ms | 任意汉字（理论兜底） |

`CharacterFeatureRepository` 默认读取 `src/data/char_info_cache.json`，也可在构造时注入其他路径。223 个字覆盖了武将名所有用字的 99.6%，缓存未命中的汉字在运行时由 unihan-etl / cnradical / pypinyin 补齐并写入进程内存；需要落盘时由仓库的 `save()` 以 UTF-8/LF 原子写入。

---

## 四、关键代码片段

### 4.1 设备探测（prober.py）

```python
def probe_mumu_adb() -> str:
    # 1. PATH 查找
    adb = shutil.which("adb")
    if adb:
        return adb
    # 2. 注册表或环境变量
    mumu_home = os.getenv("MUMU_HOME") or _read_registry()
    if mumu_home:
        return os.path.join(mumu_home, "nx_main", "adb.exe")
    # 3. 常见安装路径
    for base in ["D:/模拟器/MuMu Player 12", "C:/Program Files/MuMu Player 12"]:
        path = os.path.join(base, "nx_main", "adb.exe")
        if os.path.exists(path):
            return path
    return ""
```

> **设计思路：** 三个优先级覆盖了大多数场景：系统 PATH 最快，注册表/环境变量次之，常见安装路径兜底。函数式设计无内部状态，可被多处调用而不互相影响。

`probe_all_devices_with_status()` 是配置页使用的状态化版本：它在 `MuMuManager.exe` 非零退出或超时时等待 0.2 秒重试一次，返回 `(devices, error)`。空设备列表且 `error` 为空表示正常枚举但没有实例；`error` 非空表示探测失败，UI 必须保留上次成功的列表而非清空当前选择。

### 4.2 图像预处理流水线

```python
def ImagePreprocessor.preprocess_roi(roi: np.ndarray) -> np.ndarray:
    # 1. 放大 3×
    roi = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    # 2. CLAHE 自适应直方图均衡
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab[..., 0] = clahe.apply(lab[..., 0])
    roi = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    # 3. 锐化
    kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
    roi = cv2.filter2D(roi, -1, kernel)
    # 4. 灰度
    return cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
```

> **设计思路：** 2560×1440 基准下，默认武将名称 ROI 为 50×145px。额外的 5px 高度用于给竖排名称留出上下缓冲，降低字符被截断的概率。放大使字符像素更密集；CLAHE 解决游戏 UI 渐变背景的干扰；锐化强化边缘清晰度；最后灰度化是 OCR 引擎的期望输入。顺序不可调换。

识别日志会按槽位记录缩放后的 ROI 坐标，以及 PaddleOCR 返回的原始文本和置信度，格式类似：

```text
武将 6 OCR ROI: x=1615, y=370, w=50, h=145 (参考 ROI=[1615, 370, 50, 145])
武将 6 OCR 原始结果: text='祝融夫', confidence=0.9980
```

---

## 五、接口说明

### Capture 层公共方法

| 类/函数 | 说明 |
|---------|------|
| `AdbCapture(adb_path, adb_port)` | 构造 ADB 截图器 |
| `AdbCapture.connect()` → `(bool, str)` | 连接模拟器 |
| `AdbCapture.screencap_full()` → `(bool, Image\|str)` | 全屏截图 |
| `probe_mumu_adb()` → `str` | 探测 ADB 路径 |
| `probe_all_devices()` → `list[MuMuDeviceInfo]` | 列出 MuMu 实例 |
| `pil_to_qpixmap(image)` → `QPixmap` | PIL → Qt 转换 |

### OCR 层公共方法

| 类/方法 | 说明 |
|---------|------|
| `TemplateManager.match(image, threshold)` → `(bool, float)` | 模板匹配 |
| `TemplateManager.set_template(image, roi)` | 制作模板 |
| `GeneralRecognizer.recognize(image)` → `list[dict]` | 识别 8 个武将名 |
| `ImagePreprocessor.preprocess_roi(roi)` → `np.ndarray` | OCR 图像预处理 |
| `CharacterSimilarityService.correct_hero_name(text, hero_names)` → `str` | 武将名称纠错 |
| `CharacterFeatureRepository(cache_path=None)` | 汉字特征缓存加载、动态补齐与保存 |
| `get_template_manager()` → `TemplateManager` | 获取模板管理器单例 |
| `OcrWorker.submit(task)` | 串行执行模板匹配与 OCR，并通过任务完成信号返回结果 |

活动识别路径由 `src.business.ocr_worker.OcrWorker` 统一执行。worker 在自己的线程内缓存 `GeneralRecognizer`，配置相同的连续任务复用该实例；手动截图、文件导入与轮询不会直接调用全局识别器。

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | 无外部系统依赖 | 仅依赖 ADB 可执行文件和 PaddleOCR 模型 |
| 被调用方 | `src.business.capture_service` | 持有 AdbCapture 实例，编排截图流程 |
| 被调用方 | `src.business.ocr_service` | 管理 TemplateManager 和 GeneralRecognizer |
| 被调用方 | `src.ui.mumu_config_dialog` | 连接管理、模板制作（ROI 框选） |
| 被调用方 | `src.ui.main_window` | 轮询流程使用截图和 OCR |
