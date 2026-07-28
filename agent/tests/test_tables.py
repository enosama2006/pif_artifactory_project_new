# -*- coding: utf-8 -*-
"""Tables are text (owner's rules): sdt-wrapped rows/cells must extract,
the row is the atomic batching unit, and header rows ride along as context."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion import ingest
from app.ingestion._contract import Leaf
from app.llm.client import StubLlm
from app.pipeline.batching import plan_batches
from app.pipeline.batching.plan import build_table_headers
from _adk.stages import decide_stage

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text):
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _sdt(inner, tag="anz:C_00001"):
    return (f'<w:sdt><w:sdtPr><w:tag w:val="{tag}"/></w:sdtPr>'
            f"<w:sdtContent>{inner}</w:sdtContent></w:sdt>")


def test_sdt_wrapped_rows_and_cells_extract():
    """Word form tables wrap rows/cells in w:sdt — findall missed them all
    (real runs: ZERO table leaves on a table-heavy document)."""
    data_row = ("<w:tr><w:tc>" + _p("PIF") + "</w:tc><w:tc>"
                + _p("Public Investment Fund") + "</w:tc></w:tr>")
    doc = (f'<w:document {W_NS}><w:body><w:tbl>'
           f'<w:tr><w:tc>{_p("Abbreviation")}</w:tc><w:tc>{_p("Definition")}</w:tc></w:tr>'
           f'{_sdt(data_row, "anz:C_00002")}'
           f'</w:tbl></w:body></w:document>')
    usd = ingest(doc.encode(), "ooxml")
    kinds = {l.kind for l in usd.leaves}
    assert "table_header_cell" in kinds and "table_cell" in kinds
    cells = [l for l in usd.leaves if l.kind == "table_cell"]
    assert {c.text for c in cells} == {"PIF", "Public Investment Fund"}
    assert all(c.row == "t1r1" for c in cells)


def test_nested_table_not_double_extracted():
    inner = (f'<w:tbl><w:tr><w:tc>{_p("inner cell")}</w:tc></w:tr></w:tbl>')
    doc = (f'<w:document {W_NS}><w:body><w:tbl>'
           f'<w:tr><w:tc>{_p("outer cell")}{inner}</w:tc></w:tr>'
           f'</w:tbl></w:body></w:document>')
    usd = ingest(doc.encode(), "ooxml")
    texts = [l.text for l in usd.leaves]
    assert texts.count("inner cell") == 1
    assert texts.count("outer cell") == 1


def make_table_leaves():
    leaves = [Leaf("L_000001", "heading", "Approval", "s1")]
    n = 1
    for h_i, h in enumerate(["Decision", "Date", "Owner"]):
        n += 1
        leaves.append(Leaf(f"L_{n:06d}", "table_header_cell", h, "s1",
                           row="t1r0", col=h))
    for r in range(1, 8):
        for c, col in enumerate(["Decision", "Date", "Owner"]):
            n += 1
            leaves.append(Leaf(f"L_{n:06d}", "table_cell", f"value {r}-{c} " + "x" * 30,
                               "s1", row=f"t1r{r}", col=col))
    return leaves


def test_rows_are_atomic_in_batches():
    leaves = make_table_leaves()
    batches = plan_batches(leaves, max_chars=300, max_leaves=4)  # tiny budget: 3-cell rows must still hold
    placed = [i for b in batches for i in b.leaf_ids]
    assert len(placed) == len(leaves) and set(placed) == {l.leaf_id for l in leaves}
    leaf_by_id = {l.leaf_id: l for l in leaves}
    for b in batches:                       # a row never straddles two batches
        rows_here = {leaf_by_id[i].row for i in b.leaf_ids if leaf_by_id[i].row}
        for other in batches:
            if other is b:
                continue
            rows_there = {leaf_by_id[i].row for i in other.leaf_ids if leaf_by_id[i].row}
            assert not (rows_here & rows_there)


def test_decide_payload_carries_table_headers_and_columns():
    leaves = make_table_leaves()
    seen = []

    class Spy(StubLlm):
        def json_call(self, prompt, *, payload=None, **kw):
            if (payload or {}).get("task") == "decide":
                seen.append(payload)
            return super().json_call(prompt, payload=payload, **kw)

    state = {"leaves": [l.__dict__ for l in leaves], "actors": {},
             "links": [], "cascade": []}
    result = asyncio.run(decide_stage(state, Spy()))
    assert len(result["delta"]["decisions"]) == len(leaves)
    assert build_table_headers(leaves) == {"t1": ["Decision", "Date", "Owner"]}
    for p in seen:
        cell_leaves = [l for l in p["leaves"] if l.get("row")]
        if cell_leaves:                      # header context rides with the rows
            assert p["table_headers"].get("t1") == ["Decision", "Date", "Owner"]
            assert all(l.get("column") for l in cell_leaves)
