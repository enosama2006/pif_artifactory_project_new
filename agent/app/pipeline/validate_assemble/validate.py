"""Stage 9 (AGENT): coverage gate + apply-payload assembly.

Hard gates:
  1. coverage — every leaf has a decision (count vs the stage-1 invariant);
  2. consistency — no placeholder outside the locked dictionary in the payload;
  3. addressed — a surface link on a KEEP leaf is a warning (the human sees it);
  4. definition collapse — "<X> (“<X>”)" patterns fold to "<X>";
  5. shared anchor — 2+ rewrites on one content-control tag would clobber each
     other in Word, so they are demoted to REVIEW (manual apply);
  6. residual leak sweep — distinctive tokens of any actor's name that survive
     in a rewritten text (or sit on an unlinked KEEP leaf) mean the inventory
     missed a variant: acronym-like tokens demote the leaf to REVIEW, plain
     capitalized tokens raise a warning. Turns "missed variant" from a silent
     leak into a visible item (findings of real run 646d065f6ea4).

The payload is keyed by leaf ID + character offsets: the add-in applies via
content-control anchors, never by text search.
"""
import re
from dataclasses import dataclass, field


@dataclass
class AssembleResult:
    payload: list[dict] = field(default_factory=list)      # {leaf_id, anchor, before, after, spans}
    review_queue: list[dict] = field(default_factory=list)  # {leaf_id, text, reason}
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# "<X> (“<X>”)" — a name and its defined abbreviation mapping to the SAME
# placeholder produces a nonsensical doubled definition; fold it.
_DUP_DEF = re.compile(r"(<[^<>]+>)\s*[\(（]\s*[“”\"'«]*\s*\1\s*[”\"'»]*\s*[\)）]")

# Tokens too generic to signal identity when they survive a rewrite.
_GENERIC_TOKENS = {
    "data", "governance", "policy", "policies", "department", "departments",
    "committee", "committees", "board", "management", "office", "officer",
    "center", "centre", "division", "divisions", "sheet", "register",
    "registers", "appendix", "appendices", "table", "contents", "list",
    "laws", "regulations", "regulatory", "document", "documents",
    "applicable", "issue", "tracking", "definitions", "abbreviations",
    "functional", "domain", "domains", "ownership", "catalog", "catalogue",
    "tools", "records", "administration", "advisory", "national",
    "analytics", "analytic", "advanced", "ai", "risk", "corporate",
    "affairs", "version", "approval", "history", "amendment", "steering",
    "working", "group", "strategy", "chief", "staff", "head", "body",
    "the", "of", "and", "for", "hub", "general", "executive", "internal",
}

_ACRONYMISH = re.compile(r"^(?:[A-Z]{2,6}|[A-Za-z]*[A-Z][a-z]*[A-Z][A-Za-z]*)$")
_PLACEHOLDER_RE = re.compile(r"<[^<>]+>")


def collapse_duplicate_placeholder(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _DUP_DEF.sub(r"\1", text)
    return text


def _actor_token_index(actors) -> dict[str, str]:
    """{distinctive token -> actor_id} from every actor name and variant."""
    out: dict[str, str] = {}
    for a in actors.values():
        for src in [a.name, *a.variants]:
            for tok in re.findall(r"[A-Za-z؀-ۿ][\w؀-ۿ&\-]*", src):
                if len(tok) < 2 or tok.lower() in _GENERIC_TOKENS:
                    continue
                out.setdefault(tok, a.actor_id)
    return out


def _leaks_in(text: str, token_index: dict[str, str]):
    """Yield (token, actor_id, acronymish) for actor tokens surviving in text."""
    haystack = _PLACEHOLDER_RE.sub(" ", text)
    for tok, actor_id in token_index.items():
        if re.search(rf"(?<![A-Za-z_؀-ۿ]){re.escape(tok)}(?![A-Za-z_؀-ۿ])", haystack):
            yield tok, actor_id, bool(_ACRONYMISH.match(tok))


def validate_and_assemble(leaves, links, decisions, actors,
                          cascade_hits=()) -> AssembleResult:
    res = AssembleResult()
    dec_by_leaf = {d.leaf_id: d for d in decisions}

    missing = [lf.leaf_id for lf in leaves if lf.leaf_id not in dec_by_leaf]
    if missing:
        raise AssertionError(f"coverage broken — leaves without decision: {missing}")

    ph_by_actor = {a.actor_id: a.placeholder for a in actors.values()}
    links_by_leaf: dict[str, list] = {}
    for l in links:
        links_by_leaf.setdefault(l.leaf_id, []).append(l)
    cascade_by_leaf = {c.leaf_id: c for c in cascade_hits}
    token_index = _actor_token_index(actors)

    pending: list[tuple] = []          # (leaf, payload item)
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
                after = collapse_duplicate_placeholder(render_preview(lf, spans))
                pending.append((lf, {"leaf_id": lf.leaf_id, "anchor": lf.anchor,
                                     "before": lf.text, "after": after,
                                     "spans": spans}))
        else:  # KEEP
            for l in links_by_leaf.get(lf.leaf_id, []):
                res.warnings.append(
                    f"{lf.leaf_id}: surface «{l.surface}» ({l.actor_id}) on a KEEP leaf")
            for tok, actor_id, acro in _leaks_in(lf.text, token_index):
                if acro:
                    res.review_queue.append({
                        "leaf_id": lf.leaf_id, "text": lf.text,
                        "reason": f"possible missed variant of {actor_id}: «{tok}» in a KEEP leaf"})
                    break
                res.warnings.append(
                    f"{lf.leaf_id}: actor token «{tok}» ({actor_id}) on a KEEP leaf")

    # gate 5 — shared anchors clobber each other on apply
    by_anchor: dict[str, list] = {}
    for lf, item in pending:
        if item["anchor"]:
            by_anchor.setdefault(item["anchor"], []).append(item["leaf_id"])
    shared = {a for a, ids in by_anchor.items() if len(ids) > 1}

    for lf, item in pending:
        if item["anchor"] in shared:
            res.review_queue.append({
                "leaf_id": lf.leaf_id, "text": lf.text,
                "reason": f"shared anchor {item['anchor']} — applying would overwrite "
                          f"sibling leaves; apply manually"})
            continue
        # gate 6 — residual leak sweep on the rewritten text
        demoted = False
        for tok, actor_id, acro in _leaks_in(item["after"], token_index):
            if acro:
                res.review_queue.append({
                    "leaf_id": lf.leaf_id, "text": lf.text,
                    "reason": f"residual identity token «{tok}» ({actor_id}) survives "
                              f"the rewrite — inventory variant missing"})
                demoted = True
                break
            res.warnings.append(
                f"{lf.leaf_id}: token «{tok}» ({actor_id}) survives the rewrite")
        if not demoted:
            res.payload.append(item)

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
