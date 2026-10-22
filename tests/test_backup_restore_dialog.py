"""备份恢复对话框测试：列表对照 / 预览 / 恢复流程 / busy 拦截 / 坏备份禁恢复。"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox
from src.business.maintenance.backup_restore_service import select_group_entries
from src.ui.data_admin.backup_restore_dialog import BackupRestoreDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _heroes(*pairs: tuple[int, str]) -> list[dict]:
    return [{"id": i, "name": n} for i, n in pairs]


def _seed(tmp_path: Path) -> None:
    """当前 2 名武将 + 一新一旧两份备份。"""
    _write(tmp_path / "heroes.json", _heroes((1, "刘备"), (2, "孙权")))
    _write(tmp_path / "backups" / "heroes-20261010-120243-000000.json", _heroes((1, "刘备")))
    _write(tmp_path / "backups" / "heroes-20260901-090000-000000.json",
           _heroes((1, "刘备"), (2, "孙权"), (3, "吕布")))


def test_dialog_lists_current_row_and_backups_with_preview(tmp_path: Path) -> None:
    _app()
    _seed(tmp_path)

    dialog = BackupRestoreDialog(lambda: False, data_dir=tmp_path)

    assert dialog._table.rowCount() == 3  # 当前 + 2 备份
    assert dialog._table.item(0, 2).text() == "← 当前"
    assert not dialog._restore_button.isEnabled()

    dialog._table.selectRow(1)  # 最新备份：武将 1 名，较当前 -1
    assert "预览：武将 1 名" in dialog._preview_label.text()
    assert "-1" in dialog._preview_label.text()
    assert dialog._restore_button.isEnabled()

    dialog._table.selectRow(0)  # 当前文件伪行：无恢复语义
    assert "当前：武将 2 名" in dialog._preview_label.text()
    assert not dialog._restore_button.isEnabled()


def test_restore_flow_replaces_file_emits_signal_and_shows_summary(
    tmp_path: Path, monkeypatch
) -> None:
    """单文件恢复全流程：覆盖目标文件、发 restored 信号、弹变更摘要、留底恢复前状态。"""
    _app()
    current = _heroes((1, "刘备"), (2, "孙权"))
    _write(tmp_path / "heroes.json", current)
    backup_data = _heroes((1, "刘备"))
    _write(tmp_path / "backups" / "heroes-20261010-120243-000000.json", backup_data)

    dialog = BackupRestoreDialog(lambda: False, data_dir=tmp_path)
    summaries: list[str] = []
    # heroes 属核心组，确认框为三键（整组/单文件/取消）；此处选"仅恢复该文件"
    monkeypatch.setattr(
        dialog, "_confirm_restore",
        lambda target, entry: ("single", None),
    )
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: summaries.append(args[2]),
    )
    emitted: list[str] = []
    dialog.restored.connect(emitted.append)

    dialog._table.selectRow(1)
    dialog._on_restore()

    assert json.loads((tmp_path / "heroes.json").read_text(encoding="utf-8")) == backup_data
    assert emitted == ["heroes"]
    assert len(summaries) == 1
    assert "2 → 1 名" in summaries[0]
    assert "移除 1" in summaries[0]
    assert "可再次打开本对话框回退" in summaries[0]
    # 恢复前的当前状态已留底（双向可回退）
    snapshots = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "backups").glob("heroes-2026*.json")
    ]
    assert current in snapshots


def test_group_restore_restores_core_files_together(tmp_path: Path, monkeypatch) -> None:
    """整组恢复：heroes 锚点 + guides 取不晚于锚点的备份；synergies 无备份保持当前。"""
    _app()
    _write(tmp_path / "heroes.json", _heroes((1, "刘备"), (2, "孙权")))
    _write(tmp_path / "guides.json", [{"hero_id": 1, "description": "今天的攻略"}])
    _write(tmp_path / "synergies.json", [])
    heroes_backup = _heroes((1, "刘备"))
    guides_backup = [{"hero_id": 1, "description": "三天前的攻略"}]
    _write(tmp_path / "backups" / "heroes-20261010-120243-000000.json", heroes_backup)
    _write(tmp_path / "backups" / "guides-20261009-090000-000000.json", guides_backup)
    _write(tmp_path / "backups" / "guides-20261020-090000-000000.json", [])  # 晚于锚点，不选

    dialog = BackupRestoreDialog(lambda: False, data_dir=tmp_path)
    summaries: list[str] = []
    monkeypatch.setattr(
        dialog, "_confirm_restore",
        lambda target, entry: ("group", select_group_entries(dialog._targets, target.key, entry)),
    )
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: summaries.append(args[2]),
    )
    emitted: list[str] = []
    dialog.restored.connect(emitted.append)

    dialog._table.selectRow(1)  # heroes 备份（锚点）
    dialog._on_restore()

    assert json.loads((tmp_path / "heroes.json").read_text(encoding="utf-8")) == heroes_backup
    assert json.loads((tmp_path / "guides.json").read_text(encoding="utf-8")) == guides_backup
    assert json.loads((tmp_path / "synergies.json").read_text(encoding="utf-8")) == []  # 保持当前
    assert emitted == ["heroes"]
    assert len(summaries) == 1
    assert "已整组恢复到" in summaries[0]
    assert "武将：2 → 1 名" in summaries[0]
    assert "相性：保持当前状态" in summaries[0]


def test_restore_failure_message_states_file_unmodified(
    tmp_path: Path, monkeypatch
) -> None:
    """恢复失败提示必须说明当前文件未被修改（原子写入），避免用户恐慌。"""
    _app()
    _write(tmp_path / "heroes.json", _heroes((1, "刘备")))
    _write(tmp_path / "backups" / "heroes-20261010-120243-000000.json", _heroes((1, "刘备")))
    dialog = BackupRestoreDialog(lambda: False, data_dir=tmp_path)
    critical_texts: list[str] = []
    monkeypatch.setattr(dialog, "_confirm_restore", lambda target, entry: ("single", None))
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *args, **kwargs: critical_texts.append(args[2]),
    )

    import src.ui.data_admin.backup_restore_dialog as dialog_module
    monkeypatch.setattr(
        dialog_module, "restore_backup",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("磁盘占用")),
    )

    dialog._table.selectRow(1)
    dialog._on_restore()

    assert critical_texts and "当前文件未被修改" in critical_texts[0]
    assert json.loads((tmp_path / "heroes.json").read_text(encoding="utf-8")) == _heroes((1, "刘备"))


def test_backup_source_label_updates_per_target(tmp_path: Path) -> None:
    """备份来源标注随目标切换：核心三文件为自动快照，combos 为导入/留底产生。"""
    _app()
    _write(tmp_path / "heroes.json", _heroes((1, "刘备")))
    dialog = BackupRestoreDialog(lambda: False, data_dir=tmp_path)

    assert "自动快照" in dialog._source_label.text()

    dialog._target_combo.setCurrentIndex(3)  # combos

    assert "手工编辑不产生快照" in dialog._source_label.text()


def test_restore_blocked_while_generation_busy(tmp_path: Path, monkeypatch) -> None:
    _app()
    _seed(tmp_path)
    dialog = BackupRestoreDialog(lambda: True, data_dir=tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )

    dialog._table.selectRow(1)
    dialog._on_restore()

    assert warnings and "生成任务正在运行" in warnings[0]
    assert json.loads((tmp_path / "heroes.json").read_text(encoding="utf-8")) == _heroes(
        (1, "刘备"), (2, "孙权")
    )


def test_invalid_backup_selected_disables_restore(tmp_path: Path) -> None:
    _app()
    _write(tmp_path / "heroes.json", _heroes((1, "刘备")))
    _write(tmp_path / "backups" / "heroes-20261010-120243-000000.json", [{"id": 2}])

    dialog = BackupRestoreDialog(lambda: False, data_dir=tmp_path)

    dialog._table.selectRow(1)
    assert "无法恢复" in dialog._preview_label.text()
    assert not dialog._restore_button.isEnabled()


def test_empty_backup_state_shows_hint(tmp_path: Path) -> None:
    _app()
    _write(tmp_path / "heroes.json", _heroes((1, "刘备")))

    dialog = BackupRestoreDialog(lambda: False, data_dir=tmp_path)

    assert dialog._table.rowCount() == 1  # 仅当前文件行
    assert "暂无自动备份" in dialog._preview_label.text()
