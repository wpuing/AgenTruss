"""Parse JUnit XML for CI skip≠pass gates."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(path: Path) -> tuple[int, int, int, int]:
    """Return (tests, failures, errors, skipped)."""
    root = ET.parse(path).getroot()
    # pytest may emit <testsuites> or bare <testsuite>
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if root.tag == "testsuites" and not suites:
        suites = list(root)
    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.attrib.get("tests") or 0)
        failures += int(suite.attrib.get("failures") or 0)
        errors += int(suite.attrib.get("errors") or 0)
        skipped += int(suite.attrib.get("skipped") or 0)
    return tests, failures, errors, skipped


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: junit_gate.py <junit.xml>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"FAIL: missing junit report {path}", file=sys.stderr)
        return 1
    tests, failures, errors, skipped = summarize(path)
    print(f"junit tests={tests} failures={failures} errors={errors} skipped={skipped}")
    if tests <= 0:
        print("FAIL: zero tests executed", file=sys.stderr)
        return 1
    if skipped > 0:
        print("FAIL: required integration job must have skipped=0", file=sys.stderr)
        return 1
    if failures > 0 or errors > 0:
        print("FAIL: junit reported failures/errors", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
