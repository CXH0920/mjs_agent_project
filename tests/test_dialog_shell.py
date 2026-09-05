"""阶段 7 弹窗外壳、反馈语义与缩放回归测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from src.business.card_catalog import CardCatalogService
from src.data.card_catalog import CardAnnotationRepository, CardFieldSchemaRepository, CardRepository
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide
from src.data.synergy_manager import SynergyManager
from src.ui.configuration.faction_color_dialog import FactionColorDialog
from src.ui.configuration.mumu_config_dialog import MumuConfigDialog
from src.ui.configuration.roi_selector import RoiSelectorDialog
from src.ui.configuration.settings_dialog import SettingsDialog
from src.ui.data_admin.data_management_dialog import DataManagementDialog
from src.ui.data_admin.official_data_import_dialog import OfficialDataImportDialog
from src.ui.generation.backend_choose_dialog import BackendChooseDialog
from src.ui.generation.guide_progress_dialog import GuideProgressDialog
from src.ui.library.card_management_panel import CardAnnotationEditDialog
from src.ui.library.hero_edit_dialog import HeroEditDialog
from src.ui.shared.guide_detail_dialog import GuideDetailDialog
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog
from src.ui.shared.style import GLOBAL_STYLE, ROLE_DANGER, ROLE_PRIMARY, ROLE_SECONDARY
from src.ui.shared.widgets import DialogFooter, EmptyState, PageHeader, show_toast


def _app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(GLOBAL_STYLE)
    return app


class _CaptureService(QObject):
    official_import_progress = Signal(str, int, int)
    official_import_completed = Signal(object)
    official_import_failed = Signal(str)


class _MumuCaptureService(QObject):
    connection_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.connection_state = ("disconnected", "")
        self.capture = None

    def update_config(self, _config: dict) -> None:
        pass


class _MumuOperationService(QObject):
    adb_detected = Signal(bool, str, str)
    devices_refreshed = Signal(object)
    device_refresh_failed = Signal(str)
    connection_finished = Signal(bool, str)
    disconnection_finished = Signal(bool, str)
    device_tested = Signal(bool, str, str)
    screenshot_ready = Signal(str, object)
    screenshot_failed = Signal(str, str)
    operation_failed = Signal(str, str)

    def refresh_devices(self) -> None:
        self.devices_refreshed.emit([])

    def shutdown(self) -> None:
        pass


class _MumuOcrService:
    poll_state = "stopped"

    def __init__(self, root: Path) -> None:
        self._root = root

    def is_template_loaded(self, _template_name: str = "hero_selection") -> bool:
        return False

    def template_path(self, template_name: str = "hero_selection") -> Path:
        return self._root / f"{template_name}.png"


def _assert_dialog_shell(dialog, app: QApplication) -> None:
    dialog.show()
    app.processEvents()
    header = dialog.findChild(PageHeader)
    footer = dialog.findChild(DialogFooter)
    assert header is not None and header.isVisibleTo(dialog)
    assert footer is not None and footer.isVisibleTo(dialog)
    assert header.geometry().bottom() < footer.geometry().top()
    assert footer.geometry().bottom() <= dialog.contentsRect().bottom()
    assert footer.accept_button.property("uiRole") in (ROLE_PRIMARY, ROLE_SECONDARY)
    for button in (footer.cancel_button, footer.accept_button):
        if button.isVisibleTo(dialog):
            assert button.fontMetrics().horizontalAdvance(button.text()) < button.width()
    dialog.hide()


def test_representative_dialog_shells_keep_header_and_footer_visible(tmp_path) -> None:
    app = _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    guide = HeroGuide(hero_id=1, description="# 完整攻略\n正文")
    (tmp_path / "cards.json").write_text(
        '[{"id":"8","name":"冲杀","card_type":"行动牌","card_desc":"伤害"}]',
        encoding="utf-8",
    )
    (tmp_path / "card_field_schema.json").write_text(
        '{"schema_version":1,"fields":[{"key":"note","label":"补充说明","value_type":"markdown"}]}',
        encoding="utf-8",
    )
    (tmp_path / "card_annotations.json").write_text(
        '{"schema_version":1,"annotations":[]}',
        encoding="utf-8",
    )
    card_service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    card_service.load_all()
    dialogs = [
        SettingsDialog(tmp_path / "config.env", pricing_path=tmp_path / "pricing.json"),
        FactionColorDialog(tmp_path / "faction_colors.json"),
        MumuConfigDialog(
            {
                "mumu_adb_path": "adb.exe", "mumu_adb_port": 0,
                "mumu_ocr_enabled": False, "mumu_ocr_poll_mode": False,
                "mumu_ocr_auto_switch_tab": False, "mumu_ocr_poll_interval": 2,
                "mumu_ocr_match_threshold": 0.8, "mumu_hero_selection_threshold": 0.8,
                "mumu_hero_selection_cooldown": 180, "mumu_match_guide_threshold": 0.8,
                "mumu_ocr_use_gpu": False, "mumu_ocr_cpu_threads": 6,
            },
            capture_service=_MumuCaptureService(),
            ocr_service=_MumuOcrService(tmp_path),
            operation_service=_MumuOperationService(),
        ),
        RoiSelectorDialog(QPixmap(1280, 720)),
        BackendChooseDialog(),
        GuideProgressDialog(8),
        HeroEditDialog(Hero(id=1, name="超长测试武将名称", faction="测试势力")),
        CardAnnotationEditDialog(card_service, "8"),
        GuideDetailDialog("曹操", guide, hero_manager),
        OfficialDataImportDialog(_CaptureService()),
    ]

    for dialog in dialogs:
        _assert_dialog_shell(dialog, app)

    settings_footer = dialogs[0].findChild(DialogFooter)
    settings_footer.set_busy(True, "正在保存完整配置...")
    _assert_dialog_shell(dialogs[0], app)
    settings_footer.set_busy(False)

    for width, height in ((960, 640), (1100, 760), (1440, 900)):
        for dialog in dialogs:
            dialog.resize(width, height)
            _assert_dialog_shell(dialog, app)


def test_dangerous_data_action_has_distinct_role_and_requires_selection(tmp_path) -> None:
    _app()
    dialog = DataManagementDialog(
        GuideManager(tmp_path / "guides.json"),
        SynergyManager(tmp_path / "synergies.json"),
        lambda: False,
    )

    assert dialog._clear_button.property("uiRole") == ROLE_DANGER
    assert dialog._footer.cancel_button.property("uiRole") == ROLE_SECONDARY
    assert not dialog._clear_button.isEnabled()

    dialog._guide_checkbox.setChecked(True)

    assert dialog._clear_button.isEnabled()


def test_empty_hero_selection_uses_explicit_empty_state(tmp_path) -> None:
    _app()
    dialog = BaseHeroSelectDialog(HeroManager(tmp_path / "heroes.json"))

    assert dialog.findChild(PageHeader) is not None
    assert dialog.findChild(EmptyState) is not None
    footer = dialog.findChild(DialogFooter)
    assert footer is not None
    assert footer.accept_button.text() == "关闭"
    assert footer.cancel_button.isHidden()


def test_long_toast_wraps_inside_parent_and_remains_non_blocking() -> None:
    app = _app()
    parent = BackendChooseDialog()
    parent.resize(480, 360)
    parent.show()
    app.processEvents()

    toast = show_toast(parent, "配置已保存。" * 40, duration=10_000)
    app.processEvents()

    assert toast.wordWrap()
    assert toast.width() <= parent.width() - 48
    assert toast.geometry().right() < parent.width()
    assert toast.geometry().bottom() < parent.height()
    assert parent.isEnabled()
    parent.hide()
