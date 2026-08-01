# UI 视觉基线

本目录记录 UI 改造前及阶段二至六各阶段的离屏截图，用于比较布局、层级、溢出和固定尺寸稳定性。阶段七弹窗使用自动化几何与 DPI 验收，不新增包含配置字段的截图。

## 截图集合

每个集合均覆盖以下尺寸：

- `960x640`：最小支持窗口。
- `1100x760`：默认窗口。
- `1440x900`：宽屏窗口。

每个尺寸均包含以下视图：

- `library-heroes`：资料库/武将资料。
- `library-cards`：资料库/卡牌图鉴。
- `recommendation-empty`：选将推荐空状态。
- `match-empty`：对局攻略空状态。
- `recommendation-results`：阶段五固定 2×4 推荐结果。
- `match-confirmed`：阶段六已确认阵容与攻略总览。

文件前缀：

- `before-`：阶段二实施前。
- `after-foundation-`：设计 Token 和全局 QSS 基础首次落地后。
- `after-shell-`：阶段三左侧导航、顶部上下文栏和响应式外壳落地后。
- `after-library-`：阶段四武将资料与卡牌图鉴布局、层级和上下文操作落地后；覆盖三档 `library-heroes` 与 `library-cards`。
- `after-workspaces-`：阶段五、六推荐和对局工作台落地后；覆盖三档空状态与主要业务状态。

离屏环境可能缺少中文字体，中文显示为方框不作为缺陷判断依据。验收重点是控件边界、对齐、换行、滚动和页面几何；中文字体质量应在实际 Windows 窗口中复核。

## 使用规则

1. 基线文件只在明确的设计阶段更新，不因业务数据变化覆盖。
2. 页面迁移后使用新的阶段前缀生成对照截图，保留 `before-` 文件。
3. 截图不得包含 API Key、用户目录、日志原文或其他敏感信息。
4. 新页面至少覆盖空状态和一个主要业务状态；动态业务状态在对应页面阶段补充。

## 生成方式

```powershell
G:\CONDA\Anaconda3\envs\myenv\python.exe scripts\capture_ui_baselines.py
```

脚本使用内存固定数据，不连接 ADB、不执行 OCR，也不读写正式业务数据。

阶段七弹窗验收：

```powershell
$env:QT_SCALE_FACTOR='1'; G:\CONDA\Anaconda3\envs\myenv\python.exe -m pytest tests\test_dialog_shell.py -q
$env:QT_SCALE_FACTOR='1.25'; G:\CONDA\Anaconda3\envs\myenv\python.exe -m pytest tests\test_dialog_shell.py -q
$env:QT_SCALE_FACTOR='1.5'; G:\CONDA\Anaconda3\envs\myenv\python.exe -m pytest tests\test_dialog_shell.py -q
```
