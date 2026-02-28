"""Tests for the read_source_context helper."""

import os
import tempfile

import pytest

from sbl_debugger.tools.inspection import read_source_context


@pytest.fixture
def source_file():
    """Create a temporary source file with known content."""
    content = """\
#include <stdio.h>

int main() {
    int x = 42;
    printf("hello %d\\n", x);
    return 0;
}
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".c", delete=False
    ) as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


class TestReadSourceContext:
    def test_returns_lines_around_target(self, source_file):
        result = read_source_context(source_file, line=4, context=2)
        assert result is not None
        assert len(result) == 5  # lines 2-6
        assert result[0]["line"] == 2
        assert result[2]["line"] == 4
        assert result[2]["current"] is True
        assert result[2]["text"] == "    int x = 42;"

    def test_current_marker_only_on_target_line(self, source_file):
        result = read_source_context(source_file, line=4, context=1)
        assert result is not None
        for entry in result:
            if entry["line"] == 4:
                assert entry["current"] is True
            else:
                assert "current" not in entry

    def test_clamps_to_file_start(self, source_file):
        result = read_source_context(source_file, line=1, context=2)
        assert result is not None
        assert result[0]["line"] == 1
        assert result[0]["current"] is True

    def test_clamps_to_file_end(self, source_file):
        result = read_source_context(source_file, line=7, context=2)
        assert result is not None
        assert result[-1]["line"] == 7
        assert result[-1]["current"] is True

    def test_returns_none_for_missing_file(self):
        result = read_source_context("/nonexistent/file.c", line=1)
        assert result is None

    def test_returns_none_for_none_file(self):
        result = read_source_context(None, line=1)
        assert result is None

    def test_returns_none_for_none_line(self, source_file):
        result = read_source_context(source_file, line=None)
        assert result is None

    def test_custom_context_size(self, source_file):
        result = read_source_context(source_file, line=4, context=0)
        assert result is not None
        assert len(result) == 1
        assert result[0]["line"] == 4
        assert result[0]["current"] is True

    def test_strips_line_endings(self, source_file):
        result = read_source_context(source_file, line=4)
        assert result is not None
        for entry in result:
            assert not entry["text"].endswith("\n")
            assert not entry["text"].endswith("\r")
