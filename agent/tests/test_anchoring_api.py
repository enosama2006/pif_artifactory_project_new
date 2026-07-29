# -*- coding: utf-8 -*-
"""The add-in path: anchored pkg:package OOXML → POST /runs → anchored payload.

Simulates exactly what taskpane.js sends: a Word XML package whose paragraphs
are wrapped in content controls tagged anz:C_NNNNN (the anchoring pass), and
asserts the payload comes back keyed by those anchors so the add-in can apply
via getByTag — no text search anywhere.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion import ingest

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _sdt(tag, inner):
    return (f'<w:sdt><w:sdtPr><w:tag w:val="{tag}"/></w:sdtPr>'
            f"<w:sdtContent>{inner}</w:sdtContent></w:sdt>")


def _p(text, style=None):
    st = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{st}<w:r><w:t>{text}</w:t></w:r></w:p>"


PKG = f"""<?xml version="1.0"?>
<pkg:package xmlns:pkg="http://schemas.microsoft.com/office/2006/xmlPackage">
<pkg:part pkg:name="/word/document.xml"><pkg:xmlData>
<w:document {W_NS}><w:body>
{_sdt("anz:C_00001", _p("سياسة أمن المعلومات", "Title"))}
{_sdt("anz:C_00002", _p("أعدّه: أحمد عبدالرحمن"))}
{_sdt("anz:C_00003", _p("تحدد هذه السياسة التزامات صندوق الاستثمارات العامة."))}
<w:tbl><w:tr><w:tc>{_p("القرار")}</w:tc></w:tr>
<w:tr><w:tc>{_sdt("anz:C_00004", _p("قرار رقم 47"))}</w:tc></w:tr></w:tbl>
</w:body></w:document>
</pkg:xmlData></pkg:part></pkg:package>"""


def test_pkg_package_parsed_with_anchors():
    usd = ingest(PKG.encode("utf-8"), "ooxml")
    by_anchor = {l.anchor: l for l in usd.leaves if l.anchor}
    assert set(by_anchor) == {"anz:C_00001", "anz:C_00002", "anz:C_00003", "anz:C_00004"}
    assert by_anchor["anz:C_00004"].kind == "table_cell"     # sdt inside a cell
    assert by_anchor["anz:C_00001"].kind == "title"


def test_api_run_returns_anchored_payload():
    httpx = pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from app.api.routes import app  # noqa: F401

    client = TestClient(app)
    assert client.get("/health").json()["ok"] is True

    # wait=1 runs inline (the add-in instead polls GET /runs/{id} for progress)
    r = client.post("/runs?wait=1", content=PKG.encode("utf-8"),
                    headers={"Content-Type": "application/xml"}).json()
    assert r["ok"] is True
    assert r["status"] == "completed"
    assert [s["stage"] for s in r["stages"]] == [
        "ingest", "portrait", "inventory", "surface_scan",
        "classify_rules", "decide", "assemble"]
    m = r["result"]["metrics"]
    assert m["coverage"] == 1.0 and m["silent_losses"] == 0

    # live-status endpoint reflects the finished run
    status = client.get(f"/runs/{r['run_id']}").json()
    assert status["status"] == "completed"

    iv = client.post(f"/runs/{r['run_id']}/interventions",
                     json={"type": "annotate", "target": "L_000001",
                           "payload": {}, "note": "test"}).json()
    assert iv["ok"] is True
    for p in r["result"]["payload"]:
        assert "after" in p and "before" in p and "anchor" in p
