# 模块：屏幕采集与 OCR 识别

> 对应目录：`src/capture/` + `src/ocr/`
> 职责：ADB 连接与截图、MuMu 模拟器探测、图像处理、模板匹配、PaddleOCR 武将名识别

---

## 一、模块职责

本模块连接模拟器屏幕数据和 UI 推荐面板，实现"看到游戏画面 → 识别出武将名"的完整链路：

- **ADB 截图**（`src/capture/`）— 通过 ADB 连接 MuMu 模拟器，执行 `exec-out screencap` 全屏截图，全程内存中处理
- **设备探测** — 自动查找 ADB 路径和 MuMu 实例的 ADB 端口
- **模板匹配**（`src/ocr/`）— OpenCV 模板匹配快速过滤非武将选择页画面
- **OCR 识别**（`src/ocr/`）— PaddleOCR 批量识别名称区域，按字数门禁、候选闭包和候选内汉字特征评分确认名称

---

## 二、文件结构

```
src/capture/
├── __init__.py
├── adb_screen.py          # AdbCapture — ADB 连接与截图
├── image_validation.py    # 不可信图片输入校验（格式、体积、像素）
├── prober.py              # MuMu 设备自动探测
└── image_utils.py         # 图像工具（PIL ↔ QPixmap / 剪贴板 / 保存）

src/ocr/
├── __init__.py
├── template_manager.py    # TemplateManager — OpenCV 模板匹配
├── image_preprocessor.py  # ImagePreprocessor — 放大、CLAHE、锐化、灰度
├── official_board_parser.py # 官方榜单新旧版式、数据行锚点、单元格与数字模板算法
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
- 图片输入防护：本地 OCR/ROI 仅接受实际 PNG/JPEG，ADB 数据仅接受实际 PNG；统一限制 6 MiB、4,000,000 像素，并将 Pillow 解压炸弹警告提升为异常

**`screencap` 使用 `exec-out` 模式**而非 `shell screencap`：
```python
adb -s 127.0.0.1:16448 exec-out screencap -p
```
`exec-out` 直接输出二进制到 stdout，不经过设备 shell 解析，更快且不会损坏二进制 PNG 数据。

ADB 或模拟器渲染通道偶发繁忙时，`stdout` 可能为空或只返回不完整的 PNG。`screencap_full()` 会先调用 `Image.load()` 验证完整性，并对这两类瞬态结果最多重试 3 次；明确的设备离线错误仍立即失效当前连接。

### 3.2 模板匹配

模板匹配是 OCR 流程的**前置过滤器**，执行在 PaddleOCR 之前：

模板制作时会在 `templates/wujiang_select.json` 保存制作截图的参考尺寸和原始框选坐标。旧模板没有坐标元数据时，兼容使用 2560×1440 参考尺寸并保留全屏搜索。

匹配时根据当前截图与参考尺寸计算基础缩放比例，并在基础比例附近尝试多个比例，
选择置信度最高的结果：

```
match(image, threshold=0.8)
  ├── 模板未加载 → (False, 0.0)
  ├── 计算当前截图的基础缩放比例
  ├── 新模板：在原始位置的缩放局部区域按基础比例匹配
  ├── 旧模板：在全屏按基础比例匹配
  ├── 未命中：回退到全屏 0.85、0.925、1.0、1.075、1.15 倍多尺度搜索
  └── cv2.minMaxLoc() → max_val ≥ threshold → (True, confidence)
```

**为什么先做模板匹配：** 基础比例局部匹配可快速过滤正常页面；局部不命中或旧模板仍会全屏多尺度回退，保证识别率。任务日志记录 `outcome`、最高置信度、缩放与匹配策略，便于判断是否需要重新制作模板或调整阈值；只有模板命中后才执行昂贵的 PaddleOCR。

**人工识别例外：** 用户从页面点击“识别当前阵容”或导入本地图片时会传入 `force_ocr=True`，此类已明确指定识别页类型的请求跳过模板匹配，直接执行 OCR。只有自动轮询仍将模板匹配作为前置门禁，避免对无关游戏画面反复执行 OCR。

对局攻略模板应优先框选左侧常驻功能图标等固定 UI，避开回合数字、角色立绘和战场背景；这类内容会随对局状态变化，不能作为可靠的页面特征。

应用在主窗口显示前即向同一 `OcrWorker` 队列提交预热任务，不依赖模拟器连接。预热状态为 `idle`、`warming`、`ready` 或 `failed`，通过 `ocr_warmup_state_changed` 通知 UI；失败后允许重新提交。预热在 worker 线程加载 PaddleOCR、加载静态字符特征缓存，并以名称拼图的代表尺寸执行一次检测和识别推理；后续选将推荐和对局攻略识别复用该实例，因此首次实际 OCR 不再承担模型或运行时算子初始化。

ADB 截图需要 OCR 时，`CaptureService` 会先复制图像并提交 OCR worker，原始图交给独立的单线程 `image-save` 执行器压缩 PNG。OCR 完成不等待保存；保存完成通过 `image_saved` 通知。对于仍在写入的 ADB 截图，`capture_completed.save_path` 为 `None`；本地导入则保留其已存在的源文件路径。

自动轮询中，对局攻略仅在选将页命中后才会激活。对局攻略模板未命中时会回退执行一次候选角色 OCR；至少确认 3 个角色名才自动切换页面并停用该任务，`unresolved`、`unknown` 和 `conflict` 不计入数量。模板在此路径中用于加速命中，而非阻断不同战场 UI 的识别。

### 3.3 多路证据与候选确认

`GeneralRecognizer.recognize()` 先分别预处理同类 ROI，再横向拼图为一次 PaddleOCR 检测。选将页使用一张名称拼图；对局攻略的名称和阵营各使用一张拼图，避免尺寸或方向不同的区域混合。名称槽位记录批量增强图证据；缺失、多候选、冲突或置信度低于 0.8 时，才追加增强图与仅放大原图的逐槽识别。ROI 坐标以参考分辨率保存，
识别前会分别按当前截图宽高进行换算，因此支持页面比例基本不变时的分辨率变化：

```
参考 ROI → 当前截图宽高缩放 → 裁剪 → PaddleOCR
```

换算后的识别流程为：

**第一段：PaddleOCR 全量字典识别**

ROI 裁剪 → 放大 3× → CLAHE 增强对比度 → 锐化 → 灰度 → PaddleOCR

**第二段：候选确认与页面消歧**

```
PaddleOCR → 文字 + 置信度
  │
  └── 字数门禁与当前武将词表候选解析
       ├── 精确命中 → exact
       ├── 严格前缀（缺字）→ 只保留前缀白名单；唯一前缀至少已识别 2 字才确认
       ├── 等长且仅错一字、唯一候选字形分 ≥ 0.55 → unique_similarity
       ├── 等长多候选 → 候选内评分；双门槛、双证据族一致才确认
       ├── 同时命中严格前缀与等长候选 → 合并候选，length_mode=uncertain
       └── 其他增删字 → uncertain，保持未确认
  └── 多路非空候选集合取交集
       ├── 交集为空 → conflict，禁止跨白名单覆盖
       └── 交集非空 → 继续确认或保持 unresolved
  └── 页面约束
       ├── 仅对原本有多个候选且 length_mode 为 missing/complete 的槽位消歧
       └── 重复名称保留唯一更强证据；同等级全部回退
```

等长多候选的自动确认要求每路 OCR 置信度 `>= 0.7`、最高错字字形分 `>= 0.4`、与第二名分差 `>= 0.15`，并且 `enhanced` 与 `plain` 两个独立证据族支持同一结果。`batch_enhanced` 与 `single_enhanced` 同属 `enhanced`，不能重复计票。页面唯一性不会提升 `uncertain`，也不会把只有一个但未过字形安全门槛的候选自动提升。

结构化结果为 `{index, raw_name, name, candidates, resolution, length_mode, confidence, evidence}`。`name` 只保存已确认名称；`length_mode` 为 `complete`、`missing`、`uncertain` 或 `unknown`；`resolution` 包含 `exact`、`unique_prefix`、`unique_similarity`、`multi_similarity`、`slot_unique`、`manual`、`unresolved`、`unknown` 和 `conflict`。官方榜单仍使用独立的整榜解析与写入门禁，本节不抽取两条链路的共用解析器。

名称 ROI 内的卡框和底部定位字会污染像素行分割，边缘槽位也不稳定，因此当前不把视觉字符数作为硬门禁。势力关联可在后续作为附加证据，但只能过滤当前候选白名单，不能引入白名单外名称；本次未接入该逻辑。

官方榜单导入不使用页面模板匹配或 `GeneralRecognizer` 的页面识别流程，但会以一个 `OfficialImportTask` 进入通用 `OcrWorker` 队列，并复用 worker 持有的 PaddleOCR 引擎。`src.ocr.official_board_parser` 提供旧版长图和新版分页版式识别、面板切分、数据行恢复、单元格切分和胜率数字模板算法。`src.business.recognition.official_data_import_service` 继续独立负责受限候选繁体兜底、整榜唯一性和正式写入门禁；常规页面识别只复用简体引擎，不加载繁体模型，也不复用整榜缺失集合。两条链路共享 OCR 串行资源，但候选规则暂不抽取为公共解析器。

### 3.4 候选内单字字形评分

常规截图只对“与候选等长且恰好一个字符不同”的名称评分。缺字前缀和其他增删字结果只用于建立候选白名单，不参与字形决胜。对每个合法候选，仅计算那个不同字符的加权相似度：

| 维度 | 权重 | 说明 |
|------|------|------|
| 四角号码 | 40% | 反映汉字四角结构 |
| 仓颉码 | 40% | 反映字形编码层次 |
| 部首 | 20% | 直接定位字的大类 |

评分公式：
```python
if len(text) == len(candidate) and mismatch_count == 1:
    score = four_corner * 0.4 + cangjie * 0.4 + radical * 0.2
else:
    score = None                        # 不参与常规截图的候选决胜
```

唯一候选使用 0.55 安全门槛；多候选使用 0.4 绝对门槛和 0.15 领先门槛，并要求两个独立证据族一致。评分只负责在合法候选中排序，不能改变字数门禁产生的候选集合。

任一字符的某项特征缺失时，该维度贡献 0 分；尤其不能把缺失四角码补成相同的 `00000` 后计为满分。

### 3.5 汉字特征缓存

汉字特征数据采用三层策略：

| 层 | 速度 | 覆盖 |
|----|------|------|
| `char_info_cache.json`（299 字） | ~10ms | 当前武将名全部字符 + 常见 OCR 误识字 |
| 运行时原始库（按需补齐） | ~1060ms | 任意汉字（理论兜底） |

`CharacterFeatureRepository` 默认读取 `src/data/char_info_cache.json`，也可在构造时注入其他路径。静态缓存覆盖当前英雄名的全部字符；运行 `scripts/build_character_feature_cache.py` 可在 `heroes.json` 更新后补齐并以 UTF-8/LF 原子写入。缓存未命中的汉字仍由 unihan-etl / cnradical / pypinyin 按需补齐到进程内存。pypinyin 失败会记录一次 warning 并禁用后续拼音查询，cnradical 单字失败会记录具体字符；两者均降级为空特征而不中断 OCR。

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

识别日志会按槽位记录缩放后的 ROI 坐标，以及 PaddleOCR 返回的原始文本和置信度。每个任务完成后还会记录 ADB 截图、模板加载与匹配、模型初始化、名称/阵营预处理与 OCR、名称纠错、结果落盘和总耗时，便于比较冷启动与热启动。格式类似：

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
| `GeneralRecognizer.recognize(image)` → `list[dict]` | 识别页面名称并返回候选、状态和多路证据 |
| `ImagePreprocessor.preprocess_roi(roi)` → `np.ndarray` | OCR 图像预处理 |
| `official_board_parser.find_data_boundaries(...)` → `list[int]` | 检测官方榜单数据行边界 |
| `official_board_parser.split_row_cells(...)` → `dict[str, np.ndarray]` | 按官方版式切分行单元格 |
| `official_board_parser.prepare_rate_templates(...)` | 构建榜单数字模板并预计算胜率 OCR |
| `CharacterSimilarityService.correct_hero_name(text, hero_names)` → `str` | 武将名称纠错 |
| `CharacterSimilarityService.is_safe_single_substitution(text, candidate)` → `bool` | 判断唯一错字是否达到自动纠正门槛 |
| `CharacterFeatureRepository(cache_path=None)` | 汉字特征缓存加载、动态补齐与保存 |
| `get_template_manager()` → `TemplateManager` | 获取模板管理器单例 |
| `OcrWorker.submit(task)` | 串行执行预热、常规 `OcrTask` 或官方 `OfficialImportTask`，并通过任务完成信号返回结果 |

活动识别路径由 `src.business.recognition.ocr_worker.OcrWorker` 统一执行。worker 在自己的线程内缓存 `GeneralRecognizer` 和 PaddleOCR 引擎，配置相同的连续任务复用识别器；官方榜单服务也只在该线程内使用注入引擎。手动截图、文件导入、轮询与官方榜单导入不会在不同线程同时运行 PaddleOCR。

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | 无外部系统依赖 | 仅依赖 ADB 可执行文件和 PaddleOCR 模型 |
| 被调用方 | `src.business.emulator.capture_service` | 持有 AdbCapture 实例，编排截图流程 |
| 被调用方 | `src.business.recognition.ocr_service` | 管理 TemplateManager 和 GeneralRecognizer |
| 被调用方 | `src.ui.configuration.mumu_config_dialog` | 连接管理、模板制作（ROI 框选） |
| 被调用方 | `src.ui.app.main_window` | 轮询流程使用截图和 OCR |
