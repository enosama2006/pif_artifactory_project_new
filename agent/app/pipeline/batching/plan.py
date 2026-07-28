"""Deterministic batch planning — STRUCTURE vs TEXT separation.

Design rule (owner's): the document SKELETON (title/heading outline) is
context that accompanies every batch so the LLM knows where the text below
sits; the TEXT itself is partitioned into batches so that EVERY leaf lands
in EXACTLY ONE batch — batch boundaries prefer structural (section) edges,
and the invariant  sum(leaves across batches) == total leaves  is enforced
here, not trusted.
"""
from dataclasses import dataclass, field

from ... import config


@dataclass
class Batch:
    batch_id: str
    sections: list[str] = field(default_factory=list)
    leaf_ids: list[str] = field(default_factory=list)
    chars: int = 0


def build_skeleton(leaves) -> list[dict]:
    """The document outline: every title/heading with its section key."""
    return [{"section": lf.section, "text": lf.text.strip()}
            for lf in leaves if lf.kind in ("title", "heading")]


def build_table_headers(leaves) -> dict[str, list[str]]:
    """{table_id: [column headers]} — the header row is CONTEXT, like a path:
    without it the LLM cannot know what a cell value means (owner's rule)."""
    out: dict[str, list[str]] = {}
    for lf in leaves:
        if lf.kind == "table_header_cell" and lf.row:
            out.setdefault(lf.row.split("r")[0], []).append(lf.text.strip())
    return out


def _units(leaves):
    """Atomic pack units: a lone leaf, or ALL leaves of one table row —
    a row is never split across batches (owner's rule)."""
    i = 0
    while i < len(leaves):
        lf = leaves[i]
        if lf.row is None:
            yield [lf]
            i += 1
        else:
            j = i
            while j < len(leaves) and leaves[j].row == lf.row:
                j += 1
            yield leaves[i:j]
            i = j


def plan_batches(leaves, *, max_chars: int = config.BATCH_CHAR_BUDGET,
                 max_leaves: int = config.DECIDE_BATCH_LEAVES) -> list[Batch]:
    """Partition ALL leaves into batches in document order.

    A batch closes when it is full (chars or leaf count); when a section
    boundary arrives and the batch is already ≥60% full, it closes early so
    structurally-related leaves stay together. Raises if the partition does
    not cover every leaf exactly once — coverage is verified, never assumed.
    """
    batches: list[Batch] = []
    cur = Batch(batch_id="b001")

    def close():
        nonlocal cur
        if cur.leaf_ids:
            batches.append(cur)
            cur = Batch(batch_id=f"b{len(batches) + 1:03d}")

    prev_section = None
    for unit in _units(leaves):
        u_chars = sum(len(lf.text) for lf in unit)
        section = unit[0].section
        section_edge = prev_section is not None and section != prev_section
        full = (cur.chars + u_chars > max_chars) or \
               (len(cur.leaf_ids) + len(unit) > max_leaves)
        nearly_full = (cur.chars >= max_chars * 0.6) or (len(cur.leaf_ids) >= max_leaves * 0.6)
        if cur.leaf_ids and (full or (section_edge and nearly_full)):
            close()
        for lf in unit:
            cur.leaf_ids.append(lf.leaf_id)
            cur.chars += len(lf.text)
            if lf.section not in cur.sections:
                cur.sections.append(lf.section)
        prev_section = section
    close()

    # THE invariant: every leaf in exactly one batch.
    flat = [i for b in batches for i in b.leaf_ids]
    if len(flat) != len(leaves) or set(flat) != {l.leaf_id for l in leaves}:
        raise AssertionError(
            f"batch plan broke coverage: {len(flat)} placed vs {len(leaves)} leaves")
    return batches
