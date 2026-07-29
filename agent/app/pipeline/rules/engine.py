"""Stage 7 (AGENT): meaning-breakage cascades as declarative rules.

The rules the owner used to write as prompt prose ("if the document name in a
table row is hidden, its decision number and date are worthless — hide them
too") live in breakage_rules.yaml and are applied deterministically over the
stage-6 classifications. A rule cannot be 'forgotten' in one batch and applied
in another. New breakage patterns discovered during human review are added as
rules + golden-corpus test cases — never as prompt lines.
"""
from dataclasses import dataclass
from pathlib import Path

RULES_FILE = Path(__file__).with_name("breakage_rules.yaml")


@dataclass
class CascadeHit:
    leaf_id: str
    surface: str
    klass: str
    rule: str
    reason: str
    placeholder: str


def load_rules(path: Path = RULES_FILE) -> list[dict]:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]


def apply_rules(rules, leaves, hidden_rows,
                classifications: list[dict]) -> list[CascadeHit]:
    """hidden_rows: {row: {classes hidden in that row}} — WHAT was hidden
    matters since run 5 (a rule for hidden persons must not require a hidden
    document name; the old set form made cascade fire 0x on the real doc).
    A plain set is still accepted and means "hidden, class unknown"
    (matches every rule).
    classifications: [{leaf_id, surface, class}] from stage 6 (closed enum).
    """
    if isinstance(hidden_rows, (set, frozenset, list, tuple)):
        hidden_rows = {r: {"*"} for r in hidden_rows}
    hits: list[CascadeHit] = []
    cls_by_leaf: dict[str, list[dict]] = {}
    for c in classifications:
        cls_by_leaf.setdefault(c["leaf_id"], []).append(c)

    seen: set[tuple] = set()
    for rule in rules:
        when = rule["when_hidden_class"]
        when = {when} if isinstance(when, str) else set(when)
        targets = set(rule["same_row_hide"])
        for lf in leaves:
            hidden = hidden_rows.get(lf.row) if lf.row else None
            if not hidden or ("*" not in hidden and not (when & hidden)):
                continue
            for c in cls_by_leaf.get(lf.leaf_id, []):
                key = (lf.leaf_id, c["surface"], c["class"])
                if c["class"] in targets and key not in seen:
                    seen.add(key)   # two rules must not double-hit one surface
                    hits.append(CascadeHit(
                        leaf_id=lf.leaf_id, surface=c["surface"], klass=c["class"],
                        rule=rule["name"], reason=rule["reason"],
                        placeholder=rule["placeholders"].get(c["class"], "<redacted>"),
                    ))
    return hits


def compute_cascade(leaves, actors, links, classifications) -> list[CascadeHit]:
    """The FULL deterministic cascade pass: class-aware hidden rows from the
    links + declarative rules + the cover document-date rule. One function so
    the initial run (classify_rules_stage) and the comment-driven partial
    re-run compute the exact same cascade from the same inputs — the
    classifications come from the run's stored closed-enum call, so a redo
    needs zero extra LLM cost here.
    """
    from ..candidates.sweep import whole_text_is_date

    kind_to_class = {"PERSON": "PERSON", "ORG_OWNER": "ORG_OWNER",
                     "ORG_UNIT": "ORG_UNIT", "ORG_EXTERNAL": "ORG_EXTERNAL",
                     "INTERNAL_DOC": "INTERNAL_DOC_NAME", "SYSTEM": "SYSTEM"}
    actor_class = {a.actor_id: kind_to_class.get(a.kind, a.kind)
                   for a in actors.values()}
    leaf_rows = {l.leaf_id: l.row for l in leaves}
    hidden_rows: dict[str, set] = {}
    for l in links:
        row = leaf_rows.get(l.leaf_id)
        if row and l.actor_id in actor_class:
            hidden_rows.setdefault(row, set()).add(actor_class[l.actor_id])
    hits = apply_rules(load_rules(), leaves, hidden_rows, classifications)

    # Owner rule (run-5 feedback): the document's own date is never left
    # as-is. A leaf that IS one date, outside any table, in the front matter
    # (before the first heading) is the document's date → <document_date>.
    for lf in leaves:
        if lf.kind == "heading":
            break
        if lf.row is None and whole_text_is_date(lf.text):
            hits.append(CascadeHit(
                leaf_id=lf.leaf_id, surface=lf.text.strip(),
                klass="QUALIFIER_OF_IDENTIFIER", rule="document_date_on_cover",
                reason="the document's issue date identifies it",
                placeholder="<document_date>"))
    return hits
