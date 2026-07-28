"""OOXML → UnifiedDocument.

Covers RISK #2 (docs/RISKS.md): walks document.xml AND header*/footer*/
footnotes parts of a .docx — the org name usually lives in the page header,
and no v1–v10 generation ever read those parts.

Accepted inputs (auto-detected):
  - .docx zip bytes
  - raw word/document.xml
  - Word XML package (`<pkg:package>` — what Office.js `getOoxml()` returns)

Anchoring: if the add-in wrapped paragraphs in content controls tagged
`anz:...` (w:sdt/w:sdtPr/w:tag), each leaf carries that tag in `anchor`, and
the final payload applies by anchor — never by text search.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .._contract import Leaf, UnifiedDocument

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Word content-control prompt strings — UI chrome, not document content.
_PLACEHOLDER_TEXTS = {
    "Click or tap here to enter text.",
    "Click here to enter text.",
    "Choose an item.",
    "Click or tap to enter a date.",
}


class OoxmlBlock:
    def to_usd(self, raw: bytes) -> UnifiedDocument:
        parts: list[tuple[str, bytes]] = []  # (origin, xml bytes)
        if raw[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    if name == "word/document.xml":
                        parts.append(("body", z.read(name)))
                    elif re.match(r"word/header\d*\.xml", name):
                        parts.append(("page_header", z.read(name)))
                    elif re.match(r"word/footer\d*\.xml", name):
                        parts.append(("page_footer", z.read(name)))
                    elif name == "word/footnotes.xml":
                        parts.append(("footnote", z.read(name)))
        else:
            text = raw.decode("utf-8", errors="replace")
            if "<pkg:package" in text[:2000]:
                # Office.js getOoxml() envelope — slice out the main document
                m = re.search(r"<w:document[\s\S]*?</w:document>", text)
                if not m:
                    raise ValueError("pkg:package without a w:document part")
                parts.append(("body", m.group(0).encode("utf-8")))
            else:
                parts.append(("body", raw))

        usd = UnifiedDocument(source_format="ooxml")
        n = 0
        section = "root"
        sec_idx = 0
        for origin, xml_bytes in sorted(parts, key=lambda p: p[0] != "body"):
            root = ET.fromstring(xml_bytes)
            # ElementTree has no parent pointers — precompute per-part maps:
            table_ps = {id(p) for tbl in root.iter(f"{W}tbl") for p in tbl.iter(f"{W}p")}
            anchors = _anchor_map(root)
            done_tbls: set[int] = set()  # nested tables are handled by their outer table
            for el in root.iter():
                if el.tag == f"{W}tbl":
                    if id(el) in done_tbls:
                        continue
                    for t2 in el.iter(f"{W}tbl"):
                        done_tbls.add(id(t2))
                    n = self._table(el, usd, n, section, anchors)
                elif el.tag == f"{W}p" and id(el) not in table_ps:
                    text = _p_text(el)
                    if not text.strip() or text.strip() in _PLACEHOLDER_TEXTS:
                        continue
                    style = _p_style(el)
                    if origin != "body":
                        kind = origin
                    elif style == "Title":
                        kind = "title"
                    elif (style.startswith("Heading") or "heading" in style.lower()
                          or (_p_outline_level(el) is not None
                              and len(text.strip()) <= 100)):
                        # custom templates rarely use the stock "Heading N"
                        # styles — w:outlineLvl marks a heading regardless of
                        # the style name. BUT numbered policy clauses also
                        # carry outlineLvl (run 3e6163a5156e: 60 "headings",
                        # full paragraphs among them, 61 tiny inventory
                        # chunks) — a heading is SHORT, so outlineLvl only
                        # counts for texts ≤100 chars.
                        kind = "heading"
                        sec_idx += 1
                        section = f"s{sec_idx}"
                    else:
                        kind = "paragraph"
                    n += 1
                    usd.leaves.append(Leaf(f"L_{n:06d}", kind, text, section,
                                           anchor=anchors.get(id(el))))
        _drop_aggregate_duplicates(usd.leaves)
        return usd

    def _table(self, tbl, usd: UnifiedDocument, n: int, section: str, anchors):
        """One table → leaves. Word wraps rows/cells in w:sdt freely (form
        tables, repeating sections — real-run finding: ZERO table leaves on a
        table-heavy document), so rows and cells are collected with .iter and
        nested-table content is excluded explicitly."""
        t_id = f"t{len({(l.row or ':').split('r')[0] for l in usd.leaves if l.row}) + 1}"
        inner = [t2 for t2 in tbl.iter(f"{W}tbl") if t2 is not tbl]
        inner_rows = {id(r) for t2 in inner for r in t2.iter(f"{W}tr")}
        inner_cells = {id(c) for t2 in inner for c in t2.iter(f"{W}tc")}
        inner_ps = {id(p) for t2 in inner for p in t2.iter(f"{W}p")}

        headers: list[str] = []
        rows = [r for r in tbl.iter(f"{W}tr") if id(r) not in inner_rows]
        for r_i, tr in enumerate(rows):
            cells = [c for c in tr.iter(f"{W}tc") if id(c) not in inner_cells]
            for c_i, tc in enumerate(cells):
                # .iter — paragraphs may sit inside w:sdt; a nested table's
                # paragraphs belong to ITS cells, not to this one
                cell_ps = [p for p in tc.iter(f"{W}p") if id(p) not in inner_ps]
                text = " ".join(_p_text(p) for p in cell_ps).strip()
                if not text or text in _PLACEHOLDER_TEXTS:
                    continue
                if r_i == 0:
                    headers.append(text)
                    kind, col = "table_header_cell", text
                else:
                    kind = "table_cell"
                    col = headers[c_i] if c_i < len(headers) else None
                anchor = next((anchors[id(p)] for p in cell_ps if id(p) in anchors), None)
                n += 1
                usd.leaves.append(Leaf(f"L_{n:06d}", kind, text, section,
                                       row=f"{t_id}r{r_i}", col=col, anchor=anchor))

        # nested tables are real tables too — recurse into the DIRECT children
        nested_of_inner = {id(x) for t2 in inner for x in t2.iter(f"{W}tbl") if x is not t2}
        for t2 in inner:
            if id(t2) not in nested_of_inner:
                n = self._table(t2, usd, n, section, anchors)
        return n


def _anchor_map(root) -> dict[int, str]:
    """{id(w:p): 'anz:…'} for every paragraph inside a tagged content control.

    INNERMOST tag wins: root.iter yields outer sdts before the sdts nested
    inside them, so plain assignment (not setdefault) leaves each paragraph
    with its deepest anchor. Cover pages nest paragraphs inside an outer
    docPart sdt — mapping them all to the outer tag made several leaves share
    one anchor and applying them clobbered each other (run 646d065f6ea4).
    """
    out: dict[int, str] = {}
    for sdt in root.iter(f"{W}sdt"):
        pr = sdt.find(f"{W}sdtPr")
        tag_el = pr.find(f"{W}tag") if pr is not None else None
        tag = tag_el.get(f"{W}val", "") if tag_el is not None else ""
        if not tag.startswith("anz:"):
            continue
        for p in sdt.iter(f"{W}p"):
            out[id(p)] = tag
    return out


def _drop_aggregate_duplicates(leaves, window: int = 12) -> None:
    """Drop a leaf whose text is exactly the concatenation of the next 2+ leaves.

    Word cover pages surface the same content twice: once as a run-concatenated
    aggregate paragraph and again as the individual paragraphs. Keeping both
    double-counts every mention and produces conflicting rewrites.
    """
    i = 0
    while i < len(leaves):
        agg = leaves[i].text
        joined = ""
        j = i + 1
        while j < len(leaves) and j <= i + window and len(joined) < len(agg):
            joined += leaves[j].text
            j += 1
            if joined == agg and j - i - 1 >= 2:
                del leaves[i]
                break
        else:
            i += 1


def _p_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(f"{W}t"))


def _p_style(p) -> str:
    ppr = p.find(f"{W}pPr")
    if ppr is not None:
        st = ppr.find(f"{W}pStyle")
        if st is not None:
            return st.get(f"{W}val", "")
    return ""


def _p_outline_level(p):
    """w:outlineLvl value (0-8) if the paragraph is outline-marked, else None."""
    ppr = p.find(f"{W}pPr")
    if ppr is not None:
        lvl = ppr.find(f"{W}outlineLvl")
        if lvl is not None:
            try:
                return int(lvl.get(f"{W}val", ""))
            except ValueError:
                return None
    return None
