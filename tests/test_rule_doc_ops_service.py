"""名将杀 Agent - 元规则确认行校验与确认清单落盘测试"""

import json
from pathlib import Path

from src.business.maintenance.rule_doc_ops_service import (
    save_confirmed_diffs,
    validate_confirmed_row,
)


class TestValidateConfirmedRow:
    """validate_confirmed_row 校验规则"""

    def test_empty_value_rejected_with_section(self):
        diff = {"section": "3", "old": "|旧|值|"}
        message = validate_confirmed_row(diff, "")
        assert message is not None
        assert "确认值为空" in message
        assert "段 3" in message

    def test_missing_section_key_defaults_to_empty(self):
        """diff 无 section 键时按空段名提示，不抛异常。"""
        message = validate_confirmed_row({}, "")
        assert message is not None
        assert "确认值为空" in message

    def test_row_without_leading_pipe_rejected(self):
        message = validate_confirmed_row({"section": "1"}, "a|b|")
        assert message is not None
        assert "不是完整表格行" in message

    def test_row_without_trailing_pipe_rejected(self):
        message = validate_confirmed_row({"section": "1"}, "|a|b")
        assert message is not None
        assert "不是完整表格行" in message

    def test_column_count_mismatch_rejected(self):
        """old="|a|b|" 按 "|" 切 4 段，new 列数不同则拒绝。"""
        diff = {"section": "2", "old": "|刘备|相性|"}
        message = validate_confirmed_row(diff, "|仅一列|")
        assert message is not None
        assert "列数与原文不一致" in message

    def test_matching_columns_pass(self):
        diff = {"section": "2", "old": "|刘备|相性|"}
        assert validate_confirmed_row(diff, "|关羽|技策|") is None

    def test_column_check_skipped_without_old(self):
        """新增行（无 old）只校验表格行格式，不校验列数。"""
        assert validate_confirmed_row({"section": "2"}, "|任意|列数|可以|") is None

    def test_empty_old_string_skips_column_check(self):
        """old 为空字符串视为无原文，跳过列数校验。"""
        diff = {"section": "2", "old": ""}
        assert validate_confirmed_row(diff, "|一列|") is None


class TestSaveConfirmedDiffs:
    """save_confirmed_diffs 确认清单落盘"""

    def test_writes_to_confirmed_diff_path(self, tmp_path: Path):
        rows = [{"section": "1", "old": "|a|", "new": "|b|"}]
        path = save_confirmed_diffs(tmp_path, rows)

        assert path == tmp_path / "src" / "scripts" / ".sync_confirmed_diffs.json"
        assert path.parent.exists()  # 多级子目录自动创建
        assert json.loads(path.read_text(encoding="utf-8")) == rows

    def test_overwrites_existing_file(self, tmp_path: Path):
        stale = [{"section": "9", "old": "|x|", "new": "|y|"}]
        save_confirmed_diffs(tmp_path, stale)

        fresh = [{"section": "1", "old": "|a|", "new": "|b|"}]
        path = save_confirmed_diffs(tmp_path, fresh)

        assert json.loads(path.read_text(encoding="utf-8")) == fresh

    def test_empty_rows_still_writes_file(self, tmp_path: Path):
        path = save_confirmed_diffs(tmp_path, [])
        assert json.loads(path.read_text(encoding="utf-8")) == []
