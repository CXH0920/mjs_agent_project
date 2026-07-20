"""对局攻略页面骨架。

首期只提供四个可扩展容器，具体业务内容由后续识别结果驱动。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGridLayout, QVBoxLayout, QWidget


class MatchGuidePanel(QWidget):
    """对局攻略主面板，保留四个后续可视化扩展区域。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._blocks: list[QFrame] = []
        self._block_data: list[object | None] = [None] * 4
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        for index in range(4):
            block = QFrame(self)
            block.setObjectName(f"matchGuideBlock{index + 1}")
            block.setFrameShape(QFrame.Shape.StyledPanel)
            block.setFrameShadow(QFrame.Shadow.Plain)
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(8, 8, 8, 8)
            block_layout.setSpacing(0)
            layout.addWidget(block, index // 2, index % 2)
            self._blocks.append(block)

        layout.setRowStretch(0, 1)
        layout.setRowStretch(1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    def update_block(self, index: int, data: object) -> None:
        """保存指定板块的数据入口，具体可视化由后续版本实现。"""
        if not 0 <= index < len(self._blocks):
            raise IndexError(f"板块索引超出范围: {index}")
        self._block_data[index] = data

    def clear_blocks(self) -> None:
        """清空四个板块的预留数据。"""
        self._block_data = [None] * len(self._blocks)
