"""Stage 4 (AGENT): deterministic candidate sweep — RISK #1's first mitigation.

Patterns the classify LLM MUST rule on, found independently of any model:
if the inventory missed something these shapes catch, it still gets a
classification and can join a breakage cascade.
"""
import re

PATTERNS = [
    ("DATE", re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")),
    ("DECISION_NO", re.compile(r"(?:قرار|تعميم|خطاب)\s+رقم\s*[\d/]+")),
    ("QUOTED_NAME", re.compile(r"[«\"“]([^»\"”]{3,60})[»\"”]")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
]


def sweep(leaves) -> list[dict]:
    out = []
    for lf in leaves:
        for hint, pat in PATTERNS:
            for m in pat.finditer(lf.text):
                out.append({"leaf_id": lf.leaf_id, "surface": m.group(), "hint": hint})
    return out
