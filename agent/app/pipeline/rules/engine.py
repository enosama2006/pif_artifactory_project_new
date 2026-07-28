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


def apply_rules(rules, leaves, hidden_rows: set[str],
                classifications: list[dict]) -> list[CascadeHit]:
    """hidden_rows: table rows carrying a surface already decided hidden.
    classifications: [{leaf_id, surface, class}] from stage 6 (closed enum).
    """
    hits: list[CascadeHit] = []
    cls_by_leaf: dict[str, list[dict]] = {}
    for c in classifications:
        cls_by_leaf.setdefault(c["leaf_id"], []).append(c)

    for rule in rules:
        targets = set(rule["same_row_hide"])
        for lf in leaves:
            if lf.row is None or lf.row not in hidden_rows:
                continue
            for c in cls_by_leaf.get(lf.leaf_id, []):
                if c["class"] in targets:
                    hits.append(CascadeHit(
                        leaf_id=lf.leaf_id, surface=c["surface"], klass=c["class"],
                        rule=rule["name"], reason=rule["reason"],
                        placeholder=rule["placeholders"].get(c["class"], "<محذوف>"),
                    ))
    return hits
