# -*- coding: utf-8 -*-
"""Ingestion tests — including the RISK #2 case: text in page headers."""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion import ingest

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text, style=None):
    st = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{st}<w:r><w:t>{text}</w:t></w:r></w:p>"


DOC_XML = f"""<w:document {W_NS}><w:body>
{_p("سياسة أمن المعلومات", "Title")}
{_p("1. الغرض", "Heading1")}
{_p("تلتزم الجهة بحماية المعلومات.")}
<w:tbl>
  <w:tr><w:tc>{_p("القرار")}</w:tc><w:tc>{_p("التاريخ")}</w:tc></w:tr>
  <w:tr><w:tc>{_p("قرار رقم 47")}</w:tc><w:tc>{_p("12/3/1445")}</w:tc></w:tr>
</w:tbl>
</w:body></w:document>"""

HEADER_XML = f"""<w:hdr {W_NS}>{_p("صندوق الاستثمارات العامة — سري")}</w:hdr>"""


def make_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", DOC_XML)
        z.writestr("word/header1.xml", HEADER_XML)
    return buf.getvalue()


def test_docx_body_kinds_and_table_semantics():
    usd = ingest(make_docx(), "docx")
    by_kind = {}
    for l in usd.leaves:
        by_kind.setdefault(l.kind, []).append(l)

    assert [l.text for l in by_kind["title"]] == ["سياسة أمن المعلومات"]
    assert [l.text for l in by_kind["heading"]] == ["1. الغرض"]
    cells = by_kind["table_cell"]
    assert {c.text for c in cells} == {"قرار رقم 47", "12/3/1445"}
    assert all(c.row == "t1r1" for c in cells)
    assert {c.col for c in cells} == {"القرار", "التاريخ"}   # header semantics carried


def test_page_header_text_becomes_a_leaf():
    """The org name in the page header must NOT be invisible (RISK #2)."""
    usd = ingest(make_docx(), "docx")
    headers = [l for l in usd.leaves if l.kind == "page_header"]
    assert len(headers) == 1
    assert "صندوق الاستثمارات العامة" in headers[0].text


def test_raw_document_xml_accepted():
    usd = ingest(DOC_XML.encode("utf-8"), "ooxml")
    assert usd.leaf_count > 0
    assert not [l for l in usd.leaves if l.kind == "page_header"]
