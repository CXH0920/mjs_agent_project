"""官方榜单待复核修正对话框测试。"""

from __future__ import annotations

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.business.recognition import official_data_import_service as import_module
from src.business.recognition.official_data_import_service import OfficialDataImportService
from src.ui.data_admin.official_import_review_dialog import OfficialImportReviewDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_review_candidates_include_near_roster_names() -> None:
    service = OfficialDataImportService(hero_names=["夏侯惇", "白起"])

    candidates = service.review_candidates("夏候怀", None)

    assert "夏侯惇" in candidates
    assert candidates[0] == "夏侯惇"


def test_review_dialog_applies_corrections_and_emits_applied(tmp_path, monkeypatch) -> None:
    _app()
    pending = {
        "key": "2v2",
        "outputs": {
            "2v2胜率排行.csv": {
                "review_name": "2v2胜率排行_待复核.csv",
                "records": [{"排名": 1, "武将": "夏候", "胜率": "54.40%"}],
                "reviews": [{
                    "期望排名": 1,
                    "OCR名称": "夏候",
                    "置信度": "0.7960",
                    "异常原因": "武将名称未命中词表",
                    "行截图路径": "",
                }],
            },
        },
    }
    monkeypatch.setattr(
        OfficialDataImportService,
        "_load_hero_names",
        staticmethod(lambda: ["夏侯惇"]),
    )
    monkeypatch.setattr(import_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(import_module, "clear_pending_session", lambda *_: None)
    monkeypatch.setattr(import_module, "mark_recommendation_index_stale", lambda *_: None)
    monkeypatch.setattr("src.data.win_rate_repository.clear_win_rate_cache", lambda: None)

    dialog = OfficialImportReviewDialog(pending)
    combo = dialog._table.cellWidget(0, 3)
    combo.setCurrentText("夏侯惇")
    emitted: list = []
    dialog.applied.connect(lambda: emitted.append(1))

    dialog._accept()

    rows = list(csv.DictReader((tmp_path / "2v2胜率排行.csv").open(encoding="utf-8")))
    assert rows[0]["武将"] == "夏侯惇"
    assert emitted == [1]
