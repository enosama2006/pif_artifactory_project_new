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
