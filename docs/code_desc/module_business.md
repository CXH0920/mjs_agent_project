# 模块：业务服务层

> 对应目录：`src/business/`
> 职责：QProcess 子进程管理、服务编排、截图与 OCR 调度、官方榜单图片导入

---

## 一、模块职责

本层是 UI 层和采集层之间的**桥梁**，负责：

1. **QProcess 子进程管理** — 构建 CLI 参数、启动/监控/终止子进程、转发 stdout/stderr、清理临时文件
2. **ADB 截图业务编排** — 管理 AdbCapture 生命周期，协调截图 → 模板匹配 → OCR 的流程
3. **OCR 控制服务** — 模板管理、轮询控制、冷却管理，以及会话取消与失败退避
4. **模拟器后台操作** — 独立执行设备探测与 ADB 会话操作，避免实例枚举阻塞模板截图
5. **官方榜单导入** — 解析固定版式的 2v2 胜率/出场榜与武将放逐榜，按表格行安全覆盖 CSV
6. **推荐数据组装** — 一次读取胜率与推荐指数快照，并提供数值化的卡片排名数据

核心设计原则：**不持有 UI 引用**，全部通过 Qt Signal 与主窗口通信。

---

## 二、文件结构

```
src/business/
├── __init__.py
├── base_fetch_service.py        # QProcess 生命周期、行缓冲与统一收尾
├── fetch_service.py             # 武将采集业务（QProcess 管理）
├── guide_fetch_service.py       # 攻略生成业务（QProcess 管理）
├── synergy_fetch_service.py     # 相性获取业务（QProcess 管理）
├── capture_service.py           # 截图业务编排（ADB 截图 + OCR 调度）
├── emulator_operation_service.py # 模拟器配置页的后台 ADB 操作
├── ocr_service.py               # OCR 控制服务（模板管理 + 轮询）
├── official_data_import_service.py # 官方榜单行分割、单元格 OCR 与 CSV 输出
├── recommendation_service.py       # 推荐页胜率/指数快照与排名组装
└── fetch_utils.py               # QProcess 公共工具函数
```

---

## 三、核心逻辑

### 3.1 QProcess 服务模式

三个采集服务（Hero / Guide / Synergy）遵循完全相同设计模式：

```
QObject 子类
  ├── 多个 Signal 用于 UI 通信
  ├── fetch_*() 方法 → 构建参数 → QProcess.start()
  ├── _on_stdout_ready() → 按完整 UTF-8 行解析进度 → 发射信号
  ├── _on_finished() → 检查 exit_code → 清理临时文件 → 发射完成信号
  └── cancel() → 终止子进程
```

**信号列表：**

```
status_changed → 状态栏文字
progress_output → 子进程 stdout 行（供进度正则解析）
progress_value → (current, total) 供进度条
fetch_completed → (success, message) 通知 UI
error_occurred → 错误信息
cancelled → 用户中止后通知 UI 刷新已分批提交的数据
```

**子进程通信链路：**

```
┌─────────┐   stdout(UTF-8)   ┌──────────────┐
│ 父进程   │ ←────────────── │ 子进程       │
│ (UI)    │   stderr(UTF-8)   │ (CLI 脚本)   │
│         │ ←────────────── │              │
│         │   finished(int)   │              │
│         │ ←────────────── │              │
└─────────┘                  └──────────────┘
```

所有服务使用 `SeparateChannels` 模式，分别读取 stdout 和 stderr。

AI 生成服务以子进程退出码作为唯一成败来源：CLI 根据 `GenerationResult` 在出现失败项时返回非零；stdout 仅用于展示进度，不再承担失败项协议解析职责。用户主动中止会标记取消状态；Windows 通过 `taskkill /T /F` 异步结束 AI Python 进程及全部 Playwright/Edge 后代，进程树清理完成后才发出 `cancelled`，避免浏览器残留占用 OCR 所需资源。其他平台仍终止当前子进程；取消引起的崩溃事件会被忽略，临时文件由 `finished` 统一收尾。

`SynergyReloadWorker` 在后台解析已分批提交的 `synergies.json`；完成后由主线程一次性替换 `SynergyManager` 的内存数据并通知界面刷新，避免取消后同步解析 JSON 阻塞窗口事件循环。

### 3.2 CaptureService（截图业务）

截图流程（手动截图路径）：

```
do_capture()
  └─ [adb-capture 单线程] capture_screenshot() → AdbCapture.screencap_full() → PIL Image
       └─ [GUI 线程] 保存截图到 screenshots/ → OCR 启用？ → emit capture_completed(...)
```

`CaptureService.do_capture()` 和 `do_capture_from_file()` 支持传入 `template_name` 与 `force_ocr`。对局攻略导入使用 `match_guide` 模板并强制执行 OCR，不受“启用武将识别”开关影响；选将推荐保持默认的 `hero_selection` 模板流程。

`capture_screenshot()` 是不保存文件、不触发 OCR 的共享会话接口。它用于模板制作，并与连接/断开共享同一把会话锁，避免后台模板截图和前台截图同时操作同一个 `AdbCapture`。

轮询路径由 `PollCoordinator` 编排（OcrService 只控制定时、冷却与会话，不经过 `do_capture()`）：

```
OcrService.poll_tick → PollCoordinator._on_poll_tick()
  ├─ screencap_full()（内存中，不写磁盘，仅执行一次）
  ├─ hero_selection 模板 → GeneralRecognizer.recognize() → 填入推荐面板 8 槽
  └─ match_guide 模板 → 预留对局攻略结果
```

两个任务共用一个定时器、后台采集锁和截图，但分别维护 `active`、`cooldown_until`、`last_match_time` 与失败状态。武将选择成功后激活对局攻略任务；任一任务冷却时只跳过该任务，不影响另一个任务。

### 3.3 OcrService（OCR 控制）

控制模板制作、OCR 启用和轮询定时器：

```
OcrService (QObject)
  ├── template_changed → 更新 UI 状态
  ├── ocr_completed → 识别结果
  ├── poll_tick → 轮询触发信号（QTimer 驱动）
  │
  ├── create_template(image, roi, template_name) → 制作指定模板
  ├── start_poll(interval_ms)                    → 启动轮询
  ├── stop_poll()                                → 停止轮询
  ├── activate_task(name)                        → 激活指定任务
  ├── set_task_cooldown(name, seconds)           → 设置指定任务冷却
  └── due_poll_tasks()                            → 获取当前到期任务
```

模板按名称独立管理：旧的武将选择模板继续使用 `templates/wujiang_select.png`，对局攻略模板使用 `templates/match_guide/template.png`。模板缺失只影响对应任务，不会暂停另一个任务。

### 3.4 EmulatorOperationService（模拟器后台操作）

`EmulatorOperationService` 只依赖 `CaptureService` 和底层探测模块，不持有 UI。它使用两个单线程执行器：探测线程负责 ADB 路径与 MuMu 实例枚举，ADB 会话线程负责连接、设备测试和模板截图；两类任务互不排队。`probe_all_devices_with_status()` 会在 MuMuManager 异常退出时重试一次，并把失败原因与“正常但没有实例”区分开。

```
MumuConfigDialog
  -> MumuConfigCoordinator.detect_adb() / refresh_devices()
  -> EmulatorOperationService 的后台结果
  -> MumuConfigCoordinator 转发设备、连接和模板截图状态
  -> UI 渲染状态或打开 RoiSelectorDialog
  -> MumuConfigCoordinator.create_template(image, roi, template_name)
  -> OcrService.create_template(image, roi, template_name)
```

`MumuConfigCoordinator` 持有配置草稿、已探测设备和模板截图进行状态；设备刷新失败时视图保留上一次成功的列表与选择。ROI 框选和文件选择保留在 UI 线程，模板保存、运行时 ADB 配置和轮询恢复均由协调器委托服务完成；关闭对话框后协调器停止后台操作，避免迟到回调更新已销毁控件。

### 3.5 OfficialDataImportService（官方榜单导入）

该服务处理本地官方榜单图片，不依赖 ADB 或模板匹配。目标是用表格横线确定行，而不是按 OCR 成功数量排列，避免漏识别一个名称后其余排名整体错位。

```
OfficialDataImportDialog
  -> OfficialDataImportWorker.run()
     -> import_file()（按已选图片顺序串行执行）
           -> OpenCV HoughLinesP 检测横线
           -> 按列比例裁剪每个单元格
           -> 简体 PaddleOCR；名称歧义时按需使用繁体模型 + 武将词表校正 / 胜率数字模板识别
           -> 原子覆盖 CSV + 写入待复核 CSV/行截图
```

**版式与输出：**

| 图片 | 表格 | CSV | 列 |
|---|---|---|---|
| 2v2 | 左侧“胜率最高” | `data/2v2胜率排行.csv` | 排名、武将、胜率 |
| 2v2 | 右侧“出场最多” | `data/2v2出场排行.csv` | 排名、武将 |
| 武将放逐 | 左 1-80 + 右 81-160 | `data/武将放逐.csv` | 排名、武将 |

异常行仍写入正式 CSV，并写入对应 `*_待复核.csv`。复核记录包含 OCR 原文、置信度、异常原因、原图坐标和行截图路径。

**公共接口：**

| 接口 | 参数 | 返回/信号 | 说明 |
|---|---|---|---|
| `OfficialDataImportService.import_selected()` | `{类型: 图片路径}` | `list[dict]` | 空路径跳过；两个类型依次执行 |
| `OfficialDataImportService.import_file()` | `key`, `image_path` | `{name, records, reviews, outputs}` | 导入一种图片并覆盖其 CSV |
| `OfficialDataImportWorker.progress_changed` | `status`, `current`, `total` | 当前文件的 OCR 工作进度 | 胜率模板准备、逐行识别和罕见字兜底状态都会更新 |
| `OfficialDataImportWorker.completed` | - | `list[dict]` | 所有选中导入完成 |
| `OfficialDataImportWorker.failed` | - | `str` | 任一导入失败原因 |

**关键实现：**

```python
boundaries = self._find_data_boundaries(panel, image.shape[0], layout, panel_index)
boundaries, repaired_ranks = self._restore_missing_boundaries(boundaries)
for top, bottom in zip(boundaries, boundaries[1:]):
    expected_rank = len(batch["records"]) + 1
    fields = self._recognize_row(row, columns, column_breaks)
```

`boundaries` 由横线检测得到，因此 `expected_rank` 来自视觉行序而非 OCR 排名。若相邻边界间距超过中位行高的 1.5 倍，服务会按常规行高补插边界，并将补插边界后的数据行写入待复核，防止单条横线漏检导致后续排名整体前移。2v2 胜率格会先向左扩展 ROI，避免截断贴近列线的首位数字；2v2 出场榜及放逐榜的排名/武将分界固定为面板宽度的 45%，避免排名数字落入武将 OCR 区域。武将格会汇总原图与增强图的 OCR 候选，优先采用精确命中词表的完整姓名；两路精确结果冲突时不按置信度强选。最高结果只有单字时，再保留背景留白按字形补识别。写入前，完整名称统一经过词表的编辑距离与字形特征二次判定，不因高置信度跳过校正；名称发生校正时以“武将名称已由词表校正”写入待复核。OCR 原文作为词表前缀只有唯一候选时可自动补全；多个候选共享至少两个汉字前缀时不使用编辑距离或微小视觉分差强行决胜，而是按需调用 `chinese_cht` 繁体模型继续确认。模型不可用或仍不能唯一确认时保留原结果，以“武将名称候选不唯一”写入待复核。该逻辑仅用于官方导入，不影响常规武将识别。再以排名格和同列小数位构建字体模板，识别四位胜率数字。工作线程会先显示不定进度，待横线检测得到行数后，将胜率模板准备和逐行识别都计入当前文件进度；进入罕见字兜底时仅更新状态文字，不重置当前进度。每个图片只创建一个 `OfficialDataImportWorker`；同时选择 2v2 和放逐时在同一线程顺序处理，不会互相争用该功能的 OCR 实例。它与常规 `OcrWorker` 是独立实例，轮询或截图识别同时运行时会共享 CPU/GPU 资源。

**名称降级决策顺序：**

1. 收集原图放大与增强锐化两次 OCR 的全部文本块；完整文本精确命中 `heroes.json` 词表时优先采用，不与单字的错误高置信度竞争；两路精确结果指向不同武将时转入歧义兜底。
2. 写入前，完整候选统一通过 `CharacterSimilarityService.correct_hero_name()` 的编辑距离与字形特征二次判定，不因高置信度跳过校正；发生校正时以“武将名称已由词表校正”写入待复核 CSV 和行截图。
3. 若最高候选为单字，按亮色字形切分 2-4 个字符，保留原背景、左右内容与边缘留白后逐字 OCR；拼接结果通过 `CharacterSimilarityService.correct_hero_name()` 校正后必须仍命中词表。
4. 逐字 OCR 未得到可用名称时，只有 OCR 原文在词表中唯一对应一个前缀候选才自动补全。
5. 公共前缀存在多个候选，或多个编辑距离候选共享至少两个汉字前缀时，按需调用繁体 `chinese_cht` 模型继续确认，不以词表顺序或微小视觉分差决胜。模型不可用或仍不能确认时保留原文，并以“武将名称候选不唯一”写入待复核 CSV 和行截图。

该顺序能优先恢复低置信度但完整的词表候选，同时避免将“郭”“范”等多候选单字或“夏侯”“司马”等复姓公共前缀强行改为错误角色。

---

## 四、关键代码片段

### 4.1 QProcess 参数构建与启动

```python
def _start_process(self, args: list[str]) -> None:
    self._process = QProcess(self)
    self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
    self._process.readyReadStandardError.connect(self._on_stderr_ready)
    self._process.finished.connect(self._on_finished)
    self._process.errorOccurred.connect(self._on_error)
    self._process.start(sys.executable, args)
```

> **设计思路：** `SeparateChannels` 确保 stdout 和 stderr 不混在一起。信号连接在 start 之前绑定，避免丢失启动瞬间的事件。`sys.executable` 保证与父进程使用同一 Python 解释器。

### 4.2 stdout 行缓冲与进度正则解析

```python
def _on_stdout_ready(self) -> None:
    data = self._process.readAllStandardOutput()
    self._stdout_line_buffer.extend(data)
    while b"\n" in self._stdout_line_buffer:
        raw_line, _, remaining = self._stdout_line_buffer.partition(b"\n")
        self._stdout_line_buffer[:] = remaining
        self._on_stdout_line(raw_line.decode("utf-8", errors="replace").strip())
```

> **设计思路：** QProcess 的一次 readyRead 不等于一行输出，且 UTF-8 字符可能跨分块。基类保留未完成字节，只有读到换行后才解码并交给子类；进程结束时还会读取残余管道内容并分发行尾。取消时只调用 `kill()`，不在 GUI 线程使用 `waitForFinished()`；临时文件清理和状态通知继续由 `finished` 信号统一完成。

### 4.3 临时文件自动清理

```python
def _on_finished(self, exit_code: int) -> None:
    tmp_path = self._context.get("tmp_path", "")
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
        except OSError as e:
            logger.warning("清理临时文件失败 %s: %s", tmp_path, e)
```

> **设计思路：** 指定获取模式（指定采集、指定配对、选定武将）需要写入临时 JSON 文件传给子进程。正常和异常退出都要清理，避免残留文件堆积。

---

## 五、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.manager` | 子进程完成后调用 `manager.load()` 刷新数据缓存 |
| 依赖 | `src.scraper.*` | 构建 CLI 参数调用爬虫/AI 脚本 |
| 依赖 | `src.capture.adb_screen` | CaptureService 持有 AdbCapture 实例 |
| 依赖 | `src.ocr.*` | OCR 控制服务管理模板和识别器 |
| 依赖 | `src.ocr.character_similarity` | 官方榜单复用公开的武将词表纠错服务 |
| 依赖 | `src.data.win_rate_repository` | 胜率 CSV 覆盖后清空读取缓存 |
| 依赖 | `src.data.recommendation_index_repository` | 提供推荐指数 CSV 的手动重建接口 |
| 被调用方 | `src.ui.main_window` | 主窗口连接业务服务的 Signal，UI 操作触发 fetch_*() |
| 被调用方 | `src.ui.official_data_import_dialog` | 对话框创建后台导入线程并显示结果 |
