# UI 层架构规范

> 长期设计规则与决策依据，覆盖主窗口、对话框体系、服务通信模式。

## 一、架构模式：业务服务 + Qt 信号

### 规则 1.1：业务逻辑不持有 UI 引用

所有业务服务（`*Service` 类）继承 `QObject`，通过 `Signal` 与主窗口通信，不持有 `MainWindow` 或任何 `QWidget` 的引用。

```
CaptureService —Signal→ MainWindow —slot→ 更新 UI 或弹窗
OcrService    —Signal→ MainWindow —slot→ 启动/停止轮询
```

**为什么：** 如果业务服务持有 UI 引用，单元测试时需要构造完整的 Qt 组件树。信号槽解耦后，测试只需 assert 信号发射的参数是否正确，无需实例化 `QMainWindow`。这也是 Qt 框架推荐的设计模式。

### 规则 1.2：QTimer.singleShot(0, ...) 用于异步执行

截图、OCR 等可能阻塞的操作通过 `QTimer.singleShot(0, fn)` 延迟到事件循环下一轮执行，不直接调用。

**为什么：** `screencap_full()` 是同步阻塞的（subprocess.run + timeout），如果在 UI 事件循环中直接调用，界面会冻结直到截图完成。`singleShot(0)` 将操作排到事件队列末尾，让 Qt 有机会先处理完当前批次的 UI 刷新。

### 规则 1.3：轮询定时器不自杀

轮询触发时如果条件不满足（如 ADB 未配置、模板未加载），应 `return` 等待下一次 tick，而非调用 `stop_poll()` 永久停止。

**为什么：** `stop_poll()` 会杀死定时器，唯一重新激活的入口是用户再次打开配置对话框。如果用户远程操作或条件临时变化（如模拟器未启动），程序需要能自动恢复。自杀逻辑导致"用户配置好了一切，但轮询永远不会启动"的 bug。

## 二、对话框体系

### 规则 2.1：BaseHeroSelectDialog 控制选择行为

所有武将选择对话框继承 `BaseHeroSelectDialog`，通过 `SelectionMode` 枚举控制单选/多选/数量限制。不使用 QDialog 的直接实例化。

**为什么：** 武将选择逻辑在多个地方复用（获取指定武将、指定攻略生成、相性配对、选定武将相性）。如果每次独立实现，会导致搜索/筛选/复选框逻辑重复 4 遍。基类统一了这些逻辑，子类只需指定模式和返回格式。

### 规则 2.2：对话框不直接修改数据源

对话框对业务服务的调用仅在 `accepted` 后触发，不在对话框内嵌 `exec()` 的同时启动异步操作。

**为什么：** 对话框 `exec()` 阻塞事件循环。如果在对话框内启动 `QProcess` 并在 `finished` 时更新 UI，会因为事件循环被阻塞导致信号丢失或 UI 更新延迟。正确的顺序是：`dialog.exec()` → `accepted` → 退出阻塞 → 启动服务。

## 三、推荐面板数据流

### 规则 3.1：OCR 置信度不映射为推荐置信度

`update_recommendations()` 中 `card.set_confidence(0.5)` 固定值。

**为什么：** OCR 置信度反映的是"图像识别准确率"，而非"该武将的阵容适配度"。把 OCR 置信度直接当作推荐指数会误导用户——高识别置信度不等于高推荐价值。固定 0.5 表示"来自截图识别"，与 AI 生成的推荐指数（0~1）区分。

### 规则 3.2：选中武将的相性/胜率按名称加载

`_load_synergies_by_name(card_idx, hero_name)` 和 `_load_win_rate_by_name(card_idx, hero_name)` 使用武将名称查询，而非 index。

**为什么：** OCR 返回的是 `{index, name}`，其中 index 表示 8 个槽位的位置（1-8），不反映数据库中的英雄 ID。通过名称匹配到 `Hero` 对象后再获取 ID，进而查询 `SynergyManager` 和胜率 CSV。

### 规则 3.3：未匹配的武将名仍显示名称

```python
card._name_label.setText(name or "未知武将")
```

**为什么：** OCR 识别出的名称即使不在 HeroManager 中（新武将或识别错误），也应显示给用户看，方便人工判断。如果直接清空或显示"空"，用户无法区分"没有识别到"和"识别了但不认识"。

## 四、模拟器配置对话框

### 规则 4.1：共享 CaptureService 的 AdbCapture 实例

`MumuConfigDialog` 通过 `capture_service` 参数获取已有的 `AdbCapture` 实例，并在连接/断开后同步状态回去。

**为什么：** 如果对话框持有独立的 `AdbCapture`，而 `CaptureService` 也持有另一个，会出现"对话框中断开了，但截图按钮还在用旧连接"的状态不一致。共享引用确保了单点真相。

### 规则 4.2：制作模板 = 截图 + RoiSelector + 保存

`_do_make_template()` 流程固定：`screencap_full()` → `RoiSelectorDialog` → `TemplateManager.set_template()`。

**为什么：** 模板需要精确的 ROI 坐标来匹配游戏画面。如果只允许用户选择一张裁剪好的图片，用户难以精确定位 ROI 区域（不知原始分辨率下的坐标）。截图 + 拖拽框选可以在原始分辨率下精确定位。

## 五、全局样式

### 规则 5.1：势力色表是硬编码

`FACTION_COLORS` 字典直接定义势力→颜色映射，不从外部配置加载。

**为什么：** 名将杀的势力体系是固定的（秦、汉、楚、赵...共 14 个），游戏上线后至今未变更过。从配置加载只会增加复杂度而不会获得灵活性。如果未来新增势力，修改源码是预期行为。
