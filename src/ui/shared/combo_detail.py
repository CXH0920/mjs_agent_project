"""实战配队详情对话框（选将推荐与巅峰赛选将共用）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QVBoxLayout
from src.data.combo_seats import format_seats
from src.ui.shared.style import ROLE_SECONDARY
from src.ui.shared.widgets import DialogFooter, PageHeader


def show_combo_detail(parent, combo) -> None:
    """配队详情：2×2 号位示意 + 座次要求 + note 原文。"""
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"实战配队 ★{combo.rating} · {combo.hero1_name} + {combo.hero2_name}")
    dialog.setMinimumWidth(430)
    layout = QVBoxLayout(dialog)
    layout.addWidget(
        PageHeader(
            f"实战 ★{combo.rating}",
            f"{combo.hero1_name} + {combo.hero2_name}",
        )
    )

    seat_names: dict[int, list[str]] = {seat: [] for seat in (1, 2, 3, 4)}
    for name, seat_list in (
        (combo.hero1_name, combo.hero1_seats),
        (combo.hero2_name, combo.hero2_seats),
    ):
        for seat in seat_list:
            seat_names[seat].append(name)
    seat_grid = QGridLayout()
    seat_grid.setSpacing(6)
    for index, seat in enumerate((1, 2, 3, 4)):
        names = "、".join(seat_names[seat]) if seat_names[seat] else "--"
        cell = QLabel(f"{seat}号位\n{names}")
        cell.setObjectName("recommendationComboSeatCell")
        cell.setStyleSheet("border: 1px solid #65758b; border-radius: 6px; padding: 8px;font-size: 13px;")
        cell.setMinimumHeight(52)
        cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seat_grid.addWidget(cell, index // 2, index % 2)
    layout.addLayout(seat_grid)

    requirement = QLabel(
        f"座次要求: {combo.hero1_name}[{format_seats(combo.hero1_seats)}] "
        f"· {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
    )
    requirement.setWordWrap(True)
    layout.addWidget(requirement)

    if combo.note:
        note_label = QLabel(combo.note)
        note_label.setWordWrap(True)
        note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(note_label)

    footer = DialogFooter(accept_text="关闭", show_cancel=False, accept_role=ROLE_SECONDARY)
    footer.accepted.connect(dialog.accept)
    layout.addWidget(footer)
    dialog.exec()
