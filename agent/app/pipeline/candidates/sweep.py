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

_DATE_PATTERNS = [
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"(?:{_MONTHS})\s+\d{{4}}"),                    # "April 2025"
    re.compile(rf"\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}"),
]

PATTERNS = [
    *[("DATE", p) for p in _DATE_PATTERNS],
    ("DECISION_NO", re.compile(r"(?:قرار|تعميم|خطاب)\s+رقم\s*[\d/]+")),
    # Run-5 misses: bare reference codes in tracking tables escaped the
    # sweep entirely, so no classification and no breakage cascade —
    # "92/1445" (number/year; lookarounds keep it out of full dates) and
    # compact letter-digit codes like "Y24M06D02".
    ("DECISION_NO", re.compile(r"(?<![\d/])\d{1,4}/\d{3,4}(?![\d/])")),
    ("DECISION_NO", re.compile(r"\b(?:[A-Z]\d{2}){2,4}\b")),
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


def whole_text_is_date(text: str) -> bool:
    """True when the ENTIRE (trimmed) text is one date — the shape of a
    cover-page document date ("April 2025"), as opposed to a date inside a
    sentence. Owner rule (run-5 feedback): the document's own date is never
    left as-is."""
    t = text.strip()
    return bool(t) and any(p.fullmatch(t) for p in _DATE_PATTERNS)
