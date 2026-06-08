import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from ui.settings_dialog import _parse_env_file, _save_env_file


class TestEnvFileParsing:
    def test_parse_basic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text("KEY=val\nFOO=bar\n", encoding="utf-8")
            result = _parse_env_file(env_path)
            assert result["KEY"] == "val"

    def test_parse_nonexistent(self):
        result = _parse_env_file(Path("/x/config.env"))
        assert result == {}

    def test_parse_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "x.env"
            env_path.write_text("", encoding="utf-8")
            result = _parse_env_file(env_path)
            assert result == {}

    def test_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "c.env"
            _save_env_file(env_path, {"K": "v"})
            assert env_path.exists()
            assert not env_path.with_suffix(".env.tmp").exists()
