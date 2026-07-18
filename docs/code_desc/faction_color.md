# 势力配色配置

## 界面结构

`FactionColorDialog` 使用滚动列表展示势力。每行只显示势力名称、颜色小方块和 Hex 代码，避免在主界面长期占用大块调色板区域。

## Color Picker

`ColorPicker` 点击颜色小方块后打开 Qt 非原生颜色浮层，提供 HSB 调整控件和屏幕取色工具。用户取消时恢复打开前的颜色，确认后才把颜色写入配置页草稿。

## 保存流程

```text
ColorPicker.color()
  -> FactionColorDialog._save()
  -> save_faction_colors()
  -> data/faction_colors.json
  -> reload_faction_colors()
  -> RecommendationPanel.refresh_faction_colors()
```

保存前会校验每个值是否为六位 Hex 颜色；保存成功后刷新当前推荐卡片中的势力标签。

## 按钮与样式

势力配色页的“保存”“取消”以及颜色浮层中的常用操作统一显示中文。项目内 Qt 样式统一使用 `background-color`，不再使用 Qt 兼容性不稳定的 `background` 简写，避免按钮创建或刷新时输出 `Could not parse stylesheet of object QPushButton`。
