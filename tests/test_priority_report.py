"""JUnit report parsing accepts ordinary results and rejects DTD declarations."""

from pathlib import Path

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("defusedxml")
from defusedxml.common import DTDForbidden
from scripts.build_priority_report import build

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "static_priority.json"


@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE testsuites>',
    '<!DOCTYPE testsuites [<!ENTITY label "expanded">]>',
    '<!DOCTYPE testsuites SYSTEM "file:///not-a-real-junit-dtd">',
])
def test_report_rejects_dtd_and_entities(tmp_path, declaration):
    junit = tmp_path / "junit.xml"
    junit.write_text(declaration + '<testsuites><testsuite><testcase/></testsuite></testsuites>', encoding="utf-8")
    with pytest.raises(DTDForbidden):
        build(tmp_path / "report.pdf", FIXTURE, junit)


@pytest.mark.parametrize("failed", [False, True])
def test_report_accepts_plain_junit_and_preserves_failure_guard(tmp_path, failed):
    junit = tmp_path / "junit.xml"
    failure = '<failure message="a &amp; b"/>' if failed else ''
    junit.write_text(f'<?xml version="1.0" encoding="UTF-8"?><testsuites><testsuite><testcase name="Cruïlles">{failure}</testcase></testsuite></testsuites>',
                     encoding="utf-8")
    output = tmp_path / "report.pdf"
    if failed:
        with pytest.raises(ValueError, match="Test results contain failures"):
            build(output, FIXTURE, junit)
    else:
        build(output, FIXTURE, junit)
        assert output.read_bytes().startswith(b"%PDF-")
