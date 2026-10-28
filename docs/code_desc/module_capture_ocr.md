# 模块：屏幕采集与 OCR 识别

> 对应目录：`src/capture/` + `src/ocr/`
> 职责：屏幕采集双形态（ADB 连接模拟器截图含 raw 帧提速 / 官方 PC 客户端窗口直连 WGC+BitBlt）、多品牌模拟器探测（emulator_discovery 包，五品牌）与 PC 客户端窗口枚举、图像处理、模板匹配（分层加速）、RapidOCR 双套件武将名识别（v6 主 + v4 复核，页型模板方法 `_recognize_page`）与未决槽位复核、白名单治理
> 文档日期：2026-10-25（基线 ed8b4ac）

---

## 一、模块职责

本模块连接游戏画面数据和 UI 推荐面板，实现"看到游戏画面 → 识别出武将名"的完整链路：

- **屏幕采集**（`src/capture/`）— 双形态图像源，下游管线无感复用：
  - **模拟器 ADB 截图**：通过 ADB 连接安卓模拟器（五品牌），执行 `exec-out screencap` 全屏截图，全程内存中处理；支持 raw 帧模式跳过 PNG 编解码提速
  - **官方 PC 客户端直连**（`pc_client.py`，2026-10-25）：枚举客户端窗口（进程 `mjs.exe` + 窗口类 `UnityWndClass` 双条件），Windows.Graphics.Capture 主路径 + GDI BitBlt 兜底 + 黑帧双指标检测；模块全程只读窗口画面，不发送任何输入（法律红线）
- **设备探测**（`src/capture/emulator_discovery/`）— 自动查找 ADB 路径和五品牌（MuMu/雷电/夜神/逍遥/蓝叠）实例的 ADB 端口，函数式注册表分发
- **模板匹配**（`src/ocr/`）— OpenCV 模板匹配快速过滤非武将选择页画面，分层加速先粗筛后精匹配
- **OCR 识别**（`src/ocr/`）— RapidOCR/ONNX 双套件批量识别名称区域（PP-OCRv6-small 主引擎 + PP-OCRv4-mobile 复核引擎，均 CPU），按字数门禁、候选闭包和候选内汉字特征评分确认名称；未决槽位由复核引擎做候选内确认
- **白名单治理**（`src/ocr/`）— 名称纠错白名单的静态冲突检查与错法半自动补对闭环

---

## 二、文件结构

```
src/capture/
├── __init__.py
├── adb_screen.py          # AdbCapture — ADB 连接与截图（品牌无关，含 raw 帧模式、架构防火墙声明）
├── pc_client.py           # PcClientCapture — 官方 PC 客户端窗口直连采集（WGC 主路径 + BitBlt 兜底，
│                          #   窗口枚举三态解析、黑帧双指标检测；架构防火墙声明：不发送任何输入）
├── emulator_discovery/               # 多品牌设备探测包（2026-10 自原单文件探测模块拆包）
│   ├── __init__.py        # 函数式注册表：EMULATOR_DISCOVERY_MODULES 五品牌分发 + CONNECTION_TYPE_PC(pc_client)
│   │                      #   + EMULATOR_BRANDS/EMULATOR_TYPES/EMULATOR_LABELS/EMULATOR_HINTS + normalize_emulator_type
│   ├── models.py          # EmulatorDeviceInfo 值对象（品牌无关，依赖最底层）
│   ├── mumu.py            # MuMu 12：MuMuManager console 主路径 + 注册表/进程反查/常见路径
│   ├── ldplayer.py        # 雷电 9：ldconsole list2（list2 不报端口，按 5555+2n 推导）
│   ├── nox.py             # 夜神：NoxConsole list 七列 + netstat 按 VM PID 精确关联端口（62001+24n）
│   ├── memu.py            # 逍遥：.memu 配置直读 ADB 转发 hostport（console 为 GUI 无 stdout，死路径已移除）
│   ├── bluestacks.py      # 蓝叠 5：注册表 BstAdbPort（国际版）→ bluestacks.conf 解析（国内版 5.22）
│   ├── port_scan.py       # 端口段兜底扫描（8 并发 socket 试连，PORT_RULES 品牌端口带；蓝叠无固定段不参与）
│   └── console_utils.py   # console 工具调用与 UTF-8→GBK 解码 + find_process_dir 进程反查安装目录
├── image_validation.py    # 不可信图片输入校验（格式、体积、像素）
└── image_utils.py         # 图像工具（PIL ↔ QPixmap / 剪贴板 / 保存）

src/ocr/
├── __init__.py
├── template_manager.py    # TemplateManager — OpenCV 模板匹配（分层加速）
├── image_preprocessor.py  # ImagePreprocessor — 放大、灰度、gamma 差异视图回退
├── official_board_parser.py # 官方榜单新旧版式、数据行锚点、单元格与数字模板算法
├── card_grid_detector.py   # 2v2 巅峰赛牌面内容驱动卡位检测 + 派生名条 ROI
├── roi_config.py           # OcrRoiConfig / OcrRoiLayout — 选将页与对局攻略页 ROI 布局及本地覆盖
├── character_feature_repository.py  # 汉字特征缓存与动态补齐
├── character_similarity.py # CharacterSimilarityService — 名称纠错与白名单静态冲突检查（基线 10 对）
├── recognizer.py          # GeneralRecognizer — ROI、OCR 引擎与组件编排、未决槽位复核
├── name_resolution.py         # 名称证据解析与页面消歧（纯决策层，审计 G3 4.6a）
├── batch_canvas.py             # 名称拼图批量画布（分块/拼接/碎片重排，审计 G3 4.6b）
├── engine_loader.py       # OCR 引擎装载层（RapidOCR/ONNX：PP-OCRv6 主引擎 + PP-OCRv4 复核引擎，
│                          #   套件级惰性单例 + 熔断 + RapidOcrEngine 包装类；1694ab7 替代 paddle_loader）
└── template_registry.py  # 模板管理器单例
```

---

## 三、核心逻辑

### 3.1 ADB 截图链路

```
AdbCapture(adb_path, adb_port=0, screenshot_mode="auto", emulator_type="mumu")
  ├── _resolve_target() → 精确 ADB 目标
  │     └── device_serial > 127.0.0.1:port > discover_running_devices(emulator_type) 唯一实例
  ├── connect() → adb connect <target> → adb -s <target> get-state == "device"
  │     （validate_capture=True 时连通后截帧校验，失败回滚；5555 端口未注册时
  │      双查 emulator-<端口-1> 序列号归并，雷电 adb 主动扫描区形态）
  ├── check_device() → 复用连接时确认目标设备仍在线，失效即清除会话缓存
  ├── disconnect() → adb disconnect
  ├── screencap_full(log_success=True) → adb exec-out screencap -p → PIL Image（最多 3 次尝试）
  └── device_serial → 可读写，切换目标设备（IP:port 时同步 adb_port）
```

`connect()` 的目标解析有优先级：显式 `device_serial` 优先，其次是构造端口，最后仅在运行中的 MuMu 实例恰好一个时自动选定；无实例或存在多个实例直接返回错误消息，由配置页提示用户选择设备，避免误连其它在线设备。已缓存的会话在重试前先经 `check_device()` 验证，失效才重新走完整连接流程。

持续轮询调用 `screencap_full(log_success=False)`，并将模板加载、OCR 完成、冷却等正常高频事件记录为 `DEBUG`。运行日志默认仅保留连接状态及截图/OCR 的警告和错误，避免轮询成功记录持续刷屏。

轮询中，`match_guide` 仅在 `hero_selection` 模板命中后被激活一次；对局攻略识别成功即停用，直到下一次选将模板再次命中才重新激活。

**安全设计：**
- 命令注入防护：`_run_adb(*args)` 使用列表参数而非字符串拼接
- 目标设备精确校验：连接后必须 `adb -s <serial> get-state` 返回 `device`，在线但不属于本次目标设备不算成功；`device_serial` setter 仅接受 `IP:port` 且端口为纯数字的写法（用于同步内部端口），不做数值范围校验
- 超时保护：`subprocess.run` 设置 timeout（截图 15s，连接类命令默认 10s，断开 5s）
- 图片输入防护：本地 OCR/ROI 仅接受实际 PNG/JPEG，ADB 数据仅接受实际 PNG；统一限制 6 MiB、4,000,000 像素，并将 Pillow 解压炸弹警告提升为异常（`image_validation.MAX_IMAGE_SIZE_BYTES` / `MAX_IMAGE_PIXELS`）
- **架构防火墙声明**（637102b）：模块文档字符串明确声明仅提供屏幕读取能力，禁止实现 ADB 输入（tap/click/input）、自动点击/选将/战斗、游戏进程注入、内存修改、hook、反作弊绕过，违反项目法律红线（见 AGENTS.md 与 CLAUDE.md）

**`screencap` 使用 `exec-out` 模式**而非 `shell screencap`：
```python
adb -s 127.0.0.1:16448 exec-out screencap -p
```
`exec-out` 直接输出二进制到 stdout，不经过设备 shell 解析，更快且不会损坏二进制 PNG 数据。

ADB 或模拟器渲染通道偶发繁忙时，`stdout` 可能为空或只返回不完整的 PNG。`screencap_full()` 经 `load_png_image_bytes()` 校验实际格式与像素数并强制解码（等价于 `verify()` + `load()`），对空输出与解码失败两类瞬态结果最多执行 3 次尝试（首次 + 2 次重试，间隔 0.15s）。命令返回码非零属于明确故障，立即返回错误而不重试；错误消息命中 `device offline`、`device not found`、`transport closed` 等标记时同步清除已失效的连接会话。

**raw 帧模式提速（cd35c98）**：构造时 `screenshot_mode` 参数支持 `auto`（默认，raw 优先失败回退 PNG）、`raw`（仅 raw）和 `png`（仅 PNG）。raw 模式直接解析 Android screencap 输出的裸像素帧（16 字节头：宽/高/像素格式/色彩空间各 u32 小端 + RGBA_8888 或 RGBX_8888 裸像素），跳过 PNG 编解码，减少约 50-70% 的截图耗时。校验（头长度、像素格式、尺寸、字节数一致性）任一不满足时返回 `None`，由 `_capture_and_decode()` 自动回退 PNG 模式。`CaptureService.capture_for_poll()` 在轮询路径直接复用 raw 帧解码结果，避免额外的 PIL 解码。

### 3.1.1 官方 PC 客户端直连（pc_client.py，2026-10-25）

不依赖 ADB 的第二条图像源：直接读取官方 PC 客户端窗口画面（Unity 客户端 `0.9.3002.144`，2026-10-25 真机取证定稿，选型依据见 `docs/design/pc_official_client_capture_design.md`）。接口对齐 `AdbCapture`（`connected` / `device_serial` / `connect` / `disconnect` / `screencap_full`），`CaptureConnectionManager` 按形态构建对应实例，下游 OCR 管线无感复用。

**窗口识别（双条件，R1 定稿）**：进程名 `mjs.exe` + 窗口类 `UnityWndClass` 经 `EnumWindows` 枚举；标题仅作展示与配置键（`PC_WINDOW_TITLE`，默认"名将杀"），不作为匹配键（标题可被同名进程伪造）。`probe_pc_windows()` 返回 `PcWindowInfo` 候选列表（hwnd/pid/标题/客户区尺寸/是否最小化），三态语义：空列表 = 未启动（提示先启动客户端）、单窗自动选定、多窗要求用户在设备列表显式选择（不支持多开）。

**DPI 硬前置**：进程级 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` 幂等声明（`ensure_dpi_awareness()`）——未声明进程的窗口坐标在 >100% DPI 屏上被系统虚拟化谎报（真机取证：125% 副屏客户区谎报 2048×1088，真实 2560×1361），所有坐标裁剪都会失准。Qt6 宿主通常已自行声明，重复声明失败无害。

**采集主路径 WGC（Windows.Graphics.Capture，经 `windows-capture==2.0.1` 库）**：`_WgcSession` 后台线程按**窗口句柄定向**连接（`window_hwnd`），帧回调持续刷新最新 BGRA 帧。必须句柄而非标题连接：库的 `window_name` 是标题**子串匹配**，而工具自身主窗口"名将杀 Agent"包含"名将杀"，且连接时工具窗口必在 Z 序顶端，实测会 attach 到工具自身窗口截出工具 UI（2026-10-26 真机事故）；游戏标题的任何子串均为工具标题子串，字符串层面无解，句柄定向是唯一根治。WGC 优势为合成器级取图，窗口被遮挡仍可采集；要求 Win10 1903+，失败（依赖缺失/系统不支持/首帧 5 秒超时）静默降级 BitBlt。

**WGC 帧后处理（`_crop_wgc_frame_to_client`）**：帧为整窗（含标题栏）BGRA 纹理，三步还原为客户区 RGB——
1. **通道反转**：取前三通道后必须红蓝互换（BGRA→RGB），否则游戏暖色调整体偏蓝紫（三通道相等的灰白色 UI 不会暴露该互换，真机才发现）；
2. **客户区裁剪**：客户区在帧内的偏移每帧实算——DWM 可见边框（`DwmGetWindowAttribute(EXTENDED_FRAME_BOUNDS)`）查询成功时帧原点即边框左上角；失败时按"窗口阴影左右对称"用（矩形宽−帧宽）/2 自校准扣除左阴影（Win10 阴影左右各约 7~9px、上边为 0）；
3. **帧尺寸对账防线**：帧尺寸与 DWM 边框偏差超 32px 即丢帧走兜底——杜绝任何未知的错误 attach 把别的窗口画面当成功结果返回（显示诚实红线）。

**兜底 BitBlt（`_bitblt_client_capture`）**：屏幕 DC 按客户区物理坐标直取（`SRCCOPY|CAPTUREBLT` 带分层窗口），窗口须可见，被遮挡部分截不到。PrintWindow 经真机取证对该客户端稳定失败（调用返回 0），不采用；Pillow `ImageGrab` 在混合 DPI 多屏下坐标错位，也不采用（必须自写 GDI）。

**黑帧双指标检测（`is_black_frame`）**：独占全屏模式下客户端窗口出黑帧，判定需灰度均值 < 8.0 **且**近黑像素占比（<16）> 98% 双指标同时命中——取证正常画面均值 90~111、近黑占比 ≤7%，黑帧双指标远离该区间；双条件避免暗色调界面误杀。命中后返回人话引导 `MSG_BLACK_FRAME`（切窗口化：游戏内设置或 Alt+Enter，工具不代为切换）。

**连接与最小化语义**：`connect(validate_capture=True)`（用户触发路径）解析目标 → 校验非最小化 → 启动 WGC → 截一帧过黑帧检测才算成功，失败回滚断开；自动重连等高频路径 `validate_capture=False` 免付一帧。最小化返回 `MSG_MINIMIZED`；客户端失焦停渲时 WGC 返回最后一帧，属画面本身停更而非采集缺陷，识别层结果比对天然兼容，不做新鲜度特判。

**合规边界**：模块 docstring 架构防火墙声明——只读窗口画面，不实现任何输入发送、自动点击、进程注入、hook、反作弊绕过。

### 3.2 模板匹配

模板匹配是 OCR 流程的**前置过滤器**，执行在 OCR 引擎之前：

模板制作时会在 `templates/wujiang_select.json` 保存制作截图的参考尺寸和原始框选坐标（对局攻略模板存于 `templates/match_guide/template.json`）。用户模板缺失时回退随包只读默认模板（`BUNDLE_ROOT/templates/`），旧模板没有坐标元数据时，兼容使用 2560×1440 参考尺寸并保留全屏搜索。

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

**为什么先做模板匹配：** 基础比例局部匹配可快速过滤正常页面；局部不命中或旧模板仍会全屏多尺度回退，保证识别率。任务日志记录 `outcome`、最高置信度、缩放与匹配策略（`base_local` / `base_full` / `fallback_full_multiscale` / `fallback_multiscale` / `coarse_reject_multiscale` / `unmatched`），便于判断是否需要重新制作模板或调整阈值；只有模板命中后才执行昂贵的 OCR 推理。

**分层加速（cd35c98）**：全屏多尺度回退阶段新增粗扫粗筛——先将原图降采样至 1/4 尺寸（`_COARSE_SCAN_RATIO=0.25`），在降采样图上遍历全部候选缩放比例做粗匹配，仅当最优粗扫得分 ≥ `threshold - _COARSE_SCAN_MARGIN` 时才回到原尺寸做全图精扫。非目标页（轮询常态）粗扫即可判否，避免每拍付整幅多尺度扫描的几百毫秒；匹配策略记录 `coarse_reject_multiscale` 区分"粗扫即判否"与"精扫确认不匹配"。局部匹配路径不受此优化影响，仍按原始全尺寸执行。

**人工识别例外：** 用户从页面点击"识别当前阵容"或导入本地图片时会传入 `force_ocr=True`，此类已明确指定识别页类型的请求跳过模板匹配，直接执行 OCR。只有自动轮询仍将模板匹配作为前置门禁，避免对无关游戏画面反复执行 OCR。

对局攻略模板应优先框选左侧常驻功能图标等固定 UI，避开回合数字、角色立绘和战场背景；这类内容会随对局状态变化，不能作为可靠的页面特征。

应用在启动画面阶段即向同一 `OcrWorker` 队列提交预热任务，并在窗口显示前同步等待完成（引擎初始化会长时间持有 Python GIL，若与界面事件循环同时运行会卡住界面），不依赖模拟器连接。预热状态为 `idle`、`warming`、`ready` 或 `failed`，通过 `ocr_warmup_state_changed` 通知 UI；失败后允许重新提交。预热在 worker 线程加载 RapidOCR 主引擎、加载静态字符特征缓存，并以名称拼图的代表尺寸执行一次检测和识别推理（B1 起**预热单路径**——仅画布 det+rec 一次，paddle 时代的横条 rec-only 预热已删除：RapidOCR 适配层签名不接受 `det=`/`rec=` 关键字，且 rec-only 推理在 B1 管线已无消费方）；后续选将推荐和对局攻略识别复用该实例，因此首次实际 OCR 不再承担模型或运行时算子初始化。`engine_loader.create_rapidocr_ocr(suite)` 统一负责引擎构造：显式 `model_path` 绕过联网下载检查（缺文件直接熔断，绝不在线下载），det 参数显式 `limit_type=max`/`limit_side_len=960` 与生产画布同口径。

ADB 截图需要 OCR 时，`CaptureService` 会先复制图像并提交 OCR worker，原始图交给 `ImageSaveScheduler` 的单线程 `image-save` 执行器（审计 G8 后该调度器独立于 CaptureService，见 module_business.md §3.2）压缩 PNG。OCR 完成不等待保存；保存完成通过 `image_saved` 通知。对于仍在写入的 ADB 截图，`capture_completed.save_path` 为 `None`；本地导入则保留其已存在的源文件路径。

自动轮询中，对局攻略仅在选将页命中后才会激活。对局攻略模板未命中时会回退执行一次候选角色 OCR；至少确认 3 个角色名才自动切换页面并停用该任务，`unresolved`、`unknown` 和 `conflict`（`UNRESOLVED_RESOLUTIONS` 词表，b5e8e4f 单源化）不计入数量。模板在此路径中用于加速命中，而非阻断不同战场 UI 的识别。

### 3.2.1 手动识别与轮询冷却（e6d67c0 修复）

`CaptureService` 提供两条截图 → OCR 路径，共享同一 `OcrWorker` 队列但走不同入口：

| 路径 | 入口 | 调用来源 | 冷却处理 |
|------|------|---------|---------|
| 手动识别 | `do_capture()` / `do_capture_from_file()` | 选将推荐/对局攻略"识别当前阵容"按钮、本地图片导入 | 无冷却，每次点击均执行 OCR |
| 自动轮询 | `capture_for_poll()` | `PollCoordinator` 后台定时器 | 由 `OcrService.set_task_cooldown()` 按任务独立记冷却 |

**Bug 根因**：`do_capture()` 原实现中 `should_ocr` 判断包含 `or is_poll` 项，其中 `is_poll` 从 `mumu_ocr_poll_mode` 全局配置读取而非按调用来源判断。该路径只被手动 `do_capture` 触发（轮询走 `capture_for_poll` 不经过此处），因此当轮询开启时，手动识别被误标 `is_poll=True`，命中选将/攻略页面后误写 180 秒冷却（`POLL_MATCH_COOLDOWN_SECONDS` 常量 + `_poll_cooldown_until` 字段）；冷却内再次点击手动识别时 OCR 被跳过、返回空结果。

**修复**：删除 `POLL_MATCH_COOLDOWN_SECONDS` 常量、`_poll_cooldown_until` 字段、冷却跳过分支、`_queue_capture_ocr` 的 `is_poll` 参数与 pending 键；`should_ocr` 同步移除 `or is_poll` 项。手动识别恢复为"每次点击即执行 OCR"的预期行为。

**真实轮询冷却**：由 `OcrService.set_task_cooldown(task_name, seconds)` 承担。`main_window.py` 在轮询命中 `hero_selection` 后调用该方法，时长取 `mumu_hero_selection_cooldown` 配置（默认 180 秒）；冷却中的任务不进入 `due_poll_tasks()`，窗口期内该任务不再匹配、不 OCR。该机制仅作用于自动轮询路径，不影响手动识别。

### 3.3 2v2 巅峰赛卡位检测（card_grid_detector.py）

2v2 巅峰赛牌面（14 张禁选阶段 → 8~11 张候选阶段）不能用固定 8-ROI 模板。改为**内容驱动**的卡位检测：

```
detect_selection_cards(image)
  -> cv2.cvtColor(image, BGR2HSV)
  -> 掩码: S>90 或 V<90（背景低饱和宣纸 ≈ S:8, V:230）
  -> 闭运算核 = max(3, round(h/1440*5))  # 5px 基准，避免上下两行粘连
  -> cv2.connectedComponentsWithStats(mask, 8)
  -> 过滤: 面积 > 0.0055 全图 / 尺寸 [0.086w, 0.115w] × [0.215h, 0.245h] / 宽高比 [0.60, 0.95] / 位置 [0.12w, 0.88w] × [0.16h, 0.67h]
  -> 通过尺寸窗与宽高比 → cards[]；未通过 → suspicious[]（标 "尺寸窗" / "宽高比"）
  -> [确信卡 ≥ REPAIR_ARMED_MIN_CARDS=6] 计算参考几何
     ├── ref_h / ref_w = 确信卡中位高/宽
     └── rows = _split_rows_by_gap(cards)  # 按 y 最大间隙（阈值 100px）分上下两行
     ├── 修复路径：逐条处理 suspicious
     │   ├── 高度在窗内而宽度越窗 → 维持拒绝并 debug 留痕（龙饰横翼；同排两卡被动画填缝焊成的
     │   │     514×326 块也落此支；此前静默丢弃致卡数诈降仍落合法区间，2026-10-12 11→9 事故）
     │   ├── 宽度在窗内且 h ≤ SINGLE_OVERGROWTH_MAX_RATIO=1.35 × ref_h → 近界单卡收纳
     │   ├── 竖向融合块：h ≤ STACKED_SPLIT_MAX_RATIO=2.4 × ref_h 时 _split_stacked_component
     │   │     沿最窄腰线拆两段，各段以 _refit_card_width 重拟合并逐段过尺寸窗
     │   └── 其余 → 维持拒绝
     └── 主路径归一（漂移抑制）：cards 逐张 _normalize_card(mask, box, ref_w, rows)
         ├── _refit_card_width(min_run_ratio=REFIT_TRUSTED_MIN_RATIO=0.8)
         │     按卡体列纵向跨度 ≥ REFIT_SPAN_RATIO=0.6 × 段高定位卡体，宽度取 ref_w、以段中心对齐；
         │     段过短（< 0.8 × ref_w，淡色立绘截短）返回 None 维持原状，避免按段中心反被带偏
         └── 行中位对齐：y/h 取最近行（中位 y 距最相近）的中位 y/h，产出与该连通域多宽无关
  -> 卡数 ∈ CARD_COUNT_RANGE=[8, 14] 返回 _sort_row_major 行优先 bbox；否则 None（对齐 healthy_no_match）

derive_name_rois(cards)
  -> 每张卡内相对位置 [x+0.06w, y+0.15h, 0.30w, 0.38h] 派生名条 ROI

board_row_sizes(cards) -> (上排张数, 下排张数) | None
  -> _split_rows_by_gap；分不出两行（<2 卡或最大间隙 < 100px）返回 None

layout_is_plausible(cards) -> bool
  -> 校验上排张数 == 总张数 // 2（游戏奇数张时上排少一张，故 9 张必 4+5、11 张必 5+6）
  -> board_row_sizes 返回 None 时不据此判负，交由调用方按原语义处理
```

**参考几何与漂移抑制**（8020da8 / 62c5c23）：确信卡的中位尺寸与行划分在修复路径与主路径归一共用，确信卡不足时整体不武装，防非牌面屏幕误播种。主路径产出同样经 `_normalize_card` 归一——贴边动画叠层与出画立绘会把连通域撑得比卡框更宽并向左撑出（2026-10-12 实测 237→254~270、左缘左移 17~21px），逐拍跳变的卡位会同时带偏名条 ROI（读不出字，面板确认数 9↔8 闪烁）与牌面签名（每拍全量重 OCR）。归一以卡体列纵向跨度判定卡体、宽度取 `ref_w`、以连续段中心对齐：对齐只认跨度段，产出与该段所在连通域多宽无关，贴边叠层把 bbox 撑宽不会带偏卡位，重复归一等价；段过短（淡色立绘把卡体列截短至 < 0.8 × `ref_w`，ban_phase 实测 144/238）返回 `None` 维持原状，避免按段中心对齐反被带偏 +29px。

**排布合理性校验（漏检扣帧）**：`layout_is_plausible(cards)` 校验"上排张数 = 总张数 // 2"——游戏奇数张时上排少一张，故 9 张必为 4+5、11 张必为 5+6；上排多于下排说明本拍漏检（2026-10-12 实测 11 张盘面被动画填缝焊掉底排相邻两卡后成 5+4，仍落在 8~14 合法区间而被当作正常 9 张发布）。`PeakSelectWatcher._do_work()` 在 `_restore_missing_cards()` 之后、`board_signature()` 之前调用：不可信排布既不发布也不计入缺席，面板保持上一拍内容并留 info 日志；`board_row_sizes()` 供其日志打印实际排布。分不出两行（低分辨率、单排）时不据此判负。

**宽度越窗静默丢弃修复**（62c5c23）：修复路径中"高度在窗内而宽度越窗"分支此前静默丢弃，卡数诈降后仍落在合法区间（8~14），面板少了卡却无迹可查；现维持拒绝但 `logger.debug` 留痕，方便回溯。

参数均为相对比例（基准 2560×1440 实测），分辨率变化时自适应。仅当布局变化（`board_signature` 不同）时才触发 OCR，坐标/尺寸按 4/8px 量化过滤像素级抖动。

### 3.4 巅峰赛识别循环（peak_select_watcher.py）

`PeakSelectWatcher` 是独立 QObject，与标准轮询并存，负责 2v2 巅峰赛牌面的实时识别循环：

```
Tick 每 1.5s → _thread_lock 非阻塞 → _do_work() 后台线程
  ├─ CaptureService.capture_for_poll(capture)
  ├─ detect_selection_cards(frame) → None → _handle_board_absent()
  │   └─ miss_ticks++ → BOARD_EXIT_TICKS=4 后 _clear_session_state(keep_miss_ticks=True) 并发 board_exited（不写任务状态，衔接激活归 PollCoordinator）
  ├─ _restore_missing_cards(cards)  # 本拍少卡且存活卡逐张原位 → 按上一拍卡位补齐（2026-10-12 11→9 事故）
  ├─ layout_is_plausible(cards)  # 上排张数 != n//2 → 本拍漏检，既不发布也不计入缺席（详见 3.3 节）
  ├─ board_signature(cards) 量化坐标/尺寸
  │   └─ == 上次 → 牌面未变化，沿用结果
  ├─ _suspend_standard_tasks() 挂起 hero_selection/match_guide 轮询
  ├─ _recognize_board() 提交 OcrTask 到 OcrWorker，15s 超时
  └─ _publish_pool() → parse_pool() → PoolSnapshot → pool_updated 信号
```

- **PoolSnapshot**：`card_count / names / pending / stage("ban"/"pick") / overlap / banned`
- **图片导入** `recognize_image_file()` 使用独立 `_import_lock`，不影响循环
- **stage 判定**：≥12 张 = "ban" 禁选阶段；8~11 张 = "pick" 候选阶段
- **人工确认**：`confirm_pending(slot, name)` 由 `parse_pool` 校验候选在白名单内才生效（待确认槽按 `UNRESOLVED_RESOLUTIONS` 过滤）

### 3.5 ROI 布局配置（roi_config.py）

`OcrRoiConfig` 从 `config/ocr_rois.default.json`（随包只读基线，`BUNDLE_ROOT`）加载默认布局，并由 `config/ocr_rois.json`（可写运行时根，`PROJECT_ROOT`）用户覆盖层管理本地调整。两份文件共用 `schema_version: 1`，布局内 `reference_size` 为 `[宽, 高]`，每个 slot 为 `name_roi` + 可选 `team_roi`。`save_layout()` 写盘后立即更新运行时布局，无需再调用 `reload()`；`reset_layout()` 删除该页本地覆盖并回到默认；`reload()` 重新读盘，本地文件缺失页面或校验失败时仅警告并整体回退默认布局。

| 页面类型 | 席位数量 | 阵营 ROI | 说明 |
|---------|---------|---------|------|
| `hero_selection` | 8 | 无 | 标准选将页 8 名武将 |
| `match_guide` | 5 | 必须 | 对局攻略页 5 个席位（含楚/汉阵营标签） |

`OcrRoiLayout` 包含 `reference_size`（参考截图尺寸）和 `slots`（`OcrRoiSlot` 元组），每个 slot 有 `name_roi` 和可选 `team_roi`。`GeneralRecognizer` 在构造时接受 `layout` 参数或按 `page_type` 自动选择布局。加载时会校验页面要求（`hero_selection` 恰好 8 席且无需阵营 ROI，`match_guide` 恰好 5 席且每席必须带阵营 ROI）、ROI 全为整数且不得超出参考尺寸；用户通过 `save_layout` 写入本地覆盖后布局即刻生效，`reset_layout` 恢复默认。

### 3.6 多路证据与候选确认

`GeneralRecognizer.recognize()` 按 `page_type` 选择冻结识别计划后统一走 `_recognize_page(image, plan)` 模板方法（2646fd4，纯等价重构，recognizer.py 519→493 行）：`_PagePlan` 冻结数据类以四个开关注入页型差异——`has_team`（match_guide：识别楚/汉阵营标签槽）、`short_circuit_name_recheck`（match_guide：批次零产出跳过逐槽复核，见下文）、`emit_empty_results`（wujiang：缺槽补 `empty_name_result` 占位行）、`log_details`（wujiang：缩放/ROI 明细/越界/逐槽结果调试日志）；两个模块级单例 `_WUJIANG_SELECT_PLAN` 与 `_MATCH_GUIDE_PLAN`，`_recognize_match_guide()` 保留为两行委托维持既有直测入口。模板骨架：缩放计算 → 逐槽 ROI 缩放裁剪 + 预处理（`plan.has_team` 时附加 team 槽）→ 名称批量识别 → 逐槽证据决议（`requires_slot_recheck` → `_append_single_name_evidence`，受批次短路门控）→ 页面消解 `resolve_page_names` → 复核收尾 `_recheck_unresolved_slots` → `_record_det_stats()`。

骨架内先分别预处理同类 ROI，再横向拼图为一次 OCR 引擎检测。选将页使用一张名称拼图；对局攻略的名称和阵营各使用一张拼图，避免尺寸或方向不同的区域混合。名称槽位记录批量拼图证据（放大灰度 plain 族）；缺失、多候选、冲突或置信度低于 0.75 时，才追加增强图与仅放大原图的逐槽识别（`_NAME_RECHECK_CONFIDENCE` 由 0.8 降至 0.75，1694ab7 按 v6 置信分布定标——v6 在 0.7~0.99 中带占比 17.9%（v4 仅 7.5%），0.75 使回退触发量不因引擎切换放大一个数量级，实测触发率 4.5%→1.7%）。ROI 坐标以参考分辨率保存，
识别前会分别按当前截图宽高进行换算，因此支持页面比例基本不变时的分辨率变化：

```
参考 ROI → 当前截图宽高缩放 → 裁剪 → OCR 引擎
```

换算后的识别流程为：

**第一段：主引擎全量字典识别**

ROI 裁剪 → 放大 3× → 灰度 → 主引擎（v6）

**第二段：候选确认与页面消歧**

```
OCR 引擎 → 文字 + 置信度
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

等长多候选的自动确认要求每路 OCR 置信度 `>= 0.7`、最高错字字形分 `>= 0.35`、与第二名分差 `>= 0.15`，并且 `enhanced` 与 `plain` 两个独立证据族支持同一结果。`batch_plain` 与 `single_plain` 同属 `plain` 族，不能重复计票（`evidence_family()` 按 source 含 `plain`/`enhanced` 子串分族；此前文档写 `batch_enhanced`，与生产代码实际的 `batch_plain` 不符，本轮勘正）。页面唯一性不会提升 `uncertain`，也不会把只有一个但未过字形安全门槛的候选自动提升。

候选确认之前设有**词表外新武将保护**：全部证据族（`enhanced` 与 `plain` 至少两族）以 `>= 0.995` 的置信度一致读出同一词表外原文时，抑制候选内评分决胜，不做自动绑定。存在编辑距离候选时保持 `unresolved` 并保留候选（可能是新武将如"王导"，也可能是生僻字被稳定误读或整字漏识如"王濬"只读出"王"，无法区分，统一走人工确认）；完全无候选时判为 `unknown_new_hero`——保留 `raw_name`、候选清空。该检查在确定性纠错（含 `SAFE_SUBSTITUTION_WHITELIST` 混淆字对，如"苟彧"→荀彧）之后执行，置信度不足的一致读数仍走候选内评分。

拼图检测时额外设有**批处理回退门槛**：拼图结果只接受单候选且置信度 `>= 0.5`；若结果不在武将词表内且按编辑距离筛选不出唯一候选（0 个或多个），则视为截断文本风险，跳过拼图结果直接逐槽复核，避免被多候选纠错静默绑定到错误武将。

**批次零产出短路（6ea1866，行为修复）**：名称批次画布无任何文本行（`any(name_evidence.values())` 为 False）时短路逐槽双证据（`_append_single_name_evidence`）与阵营逐槽重试链——批次画布整幅无名条可检时，逐槽双证据必然同样空检，短路以免每槽 2 次、共 10 次空 det 调用刷空检告警（wujiang_select 历史上无此守卫，恒真维持原状）。阵营同口径（`team_batch_has_text`），但"批次读出字而归一化失败"保留逐槽重试——那是书法体徽记的 v4 异构救回场景，仅批次零产出才允许跳过。效果：错位页面（模板未命中仍放行的兜底 OCR）每轮 det 调用约 14 次 → 4 次，消除非对局页空转期间刷出的空检告警并缩短单轮识别耗时；真对局页识别行为不变。

结构化结果为 `{index, raw_name, name, candidates, resolution, length_mode, confidence, evidence}`。`name` 只保存已确认名称；`length_mode` 为 `complete`、`missing`、`uncertain` 或 `unknown`；`resolution` 包含 `exact`、`unique_prefix`、`unique_similarity`、`multi_similarity`、`slot_unique`、`manual`、`unresolved`、`unknown`、`unknown_new_hero` 和 `conflict`。官方榜单仍使用独立的整榜解析与写入门禁，本节不抽取两条链路的共用解析器。

**未决槽位复核（B1 起复核引擎为 v4）**：页面消解后仍未决（`resolution ∈ ACTIONABLE_UNRESOLVED_RESOLUTIONS`，即 {unresolved, conflict}）且候选闭包非空的槽位，由 `_recheck_unresolved_slots()` 调用复核引擎（`get_recheck_ocr_engine()`，当前为 RapidOCR/PP-OCRv4-mobile/ONNX，与主引擎互为异构；`MUMU_OCR_PRIMARY_ENGINE=v4` 回滚档时两套件角色互换）补充证据。喂法与生产同构（3× 灰度条、30px 间隙、960 分组画布），但接受纪律严格：读数必须精确命中该槽候选闭包内的成员才作为 `source="recheck"` 证据注入并重跑消解；不命中一律维持原状，绝不引入新名字。页面唯一性已确认的名字不可再被复核绑定，避免同页重名被复核坐实。开关 `MUMU_OCR_RECHECK_ENABLED`（代码默认 `False`，config.env.example 亦为 false），引擎加载失败自动熔断停用，不影响主流程。另有一处复核触发点：对局攻略 team 徽记归一化失败（含批量画布读出非空乱码的情形）时，按 `主引擎 → 复核引擎` 链式单条重读，读出楚/汉即停——1694ab7 修复后 119 图重放救回 15/18 个 v6 书法体徽记丢标签槽，team 覆盖率恢复 90% 基线。

**消解状态词表单源（b5e8e4f，纯等价重构）**：`src/ocr/name_resolution.py` 三个公共常量收口此前各处手抄副本——`CONFIRMED_RESOLUTIONS = frozenset({"exact", "unique_prefix", "unique_similarity", "multi_similarity", "slot_unique", "manual"})`（原私有 `_CONFIRMED_RESOLUTIONS` 转公共）、`UNRESOLVED_RESOLUTIONS = frozenset({"unresolved", "unknown", "conflict"})`（未定全集）、`ACTIONABLE_UNRESOLVED_RESOLUTIONS = frozenset({"unresolved", "conflict"})`（收窄集）。收窄属有意：`unknown` 拒识无读数无候选，没有可供下一步（复核注入 / 人工确认 / 待确认展示）消费的材料，各调用处另有守卫兜底。三个消费点改引常量：`ocr_worker` 待确认统计（CONFIRMED）、`peak_select_watcher.parse_pool` 人工确认过滤（UNRESOLVED，删本地 `_CONFIRM_RESOLUTIONS`）、`recognizer._recheck_unresolved_slots` 复核注入（ACTIONABLE）；`NameResolver.requires_slot_recheck` 与 `resolve_page_names` 同步改引。

名称 ROI 内的卡框和底部定位字会污染像素行分割，边缘槽位也不稳定，因此当前不把视觉字符数作为硬门禁。势力关联可在后续作为附加证据，但只能过滤当前候选白名单，不能引入白名单外名称；本次未接入该逻辑。

官方榜单导入不使用页面模板匹配或 `GeneralRecognizer` 的页面识别流程，但会以一个 `OfficialImportTask` 进入通用 `OcrWorker` 队列，并复用 worker 持有的 OCR 引擎。`src.ocr.official_board_parser` 提供旧版长图、新版分页版式识别、20261022 拼接长图切分（`split_stitched_image`）、面板切分、数据行恢复、单元格切分和胜率数字模板算法。`src.business.recognition.official_data_import_service` 在固定版式下对单元格逐格识别（引擎注入时数字/名称格先走 2× 单候选快通道，质量门未过的困难格回退 4× 双候选），负责识别编排、面板守卫与正式写入门禁；罕见字兜底的引擎策略在 `official_ocr_engines.OfficialOcrEngines`——B1 起 `main` 与识别管线同源（`get_primary_ocr_engine()`，v6），`rare_char` 只取复核引擎（v4）在 `allowed_names` 候选闭包内复核，**chinese_cht 繁体引擎兜底链整体退役**（开关关闭或引擎不可用 → `rare_char_failed=True` 保留原结果走待复核，不再加载第二识别引擎）；整榜唯一性补全与名称纠错规则在 `name_resolution.HeroNameResolver`；常规页面识别不复用整榜缺失集合。两条链路共享 OCR 串行资源，但候选规则暂不抽取为公共解析器。

### 3.7 候选内单字字形评分

常规截图只对"与候选等长且恰好一个字符不同"的名称评分。缺字前缀和其他增删字结果只用于建立候选白名单，不参与字形决胜。对每个合法候选，仅计算那个不同字符的加权相似度：

| 维度 | 权重 | 说明 |
|------|------|------|
| 四角号码 | 30% | 前四个有效数字按位置等权比较，匹配数除以 4 |
| 仓颉码 | 30% | `1 - Levenshtein / 较长码长度` |
| 五笔 86 全码 | 40% | `1 - Levenshtein / 较长码长度`；码缺失记 0 分 |

> 权重选型依据与完整评测数据见 `docs/design/character_similarity_design.md`。另有确定性白名单 `SAFE_SUBSTITUTION_WHITELIST`：命中"错字 → 正字"映射的等长单字替换直接视为安全（相似度按 1.0 处理），用于兜底多维相似度不足但 OCR 高频的错对。**B1 后基线为 10 对**（昧→眜、半→芈、丰→羊、口→吕、好→妤、邻→郃、赢→嬴、苟→荀、早→卓、哈→哙）：paddle 时代的 5 对旧对因 v6 直读修复退役，新增 v6 系统性错法 10 对后又退役简繁变体对 瓚→瓒 / 黃→黄（视觉字形评分 0.94 / 0.725 均远超 0.55 安全线，视觉相似路径已自动矫正，白名单回归"形近临界对"本职；简繁混淆本质是形近混淆的子集）。用户层白名单存 `data/ocr_confusion_overrides.json`，随版本退役的条目（如 珍→玠）需在白名单配置界面手动删除。

评分公式：
```python
if len(text) == len(candidate) and mismatch_count == 1:
    score = four_corner * 0.3 + cangjie * 0.3 + wubi * 0.4
else:
    score = None                        # 不参与常规截图的候选决胜
```

唯一候选使用 0.55 安全门槛；多候选使用 0.35 绝对门槛和 0.15 领先门槛，并要求两个独立证据族一致。评分只负责在合法候选中排序，不能改变字数门禁产生的候选集合。

任一字符的某项特征缺失时，该维度贡献 0 分；四角码不足四位不补零；五笔码缺失（不在离线码表内）时该维度记 0 分。

### 3.8 汉字特征缓存

汉字特征数据采用三层策略：

| 层 | 速度 | 覆盖 |
|----|------|------|
| `char_info_cache.json`（437 字） | ~10ms | 当前武将名全部字符 + 常见 OCR 误识字 |
| 运行时原始库（按需补齐） | ~1060ms | 任意汉字（理论兜底） |

`CharacterFeatureRepository` 默认读取 `src/data/char_info_cache.json`（随包只读基线），也可在构造时注入其他路径。静态缓存覆盖当前英雄名的全部字符；运行 `src/scripts/build_character_feature_cache.py` 可在 `heroes.json` 更新后补齐并以 UTF-8/LF 原子写入。缓存未命中的汉字仍由 unihan-etl / cnradical / pypinyin 按需补齐到进程内存；已有 `Options.destination` CSV 时直接复用，只有文件不存在时才调用 `Packager.export()`。pypinyin 失败会记录一次 warning 并禁用后续拼音查询，cnradical 单字失败会记录具体字符；两者均降级为空特征而不中断 OCR。五笔 86 全码来自离线码表 `src/data/wubi86.txt`，笔画数来自 UNIHAN `Unihan_IRGSources.txt`；码表缺失时对应维度按 0 分处理，不阻断识别。

基线缓存已补齐「存祖逖」等 29 字（a1f5ff0、2583571）及后续官网武将同步新增字符（74234a8），并在 6b21d75 回填运行时动态补齐的条目，总计 437 字，修复 CI 武将名词表覆盖断言。

`character_feature_repository.py` 模块头登记该文件为随包只读基线（`BUNDLE_ROOT/src/data`，不在首启部署清单）：`save()` 仅开发机脚本 `build_character_feature_cache.py` 调用（重建基线走 git/pull_data 流程），frozen 下无 UI/-m 触发链属理论债；将来维护页接入时勿简单换 `PROJECT_ROOT`——基线不在部署清单，直接换根会因文件不存在而读空识别特征，正确形态是部署 `src/data` 或读写根分离。

用户层缓存 `data/char_info_cache.json` 与基线缓存合并：运行时动态补齐的特征写入用户层，基线缓存保持只读（随包分发）。用户层格式异常时仅警告并忽略，不影响基线功能。

### 3.9 OCR 引擎装载与推理配置（engine_loader.py，B1 起）

`src/ocr/engine_loader.py`（1694ab7 替代 `paddle_loader.py`）是 RapidOCR/ONNX 双套件的唯一装载层：

**套件定义**：

| 角色 | 套件 | 模型文件（rapidocr 3.9.2 wheel 内 `models/` 目录） | 字典 | 来源 |
|---|---|---|---|---|
| 主引擎（默认） | v6 | `PP-OCRv6_det_small.onnx` + `ch_ppocr_mobile_v2.0_cls_mobile.onnx` + `PP-OCRv6_rec_small.onnx` | rec 模型内置 | wheel 内置 |
| 复核引擎（默认） | v4 | `ch_PP-OCRv4_det_mobile.onnx` + 同一 cls + `ch_PP-OCRv4_rec_mobile.onnx` | 外挂 `ppocr_keys_v1.txt` | `fetch_recheck_models.py` 预取（URL+SHA256 钉死） |

- **`primary_suite()`** — 读 `MUMU_OCR_PRIMARY_ENGINE`（默认 `"v6"`），非法值告警并回退；`"v4"` 为回滚档，此时主复核**角色互换**（v4 主 + v6 复核）。
- **`create_rapidocr_ocr(suite="v6")`** — 构造指定套件引擎：模型 SHA256 漂移哨兵（与套件内钉死基线比对，不符仅 warning 提示"上游 wheel/模型漂移，行为需复测"）；成功日志落模型指纹（哈希前 8 位 + rapidocr 版本）；`Det.limit_type="max"` + `Det.limit_side_len=960` 显式写死与生产画布同口径；`Det/Cls/Rec.model_path` 显式指向模型文件，**绕过 rapidocr 在线下载检查，缺文件直接报错熔断（离线纪律）**；`EngineConfig.onnxruntime.intra_op_num_threads` 钉 `MUMU_OCR_CPU_THREADS`（默认 6），防推理吃满核心与模拟器抢核；v4 套件额外加 `Rec.rec_keys_path`。设备固定 CPU（GPU 已否决），不配置任何 CUDA/DirectML EP。
- **`get_primary_ocr_engine()` / `get_recheck_ocr_engine()`** — 套件级惰性单例；复核套件与主引擎互斥（`"v4" if primary_suite()=="v6" else "v6"`）。`_get_shared_engine` 持锁缓存，**任何异常加入 `_FAILED_SUITES` 熔断**：此后直到进程重启恒返回 `None`（不再重试）。主引擎 `None` 意味识别停摆，由调用方按各自语义熔断（recognizer 抛 `RuntimeError`、官方导入停复核）。
- **`RapidOcrEngine` 包装类** — `ocr(img, cls=False)` 签名（与 paddleocr 2.x 消费约定一致，`cls` 为兼容位）；灰度图自动转 HWC 三通道；防御性读取 `txts/boxes/scores`（scores 缺失补 0.0）；结果翻译为 `[[box, (text, conf)], ...]` 且 **box 必须 `tolist()` 转纯 Python list**（下游按 `isinstance(line[0], (list, tuple))` 判别行格式，np 数组会打穿该判别）；空结果返回 `[None]`。内置 det 调用与空检计数（6ea1866）：`reset_call_stats()` 清零（识别任务开头对齐统计窗口）、`drain_call_stats()` 返回 `(det 调用数, 空检数)` 并清零（识别收尾聚合输出）。
- **空检告警聚合（6ea1866）**：rapidocr 检测零框时逐次抛 `The text detection result is empty` WARNING，兜底 OCR 在非对局页整链空转时每轮刷十几条且单条毫无诊断价值；`_EmptyDetFilter` 在 `create_rapidocr_ocr` 构造引擎时幂等挂载到 `RapidOCR` logger，只滤该条告警，其余 rapidocr 告警不受影响。逐条告警的聚合出口：`GeneralRecognizer` 识别开头 `_reset_det_stats()` 清零统计窗口、收尾 `_record_det_stats()` 把 `(det 调用数, 空检数)` 并入 `timing_ms`（duck-typing 兼容无统计引擎），经 OCR 阶段耗时日志新增的 `空检=空检数/调用数` 摘要字段输出（无统计时显示 `-/-`），不新增日志行。
- **打包态模型路径**：frozen 下模型复制到 `%TEMP%\mjs_rapidocr_models`（规避打包路径含中文风险；`%TEMP%` 非纯 ASCII 回退打包路径）；同步指纹为全部 6 个模型文件的 `名字:st_size:st_mtime_ns` 拼接，写入 `.synced` 标记，**指纹不一致即 rmtree 整体重拷**（源模型更新后自动重拷）。两套件共享同一副本目录。

**recognizer 侧二级熔断**：`GeneralRecognizer._engine` 加载失败时写入熔断标记（`self._ocr = False`），后续识别立即快速失败并提示"重启应用后可重试"；loader 返回 `None` 时显式 `raise RuntimeError("OCR 主引擎不可用（rapidocr/onnxruntime 或模型缺失，详见日志）")`，不再对每次识别重复尝试加载。


> **78fd65c 死代码清理：** `CaptureService.run_ocr_if_matched()` / `OcrService.run_ocr()` 同步等待路径（30 秒有限等待）已移除，生产识别全部走异步 `submit_ocr_task()`。

**OCR 任务模板控制**：`OcrTask` 中 `match_template=True` 时执行模板匹配前置过滤；`match_template=False` 时跳过模板匹配直接 OCR（巅峰赛卡位检测路径通过 `submit_ocr_task(match_template=False)` 使用）。模板未命中时，`fallback_on_template_miss=True` 可强制回退执行 OCR（对局攻略路径使用），否则返回 `healthy_no_match`。

**v4 复核模型预取（fetch_recheck_models.py，1694ab7 新增）**：rapidocr 3.9.2 wheel 只内置 v6 三件套，v4 的 det/rec/字典由 `python -m src.scripts.fetch_recheck_models` 从 RapidOCR 官方 modelscope 分发下载到 rapidocr 包 `models/` 目录（URL+SHA256 钉死、幂等：已就位且哈希匹配即跳过、流式写 `.part` 校验后 `.replace` 原子替换）。`verify_v4_models()` 返回问题清单，被 `--check`、doctor、mjs_agent.spec、release.py 四处复用做前置校验。**运行时离线纪律不变**：引擎显式 model_path、缺文件熔断、绝不在线下载；本脚本只在安装/构建时人工运行一次（需一次网络）。

**白名单静态冲突检查**：`find_whitelist_conflicts()`（已迁至 `business/recognition/name_resolution.py`，原 `character_similarity.py`）枚举词表中"等长仅差一字、且该差异对在白名单内"的高危武将名对——这类名对意味着白名单会在两个真实名字之间单方面拉边（误绑风险），供新增白名单对或新武将入库时做常驻检查。`character_similarity.py` 另新增 `CharacterSimilarityService.effective_whitelist` property（基线 + 用户层合并拷贝）与模块级 `whitelist_conflicts_with_roster(pairs, hero_names)`（1694ab7）——**武将库扩充后重检存量对**的自动化谓词：①错字出现在任何武将名中（替换可能破坏该名合法读数）；②正字不在任何武将名中（当前词表下不可能生效，疑似配置错误）。消费方为 doctor「白名单」检查组与 `tests/test_whitelist_conflicts.py` 常驻重检。拼图画布同步按检测器工作尺度（960）分块修复（3f8f30b），避免超宽画布被检测器强制降采样后行级检测退化。

### 3.10 官方榜单固定版式解析（official_board_parser.py）

榜单图片按**固定版式常量**解析，不做通用表格识别。`LAYOUTS` 内置三种榜单：`2v2` 与 `peak` 为双栏（胜率榜 + 出场榜，列 `排名/武将/胜率` 与 `排名/武将`），`exile` 为放逐榜双栏（仅 `排名/武将`）。每个版式记录 `top/bottom` 纵向比例、`panel_ranges` 横向分栏比例、`columns`、`column_breaks` 列分界比例、`header_lines` 表头行数，以及输出与待复核 CSV 文件名。`PAGED_LAYOUTS` 是同一批版式的**新版分页变体**（`variant="paged"`，`top_reference="width"` 即以宽度为基准定位顶部，`separator_mode="between_rows"`）。2026-10-22 起官方改为**拼接长图**（新版头部大图 + 前三名立绘卡 + 统计条 + 图例 + N 段分页表格整页首尾拼接）：导入服务在 `detect_layout` 之前先经 `split_stitched_image` 切成补边虚拟分页，逐段走分页链路，variant 仍为 `paged`，旧版长图与分页截图的既有流程不受影响。

```
detect_layout(image, key)
  └─ 纵横比 height/width >= 4 → 旧版长图优先，否则新版分页优先
     └─ 逐候选 extract_panels → find_data_boundaries → 行数校验
        ├─ 2v2 / peak：左右栏行数必须一致
        └─ exile：左栏 >= EXILE_FULL_PANEL_MIN_ROWS(10) 且右栏不超左栏
        两者都失败 → 抛 ValueError（携带各候选失败原因）

find_data_boundaries(panel, image_height, layout, panel_index)
  ├─ bounded（旧版）：Canny(40,120) → HoughLinesP(threshold=80,
  │     minLineLength=面板宽/3, maxLineGap=12) → 仅保留近水平线段
  │     → 按 y 聚类（间距 <= 6 合并）→ 按行高区间 [0.002h, 0.011h] 分 run
  │     → 取最长 run，截掉表头行数
  └─ between_rows（分页）：排名列白色像素行投影 → 行带中心
        → 直连行高区间 [0.075w, 0.125w] 取中位数行高
        → 按行高倍数插值恢复漏行并向前补齐 → 边界取相邻中心中点
restore_missing_boundaries(boundaries) → 按中位行高补回 Hough 漏检横线
      → 返回 (完整边界, 被修复的排名集合)

split_stitched_image(image, key) → 20261022 拼接长图切分为虚拟分页
  ├─ 逐行灰度 std 找全宽静默带（std < 8 且 >= 40px；旧版行隙仅 15~19px
  │  不会命中），带间内容块为候选段
  ├─ 每段顶部/底部平铺相邻静默带行补边距（复现真实页"表体在页起点
  │  +216px"的几何，偏移过大会被 top 25% 滤波丢行或外推出幻影行）
  ├─ 按 PAGED_LAYOUTS 双栏结构 + 每栏 >= STITCHED_SEGMENT_MIN_ROWS(30)
  │  校验（头部图例区实测 14 行/栏、行距 44~46px，与表格段同构，只能靠
  │  行数门槛区分；真实表格段最少 41 行/栏）
  └─ 有效段 >= 2 才判定为拼接图，返回 [(补边段图, 段 y 偏移)]，否则返回空
```

`split_row_cells(row, columns, column_breaks)` 按列比例切单元格，胜率列左内缩取 `-4` 像素（首位数字紧贴分隔线，通用内缩会截断"4"的左半边）。胜率用**当前榜单自带字体**建立数字模板：`build_rank_digit_templates()` 以视觉行序已知的排名格取样，`prepare_rate_templates()` 再用胜率小数位补样本并预计算整列 OCR，`recognize_rate_with_templates()` 经 `segment_glyphs()`（亮列连通切分）→ `normalize_glyph()`（等比缩放居中到 40×28 画布）→ `match_digit()`（Dice 分数）逐位匹配，单字需 `>= 0.72`，拼成 `xx.xx%`。该模块只负责图像解析与数字模板，候选词表约束与写入门禁由官方导入服务另行负责。

---

## 四、关键代码片段

### 4.1 设备探测（emulator_discovery/ 包，多品牌 2026-10）

统一入口 `resolve_adb_path(emulator_type)`：PATH 优先，其次所选品牌安装目录自带 adb（`pc_client` 无 ADB 语义短路返回空串）；
`discover_devices_with_status(emulator_type)` 按注册表 `EMULATOR_DISCOVERY_MODULES` 分发到品牌模块，统一契约 `discover_devices(retries) -> (list[EmulatorDeviceInfo], error)` 与 `find_adb() -> str`（不引入 Protocol，签名约束由注册表注释与单测锚定）。`normalize_emulator_type()` 对未知/空值回退默认品牌 `mumu`，杜绝 KeyError 扩散到配置与 UI。五品牌探测策略（均为 2026-10-24/25 真机实测标定）：

| 品牌 | 主路径 | 端口来源 | 兜底 |
|------|--------|---------|------|
| MuMu 12 | `MuMuManager.exe info --vmindex all` 解析 JSON（0.2s 重试一次） | console 输出 `adb_port` | 端口段扫描 16384+32n |
| 雷电 9 | `ldconsole list2`（七列，不含端口） | 官方默认段 5555+2n 推导 | 端口段扫描 |
| 夜神 | `NoxConsole list`（七列：index,name,标题,句柄,句柄,PID,VM PID；无运行标志列，运行态由 PID/VM PID 非 0 推断） | `netstat -ano` 把 127.0.0.1 LISTENING 端口与 VM PID 精确关联（用户自定义端口也能命中），无法关联时按 62001+24n 推导 | 端口段扫描 |
| 逍遥 | `.memu` 配置直读（`MEmuHyperv VMs\<实例>\<实例>.memu` 的 `Forwarding name="ADB"` hostport，带 xmlns 命名空间按本地名匹配） | 配置文件真实端口 | 端口段扫描 21503+10n（console 为 GUI 无 stdout，调用属永远超时的死路径，已移除） |
| 蓝叠 5 | 注册表 `Guests\<实例>\BstAdbPort`（国际版契约；产品键 nxt/nxt_cn/x 三者 × HKLM/HKCU × WOW6432Node 逐一尝试） | 注册表端口，socket 试连判定运行态 | `bluestacks.conf` 解析（国内版 5.22 注册表无 Guests 子键；`status.adb_port` 优先于配置 `adb_port`；`bst.enable_adb_access=0` 时给出开启 ADB 的明确指引）。端口动态分配，无固定段，**不设**端口扫描兜底 |

各品牌安装路径定位统一三级：环境变量（`MUMU_HOME` 等）→ **运行进程反查**（`find_process_dir`，自定义/便携安装无注册表时的兜底）→ 注册表 → 常见安装路径。`port_scan.discover_devices_via_ports()` 只做 127.0.0.1 端口带 socket 试连并标注"品牌未验证"（雷电与蓝叠默认端口同为 5555，凭端口猜品牌必然误标）；`supplement_when_empty()` 在 console 枚举成功但零实例时以扫描结果补充（特定版本 console list 返回成功但输出为空，逍遥真机实测）。探测性能（真机计时）：逍遥 10.5s → 0.08s、雷电/夜神 3.8s → 0.6s（进程反查重构为 tasklist 门控 ~0.25s + PowerShell 合并查询，未运行不再冷启动白付 ~3.7s；端口扫描 8 并发，防御回环被防火墙静默丢弃的环境），五品牌全部 <1s。

```python
def find_adb() -> str:
    # 1. 先查系统 PATH
    if shutil.which("adb"):
        return shutil.which("adb")
    # 2. 再查 MuMu 安装根（MUMU_HOME → 注册表 → 8 个常见安装路径）
    for root in _get_mumu_candidates():
        for sub in ("nx_main/adb.exe", "emulator/nemu/EmulatorShell/adb.exe"):
            if (root / sub).exists():
                return str((root / sub).resolve())
    # 3. 最后查旧版候选路径
    for root in _get_legacy_candidates():
        ...
    return ""
```

> **设计思路：** 三个优先级覆盖了大多数场景：系统 PATH 最快，注册表/环境变量次之，常见安装路径兜底。函数式设计无内部状态，可被多处调用而不互相影响。注册表同时读取 `SOFTWARE\Netease\MuMuPlayer12` 与 `SOFTWARE\WOW6432Node\Netease\MuMuPlayer12`（兼容 64 位进程读 32 位安装）。

`mumu.discover_devices()`（配置页经统一入口 `discover_devices_with_status(emulator_type)` 调用）：它执行 `MuMuManager.exe info --vmindex all` 并解析 JSON，在 `MuMuManager.exe` 非零退出、JSON 解析失败或超时时等待 0.2 秒重试一次，返回 `(devices, error)`。空设备列表且 `error` 为空表示正常枚举但没有实例；`error` 非空表示探测失败，UI 必须保留上次成功的列表而非清空当前选择。`discover_running_devices()` 在此基础上只保留 `is_running` 且 `adb_port > 0` 的实例，供 `AdbCapture.connect()` 在无显式目标时自动选定唯一实例（按 `emulator_type` 探测）。

### 4.2 图像预处理流水线

```python
def preprocess_roi(roi: np.ndarray) -> np.ndarray:
    """放大并转为灰度，供批量拼图画布与常规识别使用。"""
    enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)

def preprocess_roi_enhanced(roi: np.ndarray) -> np.ndarray:
    """gamma 提亮的差异视图，仅供逐槽回退的第二证据票使用。"""
    enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    lut = ((np.arange(256) / 255.0) ** 0.7 * 255).astype(np.uint8)
    return cv2.cvtColor(cv2.LUT(enlarged, lut), cv2.COLOR_BGR2GRAY)
```

> **设计思路：** 2560×1440 基准下，默认武将名称 ROI 为 50×145px。额外的 5px 高度用于给竖排名称留出上下缓冲，降低字符被截断的概率。放大使字符像素更密集；灰度化是 OCR 引擎的期望输入。09c904d 事故（4 字武将名被 CLAHE + 锐化 + 拼图画布检测缩放叠加拆成两框）后，主路径去掉了 CLAHE 与锐化——局部对比度增强会加深字间灰度谷，与画布检测缩放叠加会切断文本行，且深度 OCR 模型对光照自带鲁棒性。逐槽回退的第二证据票改用 gamma 0.7 提亮的差异视图：与 plain 的差异集中在暗区（拉开暗横幅上被压扁的细节），属全局色调映射不碰空间结构，只作用于单条竖条（长边 435 < 检测器工作尺度 960，永不触发降采样），不喂批量拼图画布。

识别日志会按槽位记录缩放后的 ROI 坐标，以及 OCR 引擎返回的原始文本和置信度（B1 后为 RapidOCR v6/v4 双套件，paddle 时代指代已经 683fca6 清理）。每个任务完成后，`OcrWorker._execute` 以 `stage_timing: dict[str, float | str]` 字典逐段累积各阶段耗时/标量（b5e8e4f，纯等价重构，ocr_worker.py 551→518 行；键：`template_load_ms` / `template_match_ms` / `template_confidence` / `template_scale` / `template_strategy`（未走模板匹配时初值 `"not_run"`）/ `recognition_ms` / `result_save_ms`），5 处 `_log_timing` 调用点统一以 `**stage_timing` 展开；`_log_timing` 签名保持，形参默认值与字典初值逐项一致，日志输出逐字节不变。耗时之外，识别收尾把主引擎 `(det 调用数, 空检数)` 以 `空检=空检数/调用数` 摘要并入同一行（6ea1866，无统计的引擎显示 `-/-`）。格式类似：

```text
武将 6 OCR ROI: x=1615, y=370, w=50, h=145 (参考 ROI=[1615, 370, 50, 145])
武将 6 name OCR 原始结果: text='祝融夫', confidence=0.9980
```

---

## 五、接口说明

### Capture 层公共方法

| 类/函数 | 说明 |
|---------|------|
| `AdbCapture(adb_path, adb_port=0, screenshot_mode="auto", emulator_type="mumu")` | 构造 ADB 截图器（品牌无关；`adb_port=0` 连接时自动探测唯一运行实例；`screenshot_mode`: auto/raw/png） |
| `AdbCapture.connect(validate_capture=False)` → `(bool, str)` | 连接模拟器并校验目标设备状态为 `device`；5555 端口未注册时双查 `emulator-<端口-1>` 序列号（adb 主动扫描区命名形态）；`validate_capture=True`（用户触发路径）连通后截帧校验，失败回滚断开 |
| `AdbCapture.check_device()` → `(bool, str)` | 复用连接前确认目标设备仍在线 |
| `AdbCapture.disconnect()` → `(bool, str)` | 断开模拟器 |
| `AdbCapture.screencap_full(log_success=True)` → `(bool, Image\|str)` | 全屏截图（raw 帧优先失败回退 PNG，关键字参数轮询传 `False` 抑制成功日志） |
| `PcClientCapture(window_title="名将杀")` | 构造 PC 客户端窗口采集器（接口对齐 AdbCapture，WGC 主路径 + BitBlt 兜底） |
| `PcClientCapture.set_target_window(hwnd)` | 连接前指定目标窗口（多实例时由 UI 用户选择） |
| `probe_pc_windows()` → `list[PcWindowInfo]` | 枚举客户端窗口候选（mjs.exe + UnityWndClass 双条件）；`PcWindowInfo.label` 为设备下拉展示文案（标题+客户区分辨率） |
| `is_black_frame(image)` → `bool` | 黑帧双指标判定（灰度均值 <8.0 且近黑占比 >98%） |
| `ensure_dpi_awareness()` | 进程级 Per-Monitor-V2 DPI 感知幂等声明（坐标物理化的硬前置） |
| `resolve_adb_path(emulator_type)` → `str` | 探测 ADB 路径（PATH 优先，其次品牌自带 adb；pc_client 返回空串） |
| `discover_all_devices(emulator_type)` / `discover_devices_with_status(emulator_type, retries)` | 列出所选品牌实例；状态化版本区分"无实例"与"探测失败" |
| `discover_running_devices(emulator_type)` → `list[EmulatorDeviceInfo]` | 仅返回运行中且端口有效的实例 |
| `EMULATOR_TYPES` / `EMULATOR_BRANDS` / `EMULATOR_LABELS` / `EMULATOR_HINTS` | 连接方式注册表：五品牌 + `pc_client` 全集、品牌子集、展示名与特性提示（设备/连接页 ⓘ 悬停展示） |
| `normalize_emulator_type(value)` → `str` | 未知/空连接方式回退默认品牌，杜绝 KeyError 扩散 |
| `test_adb_path(adb_path)` → `(bool, str)` | 校验 adb.exe 可执行并返回版本行 |
| `load_local_image(path)` / `load_png_image_bytes(data)` → `Image` | 不可信图片输入的格式、体积、像素校验 |

（`pil_to_qpixmap` 展示转换已迁至 `ui/shared/image_utils.py`。）

### OCR 层公共方法

| 类/方法 | 说明 |
|---------|------|
| `TemplateManager.match(image, threshold=0.8)` → `(bool, float)` | 模板匹配（基础比例局部匹配 + 多尺度回退） |
| `TemplateManager.set_template(image, roi)` | 制作模板并保存参考尺寸与框选坐标元数据 |
| `TemplateManager.reload()` / `delete_template()` | 重新加载 / 删除模板与元数据 |
| `GeneralRecognizer.recognize(image)` → `list[dict]` | 识别页面名称并返回候选、状态和多路证据 |
| `GeneralRecognizer.warmup()` / `warmup_inference()` | 预热引擎与字符特征 / 执行一次代表性拼图推理 |
| `GeneralRecognizer.adopt_engine(engine)` / `shared_engine()` | 与同进程其它识别器共享同一 OCR 引擎实例 |
| `ImagePreprocessor.preprocess_roi(roi)` → `np.ndarray` | 放大 3× + 灰度（主路径） |
| `ImagePreprocessor.preprocess_roi_enhanced(roi)` → `np.ndarray` | 放大 3× + gamma 提亮 + 灰度（逐槽回退第二证据票） |
| `engine_loader.primary_suite()` → `str` | 读 `MUMU_OCR_PRIMARY_ENGINE`（默认 `"v6"`）确定主引擎套件，非法值告警回退 |
| `engine_loader.create_rapidocr_ocr(suite="v6")` → `RapidOcrEngine` | 构造指定套件引擎（v6 主 / v4 复核，回滚档角色互换），设备固定 CPU、线程钉 `MUMU_OCR_CPU_THREADS`、det `max/960` 与生产画布同口径、SHA256 漂移哨兵 |
| `engine_loader.get_primary_ocr_engine()` → `RapidOcrEngine\|None` | 主引擎套件级惰性单例；不可用返回 `None`（调用方自行熔断） |
| `engine_loader.get_recheck_ocr_engine()` → `RapidOcrEngine\|None` | 复核引擎惰性单例（与主引擎套件互斥），受 `MUMU_OCR_RECHECK_ENABLED` 开关控制；失败熔断（`_FAILED_SUITES`） |
| `engine_loader.RapidOcrEngine` | RapidOCR 包装层，`ocr(img, cls=False)` 契约，将结果翻译为 paddleocr 2.x 风格 `[[box, (text, conf)], ...]`（box 必须 tolist）；`reset_call_stats()` / `drain_call_stats()` det 调用与空检计数（读取即清零，6ea1866） |
| `OcrRoiConfig.layout_for(page_type)` / `save_layout(...)` / `reset_layout(...)` / `reload()` | 布局读取、本地覆盖写盘（立即生效）、恢复默认、重新读盘 |
| `card_grid_detector.detect_selection_cards(image)` → `list[Roi]\|None` | 2v2 巅峰赛牌面内容驱动卡位检测；非牌面或卡数不在 `[8,14]` 返回 `None`（对齐轮询 `healthy_no_match`）；确信卡 ≥6 时进入修复路径（近界单卡收纳 / 竖向融合块拆分）并把主路径产出归一到参考几何（漂移抑制） |
| `card_grid_detector.derive_name_rois(cards)` → `list[Roi]` | 按卡内相对比例派生各卡竖排名条 ROI，顺序与输入卡位一致 |
| `card_grid_detector.board_row_sizes(cards)` → `(int, int)\|None` | 返回牌面（上排张数, 下排张数）；分不出两排（<2 卡或 y 最大间隙 < 100px）返回 `None` |
| `card_grid_detector.layout_is_plausible(cards)` → `bool` | 卡数 n 的排布是否为游戏可能出现的形状（上排 `n//2`、下排 `n-n//2`）；不可信排布说明本拍漏检，调用方应跳过不发布；分不出两排时不据此判负 |
| `official_board_parser.detect_layout(image, key)` | 按纵横比与行数校验确认旧版/分页版式 |
| `official_board_parser.split_stitched_image(image, key)` | 把 20261022 拼接长图按静默带切成补边虚拟分页（有效段 ≥2 才判定拼接图） |
| `official_board_parser.extract_panels(image, layout)` | 按版式比例切出榜单面板 |
| `official_board_parser.find_data_boundaries(...)` → `list[int]` | 检测官方榜单数据行边界（横线检测或排名列行投影） |
| `official_board_parser.restore_missing_boundaries(...)` | 按中位行高补回漏检横线并返回被修复排名 |
| `official_board_parser.split_row_cells(...)` → `dict[str, np.ndarray]` | 按官方版式切分行单元格 |
| `official_board_parser.prepare_rate_templates(...)` | 构建榜单数字模板并预计算胜率 OCR |
| `official_board_parser.recognize_rate_with_templates(...)` | 用数字模板以 Dice 分数还原 `xx.xx%` |
| `CharacterSimilarityService.correct_hero_name(text, hero_names)` → `str` | 武将名称纠错 |
| `CharacterSimilarityService.is_safe_single_substitution(text, candidate)` → `bool` | 判断唯一错字是否达到自动纠正门槛 |
| `CharacterSimilarityService.rank_single_substitution_candidates(text, candidates)` | 候选闭包内按错字字形分排序 |
| `CharacterFeatureRepository(cache_path=None, user_cache_path=None)` | 汉字特征缓存加载、动态补齐与用户层持久化 |
| `CharacterFeatureRepository.warmup()` / `warmup_characters(chars)` | 预热缓存与拼音库 / 批量补齐词表字符 |
| `get_template_manager(template_name)` → `TemplateManager` | 获取模板管理器单例（仅 `hero_selection` / `match_guide`） |
| `OcrWorker.submit(task)` | 串行执行预热、常规 `OcrTask` 或官方 `OfficialImportTask`，并通过任务完成信号返回结果 |

活动识别路径由 `src.business.recognition.ocr_worker.OcrWorker` 统一执行。worker 在自己的线程内缓存 `GeneralRecognizer` 和 OCR 引擎，配置相同的连续任务复用识别器；官方榜单服务也只在该线程内使用注入引擎。手动截图、文件导入、轮询与官方榜单导入不会在不同线程同时运行 OCR 引擎。关闭窗口时 worker 仅被通知停止并立即返回（不在 GUI 线程同步等待）；若正卡在模型预热中，会直接终止预热线程让进程快速退出，其余未完成任务由退役列表持有并在进程退出前收尾，避免窗口卡死、进程残留与运行中的 QThread 被提前销毁。

风险声明：`terminate` 会跳过引擎清理，相关资源由进程退出时 OS 回收；若预热线程 3 秒未退出则转入退役列表二次等待，15 秒仍未退出时先 `logging.shutdown()` 冲刷日志（R2，b3327f1——`os._exit` 绕过 atexit 与日志冲刷）再以 `os._exit(1)` 强制结束进程（退出码改 0：慢退出属兜底放弃而非故障），避免进程挂起；开发期热重启若进程残留可能累积资源，属已接受风险。

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | 无外部系统依赖 | 仅依赖 ADB 可执行文件、`windows-capture==2.0.1`（PC 客户端 WGC 主路径，Win10 1903+）与 RapidOCR/ONNX 运行时（rapidocr 3.9.2 + onnxruntime 1.23.2，CPU 推理，见 environment.yml）：v6 三件套随 wheel 内置，v4 三件套经 `src/scripts/fetch_recheck_models.py` 官方清单预取（URL+SHA256 钉死，不入仓）；运行时离线纪律绝不联网下载模型 |
| 被调用方 | `src.business.emulator.capture_service` | 持有 AdbCapture / PcClientCapture 实例（`CaptureConnectionManager` 按连接形态构建），编排截图流程；轮询路径通过 raw 帧提速 |
| 被调用方 | `src.business.recognition.ocr_service` | 管理 TemplateManager 和 GeneralRecognizer |
| 被调用方 | `src.business.recognition.peak_select_watcher` | 调用 `detect_selection_cards` 与 `derive_name_rois` 做 2v2 牌面识别；`_restore_missing_cards()` 补齐漏检后以 `layout_is_plausible()` / `board_row_sizes()` 校验排布合理性，不可信排布既不发布也不计入缺席 |
| 被调用方 | `src.business.recognition.ocr_worker` | 未决错法调 `pending_stats.record_pending()`；B2 复核引擎由 recognizer 内部调用 |
| 被调用方 | `src.ui.configuration.mumu_config_dialog` | 连接管理（模拟器品牌下拉 / PC 客户端单选双形态）、模板制作（ROI 框选） |
| 被调用方 | `src.ui.app.main_window` | 轮询流程使用截图和 OCR |

---

## 七、本轮文档校准（2026-10-06）

自基线 `885ea96`（2026-10-02 校准）以来的变更（B1 OCR 全量切换，1694ab7 + d1a55e1）：

**引擎层替换**
- `src/ocr/paddle_loader.py`（228 行）删除 → `src/ocr/engine_loader.py`（248 行）：RapidOCR/ONNX 双套件装载层（套件定义 / SHA256 漂移哨兵 / 指纹日志 / `_FAILED_SUITES` 熔断 / RapidOcrEngine 包装类）；依赖从 paddlepaddle-gpu / paddleocr / protobuf + CUDA 系收敛为 rapidocr==3.9.2 + onnxruntime==1.23.2
- 主引擎 PP-OCRv6-small（wheel 内置三件套）+ 复核引擎 PP-OCRv4-mobile（`fetch_recheck_models.py` 预取，URL+SHA256 钉死、`.part` 原子替换、`verify_v4_models()` 被 doctor/spec/release 复用）；`MUMU_OCR_PRIMARY_ENGINE`（默认 v6）新增，v4 为回滚档且主复核互换；`MUMU_OCR_USE_GPU` 死键退役
- 打包：collect_all 缩至 4 包、excludes 恒排除 paddle 系（根除 rapidocr paddle 后端静态牵出 libpaddle.pyd 约 148MB 问题），精简包 763MB→608MB；build_deps 机制删除

**识别链适配**
- 单槽回退触发线 `_NAME_RECHECK_CONFIDENCE` 0.8 → 0.75（v6 置信分布定标，实测触发率 4.5%→1.7%）
- 对局 team 徽记归一化失败即主→复核引擎链式重读（原仅空文本重读，批量乱码绕过旁路）；119 图重放救回 15/18 个丢标签槽
- 预热单路径化：rec-only 残留删除（RapidOCR 适配层签名不接受 `det=`/`rec=`，paddle 时代预热形态打穿签名致预热状态机误报 `warmup_failed`）；新增适配层契约测试锁死 `ocr(img, cls=False)` 签名
- 复核开关口径：`MUMU_OCR_RECHECK_ENABLED` 代码默认 `False`（旧文档误记默认 true）
- 官方榜单导入：`main` 与识别管线同源（v6）；`rare_char` 只取复核引擎（v4）在 allowed_names 候选闭包内复核，chinese_cht 繁体兜底链退役

**白名单治理**
- 基线 7 对 → 12 对 → 10 对（终态见 3.7 节）；简繁变体对退役论证（瓚↔瓒 0.94、黃↔黄 0.725 由视觉路径自动矫正）
- 新增 `effective_whitelist` property 与 `whitelist_conflicts_with_roster()` 武将库冲突重检谓词（doctor「白名单」检查组 + `tests/test_whitelist_conflicts.py`）

**其它**
- ocr_worker 预热文案引擎中性化；退役 worker 强杀兜底先 `logging.shutdown()`、退出码改 0（b3327f1）
- 陈旧文案说明：recognizer.py / ocr_loader.py / batch_canvas.py / ocr_service.py / ocr_task_coordinator.py 中仍有少量 paddle 时代注释字样未清（如 recognizer.py :300/:400 docstring 写"v6 复核引擎"实义为"复核引擎（当前 v4）"），功能无影响，本文档不照抄
- 424 字特征缓存口径不变（实测仍 424 字）

---

## 八、本轮文档校准（2026-10-11）

自基线 `0ffed36`（2026-10-06 校准）以来的变更：

**页型模板方法收口（2646fd4，纯等价重构）**
- `recognize()` 双页型路径收口为 `_recognize_page(image, plan)` 模板方法：`_PagePlan` 冻结数据类四开关（`has_team` / `short_circuit_name_recheck` / `emit_empty_results` / `log_details`）+ 模块级单例 `_WUJIANG_SELECT_PLAN` / `_MATCH_GUIDE_PLAN`；`_recognize_match_guide()` 保留为两行委托维持既有直测入口；recognizer.py 519 → 493 行，`test_architecture.py` 棘轮预算同步下调（详见 3.6 节）

**match_guide 兜底 OCR 批次零产出短路（6ea1866，行为修复）**
- 名称批次画布零文本行即短路逐槽双证据与阵营逐槽重试链（阵营同口径 `team_batch_has_text`；"批次读出字而归一化失败"保留重试，书法体徽记 v4 异构救回场景不动）——错位页面（模板未命中仍放行的兜底 OCR）每轮 det 调用约 14 次 → 4 次，真对局页识别行为不变（详见 3.6 节）
- 配套：`RapidOcrEngine.reset_call_stats()` / `drain_call_stats()`（读取即清零）、`GeneralRecognizer._reset_det_stats` / `_record_det_stats`（duck-typing 兼容无统计引擎）、OCR 阶段耗时日志新增 `空检=n/m` 聚合摘要（无统计显示 `-/-`）、`_EmptyDetFilter` 幂等挂载 RapidOCR logger 只滤 "The text detection result is empty" 逐条告警（详见 3.9 节）
- 新增 `tests/test_ocr_components.py`：批次零产出短路复现（det 全空断言仅 4 次画布调用）、批次有产出时逐槽双证据照常触发的回归、空检统计接线 / 引擎计数 / 过滤器行为用例

**ocr_worker stage_timing 字典（b5e8e4f，纯等价重构）**
- 七个阶段耗时/标量收口为 `stage_timing: dict[str, float | str]` 逐段写回（键：template_load_ms / template_match_ms / template_confidence / template_scale / template_strategy / recognition_ms / result_save_ms），5 处 `_log_timing` 调用点统一 `**` 展开（每处 6-10 行缩为 1-3 行）；`_log_timing` 签名保持，形参默认值与字典初值逐项一致，日志输出逐字节不变；ocr_worker.py 551 → 518 行（棘轮预算同步下调，详见 4.2 节）

**name_resolution 消解状态词表单源（b5e8e4f，纯等价重构）**
- 三个公共常量：`CONFIRMED_RESOLUTIONS`（原私有 `_CONFIRMED_RESOLUTIONS` 转公共）/ `UNRESOLVED_RESOLUTIONS`（未定全集）/ `ACTIONABLE_UNRESOLVED_RESOLUTIONS`（收窄集，不含 `unknown` 属有意——拒识无读数无候选，无材料可供下一步消费）；ocr_worker 待确认统计、`peak_select_watcher.parse_pool` 人工确认过滤（删本地 `_CONFIRM_RESOLUTIONS`）、recognizer 复核注入三处手抄副本改引常量（详见 3.6 节）

**特征缓存与文档勘误**
- `char_info_cache.json` 424 → 437 字（6b21d75 运行时动态补齐条目回填）；`character_feature_repository.py` 登记随包只读基线写入根风险——`save()` frozen 下无触发链属理论债，将来维护页接入勿简单换 `PROJECT_ROOT`（详见 3.8 节）
- 683fca6 清理代码内 paddle 时代引擎指代（recognizer / ocr_loader / ocr_service / ocr_worker / image_preprocessor 等 docstring 改 RapidOCR 口径），上一轮校准「陈旧文案说明」条目已落地清零；随文勘正本文档 3.6 证据族命名（`batch_enhanced` → 生产代码实际的 `batch_plain`，生产从未产生过 batch_enhanced source）与 4.2 日志示例（补 `name` 类别段，实际格式为 `武将 6 name OCR 原始结果`）
- 本轮范围（0ffed36..2081b5b）内另有 P0-3 竞态守卫原语（BusyGate / OneShotToken）、CaptureConnectionManager 出仓、巅峰赛 SessionGuard、状态栏数据新鲜度芯片、RAG 编码器 ONNX 化等变更，分别归属 module_business / module_emulator / module_rag 等文档；本模块公共接口未受影响

---

## 九、本轮文档校准（2026-10-23）

自基线 `2081b5b`（2026-10-11 校准）以来的变更（`src/capture/` 无代码变更；`src/ocr/` 仅 `card_grid_detector.py` 变更 +101/-14）：

**2v2 巅峰赛卡位稳定性与漏检扣帧（8020da8 + 62c5c23）**
- 主路径产出同样归一到参考几何：新增 `_normalize_card(mask, box, ref_w, rows)` 与参考几何计算（`ref_w` / `ref_h` = 确信卡中位、`rows = _split_rows_by_gap(cards)` 按 y 最大间隙分两行）——贴边动画叠层与出画立绘会把连通域撑得比卡框更宽并向左撑出（2026-10-12 实测 237→254~270、左缘左移 17~21px），逐拍跳变的卡位会同时带偏名条 ROI（面板确认数 9↔8 闪烁）与牌面签名（每拍全量重 OCR），归一后产出与该连通域多宽无关（详见 3.3 节）
- 卡体宽度重拟合阈值收紧：`_refit_card_width` 新增 `min_run_ratio` 关键字参数（默认 0.0，`_normalize_card` 传入 `REFIT_TRUSTED_MIN_RATIO=0.8`），段过短（< 0.8 × `ref_w`，淡色立绘把卡体列截短至 ban_phase 实测 144/238）返回 `None` 维持原状，避免按段中心对齐反被带偏 +29px
- 宽度越窗静默丢弃修复：修复路径中"高度在窗内而宽度越窗"分支（龙饰横翼；同排两卡被动画填缝焊成的 514×326 块）此前静默丢弃，卡数诈降后仍落合法区间（8~14），面板少了卡却无迹可查（2026-10-12 11→9 事故）；现维持拒绝但 `logger.debug` 留痕
- 排布合理性校验：新增公共 API `board_row_sizes(cards) -> (int, int) | None` 与 `layout_is_plausible(cards) -> bool`——游戏奇数张时上排少一张（9 张必 4+5、11 张必 5+6），上排多于下排说明本拍漏检（2026-10-12 实测 11 张盘面被动画填缝焊掉底排相邻两卡后成 5+4，仍落合法区间而被当作正常 9 张发布）；`PeakSelectWatcher._do_work()` 在 `_restore_missing_cards()` 之后、`board_signature()` 之前调用，不可信排布既不发布也不计入缺席、留 info 日志（详见 3.3 与 3.4 节）
- 配套测试：`tests/test_card_grid_detector.py` 加固漂移抑制、漏检扣帧与宽度越窗留痕的回归用例（本轮范围未列入 `src/capture/` / `src/ocr/` 之外的文件清单，此处仅登记调用方行为）

**勘误**
- §3.4 `BOARD_EXIT_TICKS` 2 → 4（peak_select_watcher.py 实际值，旧文档笔误；该常量属 `src/business/recognition/`，不在本模块代码范围，仅文档勘正）

**cb2b47d 影响核对**：该提交主体在 `src/business/recognition/ocr_worker.py`（弃单释放、发布世代守卫、落盘降级、罕见字计数熔断、取消静默），经核对未触及 `src/ocr/` 或 `src/capture/`——本模块文档无需对应更新，仅在 `module_business.md` 记录。

---

## 十、本轮文档校准（2026-10-25）

自基线 `a63bc3c`（2026-10-23 校准）以来的变更（覆盖 366303c / 28fa11b / 8c0f871 / 8f2e70a / feee42b / 514b4f8 / 4eb3445 / 282ed5b / 79fc4a0 / 39ec62b / 4bd9586 / ed8b4ac 共 12 个提交；本轮最大变更为多模拟器五品牌连接与官方 PC 客户端直连）：

**官方 PC 客户端直连（ed8b4ac）**——新增 `src/capture/pc_client.py`（577 行）
- 窗口枚举：`mjs.exe` + `UnityWndClass` 双条件（2026-10-25 真机取证定稿，标题仅展示）；窗口三态解析（未启动提示 / 单窗自动选定 / 多窗用户显式选择，不支持多开）
- 采集主路径 WGC（`windows-capture==2.0.1`，Win10 1903+，遮挡可采集）+ GDI BitBlt 客户区裁剪兜底（窗口须可见）；PrintWindow 经真机取证稳定失败不采用；WGC 会话**窗口句柄定向**（标题子串匹配会命中"名将杀 Agent"工具自身窗口的真机事故根治）
- 黑帧双指标检测与人话引导文案、帧尺寸对账防线、BGRA→RGB 通道反转、DWM 失败回退路径左阴影自校准；进程级 Per-Monitor-V2 DPI 声明硬前置
- 详见新增 §3.1.1；环境依赖新增 `windows-capture==2.0.1`（environment.yml / environment-ci.yml）

**模拟器连接层扩展为五品牌（8f2e70a / feee42b / 514b4f8 / 4eb3445 / 282ed5b）**
- `src/capture/` 原单文件探测模块拆包为 `src/capture/emulator_discovery/`：函数式注册表（`EMULATOR_DISCOVERY_MODULES` 五品牌模块 + `CONNECTION_TYPE_PC`）+ `models.py`（`EmulatorDeviceInfo` 品牌无关值对象）+ 五品牌探测器 + `port_scan.py`（端口段兜底，8 并发）+ `console_utils.py`（console 工具调用 / GBK 解码 / 进程反查）
- `AdbCapture` 品牌参数化：新增 `emulator_type` 构造参数（端口为 0 时按品牌探测唯一实例）；`connect(validate_capture=)` 连接后截帧校验；`_reconcile_emulator_serial()` 对雷电等 5555~5585 adb 主动扫描区端口双查 `emulator-<端口-1>` 序列号
- 探测提速（真机计时）：逍遥 console 死路径移除改 `.memu` 配置直读（10.5s → 0.08s）；进程反查重构为 tasklist 门控 + PowerShell 合并查询（未运行不再白付 ~3.7s，282ed5b 修复 PowerShell 部分成功被 rc 判据误杀的问题）；端口扫描 8 并发（丢弃型回环最坏环境雷电/夜神 3.8s → 0.6s）；探测执行器同键任务去重防连点累积
- 详见 §4.1 五品牌策略表

**官方榜单导入（366303c / 28fa11b / 8c0f871，主体在 `src/business/recognition/`，经 `module_business.md` 记录，此处登记 ocr 侧）**
- `official_board_parser.split_stitched_image()`（新增 107 行中的主体）：20261022 拼接长图按静默带切分为补边虚拟分页，参数按真实样本实测标定（std<8、带长≥40px、段≥300px、每栏≥30 行、补边 170/100px），有效段 ≥2 才判定拼接图（§3.10 已更新）
- 单格识别快通道位于 `official_data_import_service.py`（business 层）：2× 放大单候选先试，数字格需纯排名/胜率格式、名称格需词表精确命中且置信度 ≥0.90 才采信，未过门回退 4× 双候选路径，整板导入提速约 4.6 倍

**连接键多品牌化与配置键扩展（8f2e70a / ed8b4ac，详见 module_config.md）**
- env 键元表新增 `EMULATOR_TYPE` / `EMULATOR_ADB_PATH` / `EMULATOR_ADB_PORT` / `EMULATOR_SCREENSHOT_MODE`（`default_from` 回退旧 `MUMU_*` 键，老 config.env 不改字平滑升级）+ `EMULATOR_BRAND`（切 PC 客户端期间的品牌记忆）+ `PC_WINDOW_TITLE`（PC 直连目标窗口标题），32 → 38 键
- `AdbCapture` / `CaptureConnectionManager` / `OcrService.update_config` / `doctor.py` 连接键读取统一兼容 `emulator_*` 新键优先、`mumu_*` 旧键回退（升级过渡期）

**本模块其余文件**（`src/ocr/`）本轮无代码变更；模板匹配、OCR 识别链、ROI、特征缓存与白名单口径全部延续 2026-10-23 基线。
