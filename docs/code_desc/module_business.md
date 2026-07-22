# 模块：业务服务层

> 对应目录：`src/business/`
> 职责：QProcess 子进程管理、服务编排、截图与 OCR 调度、官方榜单图片导入

---

## 一、模块职责

本层是 UI 层和采集层之间的**桥梁**，负责：

1. **QProcess 子进程管理** — 构建 CLI 参数、启动/监控/终止子进程、转发 stdout/stderr、清理临时文件
2. **ADB 截图业务编排** — 管理 AdbCapture 生命周期，协调截图 → 模板匹配 → OCR 的流程
3. **OCR 控制服务** — 模板管理、轮询控制、冷却管理
4. **模拟器后台操作** — 独立执行设备探测与 ADB 会话操作，避免实例枚举阻塞模板截图
5. **官方榜单导入** — 解析固定版式的 2v2 胜率/出场榜与武将放逐榜，按表格行安全覆盖 CSV

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

AI 生成服务以子进程退出码作为唯一成败来源：CLI 根据 `GenerationResult` 在出现失败项时返回非零；stdout 仅用于展示进度，不再承担失败项协议解析职责。

### 3.2 CaptureService（截图业务）

截图流程（手动截图路径）：

```
do_capture()
  └─ QTimer.singleShot(0, _execute_capture)  ← 延后回调，ADB 截图仍在 GUI 线程执行
       ├─ AdbCapture.screencap_full() → PIL Image
       ├─ 保存截图到 screenshots/
       ├─ OCR 启用？
       │   ├─ TemplateManager.match(image, template_name) → 页面模板匹配
       │   │   ├─ 否 → 跳过
       │   │   └─ 是 → GeneralRecognizer.recognize() → 保存 JSON
       │   └─ 返回结果
      └─ emit capture_completed({image, save_path, ocr_results, ocr_matched})
```

`CaptureService.do_capture()` 和 `do_capture_from_file()` 支持传入 `template_name` 与 `force_ocr`。对局攻略导入使用 `match_guide` 模板并强制执行 OCR，不受“启用武将识别”开关影响；选将推荐保持默认的 `hero_selection` 模板流程。

`capture_screenshot()` 是不保存文件、不触发 OCR 的共享会话接口。它用于模板制作，并与连接/断开共享同一把会话锁，避免后台模板截图和前台截图同时操作同一个 `AdbCapture`。

轮询路径（OcrService 控制，不经过 do_capture）：

```
OcrService.poll_tick → MainWindow._on_poll_capture()
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
  -> EmulatorOperationService.detect_adb() / refresh_devices()
  -> devices_refreshed 或 device_refresh_failed
  -> EmulatorOperationService.connect() / test_device()
  -> EmulatorOperationService.capture_template_screenshot()
  -> signal 回到 UI：显示结果或打开 RoiSelectorDialog
  -> OcrService.create_template(image, roi, template_name)
```

ROI 框选保留在 UI 线程，模板保存和文件选择统一委托 `OcrService`；设备刷新失败时对话框保留上一次成功的设备列表和选择。模板截图进行中由 UI 显式记录，其他状态刷新不会重新启用或覆盖其按钮文字；关闭对话框后服务不再向该对话框投递结果。

### 3.5 OfficialDataImportService（官方榜单导入）

该服务处理本地官方榜单图片，不依赖 ADB 或模板匹配。目标是用表格横线确定行，而不是按 OCR 成功数量排列，避免漏识别一个名称后其余排名整体错位。

```
OfficialDataImportDialog
  -> OfficialDataImportWorker.run()
     -> import_file()（按已选图片顺序串行执行）
           -> OpenCV HoughLinesP 检测横线
           -> 按列比例裁剪每个单元格
           -> 简体 PaddleOCR；歧义单字时按需使用繁体模型 + 武将词表校正 / 胜率数字模板识别
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
for top, bottom in zip(boundaries, boundaries[1:]):
    expected_rank = len(batch["records"]) + 1
    fields = self._recognize_row(row, columns, column_breaks)
```

`boundaries` 由横线检测得到，因此 `expected_rank` 来自视觉行序而非 OCR 排名。2v2 胜率格会先向左扩展 ROI，避免截断贴近列线的首位数字；2v2 出场榜及放逐榜的排名/武将分界固定为面板宽度的 45%，避免排名数字落入武将 OCR 区域。武将格会汇总原图与增强图的 OCR 候选，优先采用精确命中词表的完整姓名；最高结果只有单字时，再保留背景留白按字形补识别。补识别仍失败但该首字在词表中只有唯一候选时可确认该候选；存在多个同首字候选的单字才懒加载 `chinese_cht` 繁体模型。繁体模型的完整候选会再次通过词表校正，最终必须精确命中词表；加载或推理不可用时保留原结果待复核。该逻辑仅用于官方导入，不影响常规武将识别。再以排名格和同列小数位构建字体模板，识别四位胜率数字。工作线程会先显示不定进度，待横线检测得到行数后，将胜率模板准备和逐行识别都计入当前文件进度；进入罕见字兜底时仅更新状态文字，不重置当前进度。每个图片只创建一个 `OfficialDataImportWorker`；同时选择 2v2 和放逐时在同一线程顺序处理，不会互相争用该功能的 OCR 实例。它与常规 `OcrWorker` 是独立实例，轮询或截图识别同时运行时会共享 CPU/GPU 资源。

**名称降级决策顺序：**

1. 收集原图放大与增强锐化两次 OCR 的全部文本块；任一完整文本精确命中 `heroes.json` 词表时优先采用，不与单字的错误高置信度竞争。
2. 若最高候选为单字，按亮色字形切分 2-4 个字符，保留原背景、左右内容与边缘留白后逐字 OCR；拼接结果通过 `_correct_with_hero_list()` 校正后必须仍命中词表。
3. 逐字 OCR 未得到可用名称时，只有该首字在词表中唯一对应一个角色才自动补全。
4. 首字存在多个或零个候选时，按需调用繁体 `chinese_cht` 模型识别整格和字形；完整候选经词表校正后必须精确命中词表才采用。模型不可用或仍不能确认时保留单字，并以“武将名称疑似缺字”写入待复核 CSV 和行截图。

该顺序能优先恢复低置信度但完整的词表候选，同时避免将“郭”“范”等多候选单字强行改为错误角色。

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
| 依赖 | `src.ocr.recognizer` | 官方榜单复用两段式武将词表校正 |
| 依赖 | `src.data.win_rate_repository` | 胜率 CSV 覆盖后清空读取缓存 |
| 被调用方 | `src.ui.main_window` | 主窗口连接业务服务的 Signal，UI 操作触发 fetch_*() |
| 被调用方 | `src.ui.official_data_import_dialog` | 对话框创建后台导入线程并显示结果 |
