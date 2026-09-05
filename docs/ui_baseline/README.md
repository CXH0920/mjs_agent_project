# UI 视觉基线

本目录存放 UI 离屏截图基线，用于比较布局、层级、溢出和固定尺寸稳定性。
2026-09 批次 7（G1 样式收敛）起为本轮新生成基线的存放处：旧阶段截图
（`before-`/`after-foundation-`/`after-shell-`/`after-library-`/`after-workspaces-` 系列）
已清理，新基线以 `before-g1-` / `after-g1-` 前缀区分变更前后。

离屏环境可能缺少中文字体，中文显示为方框不作为缺陷判断依据。验收重点是控件边界、对齐、换行、滚动和页面几何；中文字体质量应在实际 Windows 窗口中复核。

## 使用规则

1. 基线文件只在明确的样式/设计阶段更新，不因业务数据变化覆盖。
2. 变更后用新前缀生成对照截图（如 G1 用 `before-g1-` / `after-g1-`），保留变更前文件。
3. 截图不得包含 API Key、用户目录、日志原文或其他敏感信息。
4. 新页面至少覆盖空状态和一个主要业务状态；动态业务状态在对应页面阶段补充。

## 生成方式

```powershell
python -m src.scripts.capture_ui_baselines --prefix before-g1
```

需在项目约定的 myenv 环境中运行。

脚本使用内存固定数据，不连接 ADB、不执行 OCR，也不读写正式业务数据；
覆盖推荐页与对局攻略页的空态/主要业务状态 × 三档窗口尺寸（960x640 / 1100x760 / 1440x900）。

阶段七弹窗验收：

```powershell
$env:QT_SCALE_FACTOR='1'; G:\CONDA\Anaconda3\envs\myenv\python.exe -m pytest tests\test_dialog_shell.py -q
$env:QT_SCALE_FACTOR='1.25'; G:\CONDA\Anaconda3\envs\myenv\python.exe -m pytest tests\test_dialog_shell.py -q
$env:QT_SCALE_FACTOR='1.5'; G:\CONDA\Anaconda3\envs\myenv\python.exe -m pytest tests\test_dialog_shell.py -q
```
