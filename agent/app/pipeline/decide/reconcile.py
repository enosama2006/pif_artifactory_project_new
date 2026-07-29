"""Stage 8 (AGENT): reconcile an LLM batch response BY LEAF ID.

Counting, not trust. Every leaf ID sent must come back with a decision:
  - missing from response      → caller retries it alone → still missing → auto-REVIEW
  - invented placeholder       → demoted to REVIEW (dictionary is locked)
  - malformed entry            → REVIEW
Silent loss is structurally impossible: the only exits are a valid decision
or a visible REVIEW item.
"""
import re
from dataclasses import dataclass

VALID_DECISIONS = {"REWRITE", "KEEP", "REVIEW"}

_TAG = re.compile(r"<[^<>\s]+>")


@dataclass
class Decision:
    leaf_id: str
    decision: str               # ∈ VALID_DECISIONS
    placeholder: str | None = None
    reason: str = ""
    # Full corrected text — produced ONLY for user-guided leaves in the
    # comment-driven redo: spans cannot express wording fixes (a duplicated
    # phrase survived a "fix the duplication" comment, HITL run 1).
    rewrite: str | None = None


def reconcile_batch(sent_leaf_ids: list[str], response: dict,
                    allowed_placeholders: set[str],
                    retry_fn=None) -> list[Decision]:
    """response: {leaf_id: {decision, use?, reason?}} as returned by the LLM.
    retry_fn(leaf_id) -> dict | None — single-leaf recovery call (optional).
    """
    out: list[Decision] = []
    for leaf_id in sent_leaf_ids:
        entry = response.get(leaf_id)
        if entry is None and retry_fn is not None:
            entry = retry_fn(leaf_id)
        if not isinstance(entry, dict):
            out.append(Decision(leaf_id, "REVIEW",
                                reason="auto-REVIEW: no decision returned"))
            continue
        decision = entry.get("decision")
        if decision not in VALID_DECISIONS:
            out.append(Decision(leaf_id, "REVIEW",
                                reason=f"auto-REVIEW: invalid decision {decision!r}"))
            continue
        ph = entry.get("use")
        if isinstance(ph, str) and not ph.strip():
            ph = None      # blank "use" is absence, not invention (run 7)
        if decision == "REWRITE" and ph is not None and ph not in allowed_placeholders:
            # A leaf with several mentions often comes back with "use" as a
            # comma-joined list ("<a>, <b>") — that is advisory, not invented
            # (run-5: 4 false REVIEWs on rows like "DCGA, D&T"). If every tag
            # inside is in the locked dictionary, accept the REWRITE with no
            # single placeholder: the pre-linked spans drive the rewrite.
            tags = _TAG.findall(str(ph))
            if tags and all(t in allowed_placeholders for t in tags):
                ph = None
            else:
                out.append(Decision(leaf_id, "REVIEW",
                                    reason=f"invented placeholder {ph} — not in the locked dictionary"))
                continue
        d = Decision(leaf_id, decision, placeholder=ph,
                     reason=entry.get("reason", ""))
        # guided-rewrite text (HITL redo): accepted only when every tag in it
        # sits in the locked dictionary — otherwise the spans stay in charge
        rw = entry.get("rewrite")
        if (decision == "REWRITE" and isinstance(rw, str) and rw.strip()
                and all(t in allowed_placeholders for t in _TAG.findall(rw))):
            d.rewrite = rw
        out.append(d)
    return out
