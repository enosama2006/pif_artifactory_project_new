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
    for lf in leaves:
        section_edge = prev_section is not None and lf.section != prev_section
        full = (cur.chars + len(lf.text) > max_chars) or (len(cur.leaf_ids) >= max_leaves)
        nearly_full = (cur.chars >= max_chars * 0.6) or (len(cur.leaf_ids) >= max_leaves * 0.6)
        if cur.leaf_ids and (full or (section_edge and nearly_full)):
            close()
        cur.leaf_ids.append(lf.leaf_id)
        cur.chars += len(lf.text)
        if lf.section not in cur.sections:
            cur.sections.append(lf.section)
        prev_section = lf.section
    close()

    # THE invariant: every leaf in exactly one batch.
    flat = [i for b in batches for i in b.leaf_ids]
    if len(flat) != len(leaves) or set(flat) != {l.leaf_id for l in leaves}:
        raise AssertionError(
            f"batch plan broke coverage: {len(flat)} placed vs {len(leaves)} leaves")
    return batches
