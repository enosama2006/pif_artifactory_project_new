"""Stage 4 (AGENT): deterministic candidate sweep — RISK #1's first mitigation.

Patterns the classify LLM MUST rule on, found independently of any model:
if the inventory missed something these shapes catch, it still gets a
classification and can join a breakage cascade.
"""
import re

_MONTHS = ("January|February|March|April|May|June|July|August|September|"
           "October|November|December|"
           "يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر|"
           "محرم|صفر|ربيع الأول|ربيع الآخر|جمادى الأولى|جمادى الآخرة|رجب|شعبان|رمضان|شوال|ذو القعدة|ذو الحجة")

PATTERNS = [
    ("DATE", re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")),
    ("DATE", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("DATE", re.compile(rf"(?:{_MONTHS})\s+\d{{4}}")),          # "April 2025"
    ("DATE", re.compile(rf"\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}")),
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
