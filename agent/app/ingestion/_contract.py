"""UnifiedDocument (USD) — the single internal input contract.

Hard-won rule (v9 refactor report): never let a parser's type vocabulary cross
into the pipeline. The `kind` set here is closed and project-owned; ingestion
blocks must map whatever their format contains onto it.

Every piece of human-visible text is a leaf — titles, headings, meta lines,
table header cells, headers/footers/footnotes. No exceptions: anything not a
leaf is invisible to anonymization and WILL leak.
"""
from dataclasses import dataclass, field

KINDS = {
    "title", "meta", "heading", "paragraph", "list_item",
    "table_header_cell", "table_cell", "caption",
    "page_header", "page_footer", "footnote",
}


@dataclass
class Leaf:
    leaf_id: str          # L_000001… — document order, stable, THE anchor namespace
    kind: str             # ∈ KINDS
    text: str
    section: str          # section path key ("root", "s1", "s1/s1.2", …)
    row: str | None = None    # table row address (t1r4) — cascade-rule scope
    col: str | None = None    # column header text — free table semantics

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown leaf kind {self.kind!r} — extend KINDS deliberately")


@dataclass
class UnifiedDocument:
    leaves: list[Leaf] = field(default_factory=list)
    source_format: str = ""

    @property
    def leaf_count(self) -> int:
        """THE coverage invariant: every later stage is audited against this number."""
        return len(self.leaves)

    def sections(self) -> dict[str, list[Leaf]]:
        out: dict[str, list[Leaf]] = {}
        for lf in self.leaves:
            out.setdefault(lf.section, []).append(lf)
        return out
