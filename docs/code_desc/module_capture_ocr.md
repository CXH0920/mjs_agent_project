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
├── recognizer.py          # GeneralRecognizer — PaddleOCR + 编辑距离矫正
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

```
match(image, threshold=0.8)
  ├── 模板未加载 → (False, 0.0)
  ├── 截图分辨率 < 模板 → (False, 0.0)
  └── cv2.matchTemplate(gray, template, TM_CCOEFF_NORMED)
       └── cv2.minMaxLoc() → max_val ≥ threshold → (True, confidence)
```

**为什么先做模板匹配：** 模板匹配耗时 < 50ms，PaddleOCR 每次 0.5-3 秒。先低成本过滤掉非武将选择页的画面，只在确认目标页面后才执行昂贵的 OCR。

### 3.3 两段式 OCR 识别

`GeneralRecognizer.recognize()` 对 8 个 ROI 逐一识别：

**第一段：PaddleOCR 全量字典识别**

ROI 裁剪 → 放大 3× → CLAHE 增强对比度 → 锐化 → 灰度 → PaddleOCR

**第二段：武将名库编辑距离矫正**

```
PaddleOCR → 文字 + 置信度
  │
  ├── 极高置信度（≥99.5%）且不在武将库？
  │   └── ✅ 信任 OCR，保护新武将不被误矫正
  │
  └── 否则 → 155 武将库编辑距离匹配
       ├── 距离 ≤ 1 且唯一候选 → 直接采纳
       └── 距离 ≤ 1 且多候选 → 多维汉字特征评分决胜
```

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

223 个字覆盖了武将名所有用字的 99.6%，缓存未命中的汉字在运行时由 unihan-etl / cnradical / pypinyin 补齐并写入进程内存。

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

### 4.2 图像预处理流水线

```python
def _preprocess_roi(self, roi: np.ndarray) -> np.ndarray:
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

> **设计思路：** 原始 ROI 只有 ~40×140px，直接送 PaddleOCR 对小字符识别率低。放大使字符像素更密集；CLAHE 解决游戏 UI 渐变背景的干扰；锐化强化边缘清晰度；最后灰度化是 OCR 引擎的期望输入。顺序不可调换。

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
| `get_template_manager()` → `TemplateManager` | 获取模板管理器单例 |
| `get_recognizer(rois, hero_names)` → `GeneralRecognizer` | 获取识别器单例 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | 无外部系统依赖 | 仅依赖 ADB 可执行文件和 PaddleOCR 模型 |
| 被调用方 | `src.business.capture_service` | 持有 AdbCapture 实例，编排截图流程 |
| 被调用方 | `src.business.ocr_service` | 管理 TemplateManager 和 GeneralRecognizer |
| 被调用方 | `src.ui.mumu_config_dialog` | 连接管理、模板制作（ROI 框选） |
| 被调用方 | `src.ui.main_window` | 轮询流程使用截图和 OCR |
