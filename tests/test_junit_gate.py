"""Unit tests for JUnit skip≠pass gate helper."""

from __future__ import annotations

from pathlib import Path

from tests.junit_gate import summarize


def test_summarize_testsuites(tmp_path: Path):
    xml = tmp_path / "j.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="a" tests="2" failures="0" errors="0" skipped="0"/>
</testsuites>
""",
        encoding="utf-8",
    )
    assert summarize(xml) == (2, 0, 0, 0)


def test_summarize_detects_skipped(tmp_path: Path):
    xml = tmp_path / "j.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="a" tests="1" failures="0" errors="0" skipped="1"/>
""",
        encoding="utf-8",
    )
    assert summarize(xml) == (1, 0, 0, 1)
