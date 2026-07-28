"""OOXML → UnifiedDocument.

Covers RISK #2 (docs/RISKS.md): walks document.xml AND header*/footer*/
footnotes parts of a .docx — the org name usually lives in the page header,
and no v1–v10 generation ever read those parts.

Raw document.xml input (no zip) is also accepted for the add-in path, where
the client sends the package it extracted itself.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .._contract import Leaf, UnifiedDocument

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


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
            parts.append(("body", raw))

        usd = UnifiedDocument(source_format="ooxml")
        n = 0
        section = "root"
        sec_idx = 0
        for origin, xml_bytes in sorted(parts, key=lambda p: p[0] != "body"):
            root = ET.fromstring(xml_bytes)
            # ElementTree has no parent pointers; table paragraphs are handled
            # by _table(), so collect their ids once and skip them in the walk.
            table_ps = {id(p) for tbl in root.iter(f"{W}tbl") for p in tbl.iter(f"{W}p")}
            for el in root.iter():
                if el.tag == f"{W}tbl":
                    n = self._table(el, usd, n, section)
                elif el.tag == f"{W}p" and id(el) not in table_ps:
                    text = _p_text(el)
                    if not text.strip():
                        continue
                    style = _p_style(el)
                    if origin != "body":
                        kind = origin
                    elif style.startswith("Heading") or style == "Title":
                        kind = "title" if style == "Title" else "heading"
                        if kind == "heading":
                            sec_idx += 1
                            section = f"s{sec_idx}"
                    else:
                        kind = "paragraph"
                    n += 1
                    usd.leaves.append(Leaf(f"L_{n:06d}", kind, text, section))
        return usd

    def _table(self, tbl, usd: UnifiedDocument, n: int, section: str):
        t_id = f"t{sum(1 for l in usd.leaves if l.row and l.row.endswith('r0')) + 1}"
        headers: list[str] = []
        for r_i, tr in enumerate(tbl.findall(f"{W}tr")):
            for c_i, tc in enumerate(tr.findall(f"{W}tc")):
                text = " ".join(_p_text(p) for p in tc.findall(f"{W}p")).strip()
                if not text:
                    continue
                if r_i == 0:
                    headers.append(text)
                    kind, col = "table_header_cell", text
                else:
                    kind = "table_cell"
                    col = headers[c_i] if c_i < len(headers) else None
                n += 1
                usd.leaves.append(Leaf(f"L_{n:06d}", kind, text, section,
                                       row=f"{t_id}r{r_i}", col=col))
        return n


def _p_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(f"{W}t"))


def _p_style(p) -> str:
    ppr = p.find(f"{W}pPr")
    if ppr is not None:
        st = ppr.find(f"{W}pStyle")
        if st is not None:
            return st.get(f"{W}val", "")
    return ""
