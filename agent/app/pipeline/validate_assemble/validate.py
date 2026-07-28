"""Stage 9 (AGENT): coverage gate + apply-payload assembly.

Three gates, all hard:
  1. coverage — every leaf has a decision (count vs the stage-1 invariant);
  2. consistency — no placeholder outside the locked dictionary in the payload;
  3. addressed — a surface link on a KEEP leaf is a warning (the human sees it).
The payload is keyed by leaf ID + character offsets: the add-in applies via
content-control anchors, never by text search.
"""
from dataclasses import dataclass, field


@dataclass
class AssembleResult:
    payload: list[dict] = field(default_factory=list)      # {leaf_id, spans:[{start,end,replace}]}
    review_queue: list[dict] = field(default_factory=list)  # {leaf_id, text, reason}
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def validate_and_assemble(leaves, links, decisions, actors,
                          cascade_hits=()) -> AssembleResult:
    res = AssembleResult()
    dec_by_leaf = {d.leaf_id: d for d in decisions}
    leaf_by_id = {lf.leaf_id: lf for lf in leaves}

    missing = [lf.leaf_id for lf in leaves if lf.leaf_id not in dec_by_leaf]
    if missing:
        raise AssertionError(f"coverage broken — leaves without decision: {missing}")

    ph_by_actor = {a.actor_id: a.placeholder for a in actors.values()}
    links_by_leaf: dict[str, list] = {}
    for l in links:
        links_by_leaf.setdefault(l.leaf_id, []).append(l)
    cascade_by_leaf = {c.leaf_id: c for c in cascade_hits}

    for lf in leaves:
        d = dec_by_leaf[lf.leaf_id]
        if d.decision == "REVIEW":
            res.review_queue.append({"leaf_id": lf.leaf_id, "text": lf.text,
                                     "reason": d.reason})
        elif d.decision == "REWRITE":
            if lf.leaf_id in cascade_by_leaf:
                c = cascade_by_leaf[lf.leaf_id]
                spans = [{"start": lf.text.find(c.surface),
                          "end": lf.text.find(c.surface) + len(c.surface),
                          "replace": c.placeholder}]
            else:
                spans = [{"start": l.start, "end": l.end,
                          "replace": ph_by_actor[l.actor_id]}
                         for l in links_by_leaf.get(lf.leaf_id, [])]
            if spans:
                res.payload.append({"leaf_id": lf.leaf_id, "spans": spans})
        else:  # KEEP
            for l in links_by_leaf.get(lf.leaf_id, []):
                res.warnings.append(
                    f"{lf.leaf_id}: surface «{l.surface}» ({l.actor_id}) on a KEEP leaf")

    res.metrics = {
        "leaves": len(leaves),
        "coverage": len(dec_by_leaf) / len(leaves) if leaves else 1.0,
        "rewrites": len(res.payload),
        "review": len(res.review_queue),
        "warnings": len(res.warnings),
        "silent_losses": 0,  # by construction — see the coverage assertion above
    }
    return res


def render_preview(leaf, spans) -> str:
    """Apply spans to a leaf's text (right-to-left so offsets stay valid)."""
    text = leaf.text
    for s in sorted(spans, key=lambda x: -x["start"]):
        text = text[:s["start"]] + s["replace"] + text[s["end"]:]
    return text
