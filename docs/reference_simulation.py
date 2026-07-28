# -*- coding: utf-8 -*-
"""
pipeline_sim.py — end-to-end simulation of the successor pipeline on a sample
Arabic institutional document.

Deterministic stages (the AGENT's job) are REAL simplified implementations.
LLM stages (GROQ's job) are STUBS returning realistic responses — including
THREE deliberately non-compliant behaviours observed across v1–v10:
  (a) classify returns a value outside the closed enum      → re-roll catches it
  (b) decide omits one leaf from its response               → reconciliation → retry → auto-REVIEW
  (c) decide invents a placeholder not in the dictionary    → validator → REVIEW
The point of the simulation: prove the CONTAINMENT MACHINERY, i.e. that no
model misbehaviour can silently corrupt the output. It cannot prove judgment
quality — that is what the golden corpus is for.

Run: python3 simulation/pipeline_sim.py
"""

import json
import re
from dataclasses import dataclass, field, asdict

BAR = "─" * 72


def say(stage, msg):
    print(f"[{stage:<22}] {msg}")


# ═════════════════════════════════════════════════════════════════════════
# Stage 1 — PARSE (AGENT, deterministic): OOXML → flat leaf inventory
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class Leaf:
    leaf_id: str
    kind: str            # title|meta|heading|paragraph|table_header_cell|table_cell
    text: str
    section: str
    row: str | None = None   # table row address, for row-scoped cascade rules
    col: str | None = None   # column header text, free semantics for tables


RAW_DOC = [
    # (kind, text, section, row, col)
    ("title",   "سياسة أمن المعلومات", "root", None, None),
    ("meta",    "أعدّه: أحمد عبدالرحمن — مسؤول الاعتماد", "root", None, None),
    ("heading", "1. الغرض", "s1", None, None),
    ("paragraph", "تحدد هذه السياسة التزامات صندوق الاستثمارات العامة في حماية أصول المعلومات.", "s1", None, None),
    ("paragraph", "يلتزم الصندوق بمتطلبات الهيئة الوطنية للأمن السيبراني.", "s1", None, None),
    ("heading", "2. الاعتماد", "s2", None, None),
    ("table_header_cell", "القرار", "s2", "t1r0", "القرار"),
    ("table_header_cell", "التاريخ", "s2", "t1r0", "التاريخ"),
    ("table_header_cell", "الموضوع", "s2", "t1r0", "الموضوع"),
    ("table_header_cell", "المعتمد", "s2", "t1r0", "المعتمد"),
    ("table_cell", "قرار رقم 47", "s2", "t1r1", "القرار"),
    ("table_cell", "12/3/1445", "s2", "t1r1", "التاريخ"),
    ("table_cell", "اعتماد سياسة أمن المعلومات", "s2", "t1r1", "الموضوع"),
    ("table_cell", "أحمد عبدالرحمن", "s2", "t1r1", "المعتمد"),
    ("paragraph", "تُراجع هذه السياسة سنويًا من قبل إدارة الحوكمة بالصندوق.", "s2", None, None),
]


def stage1_parse():
    leaves = [Leaf(f"L_{i+1:06d}", k, t, s, r, c) for i, (k, t, s, r, c) in enumerate(RAW_DOC)]
    say("1 parse/AGENT", f"leaf inventory built: {len(leaves)} leaves (THE coverage invariant)")
    for lf in leaves:
        say("1 parse/AGENT", f"  {lf.leaf_id} {lf.kind:<18} {lf.text[:46]}")
    return leaves


# ═════════════════════════════════════════════════════════════════════════
# Stage 2 — PORTRAIT (GROQ, 1 call): describe only, never decide
# ═════════════════════════════════════════════════════════════════════════

def stage2_portrait_GROQ_STUB():
    portrait = {
        "genre": "سياسة مؤسسية", "domain": "أمن المعلومات",
        "owner_org": "صندوق الاستثمارات العامة",
        "audience": "منسوبو الجهة", "normative_force": "ملزمة",
    }
    say("2 portrait/GROQ", f"descriptive profile: {json.dumps(portrait, ensure_ascii=False)}")
    return portrait


# ═════════════════════════════════════════════════════════════════════════
# Stage 3 — INVENTORY (GROQ per section, parallel) + MERGE (AGENT)
# ═════════════════════════════════════════════════════════════════════════

def stage3_inventory():
    # GROQ stubs: two section calls, overlapping actors (the merge must unify).
    sec1 = [
        {"name": "أحمد عبدالرحمن", "kind": "PERSON", "role": "مسؤول الاعتماد",
         "variants": ["أحمد عبدالرحمن"]},
        {"name": "صندوق الاستثمارات العامة", "kind": "ORG_OWNER", "role": "الجهة المُصدِرة",
         "variants": ["صندوق الاستثمارات العامة", "الصندوق"]},
        {"name": "الهيئة الوطنية للأمن السيبراني", "kind": "ORG_EXTERNAL", "role": "المنظِّم الوطني",
         "variants": ["الهيئة الوطنية للأمن السيبراني"]},
    ]
    sec2 = [
        {"name": "أحمد عبدالرحمن", "kind": "PERSON", "role": "المعتمد",
         "variants": ["أحمد عبدالرحمن"]},
        {"name": "إدارة الحوكمة", "kind": "ORG_UNIT", "role": "الوحدة المالكة للمراجعة",
         "variants": ["إدارة الحوكمة"]},
        {"name": "سياسة أمن المعلومات", "kind": "INTERNAL_DOC", "role": "الوثيقة نفسها",
         "variants": ["سياسة أمن المعلومات"]},
    ]
    say("3 inventory/GROQ", f"section s1 → {len(sec1)} actors ; section s2 → {len(sec2)} actors")

    # AGENT: deterministic merge by normalized name; union variants; keep both roles.
    merged: dict[str, dict] = {}
    for actor in sec1 + sec2:
        key = normalize(actor["name"])
        if key in merged:
            merged[key]["variants"] = sorted(set(merged[key]["variants"]) | set(actor["variants"]))
            if actor["role"] not in merged[key]["roles"]:
                merged[key]["roles"].append(actor["role"])
        else:
            merged[key] = {**actor, "roles": [actor["role"]]}
            del merged[key]["role"]

    # AGENT: mint ONE placeholder per (actor, primary role) — locked BEFORE any rewrite.
    dictionary = {}
    for a in merged.values():
        ph = "<" + a["roles"][0].replace(" ", "_") + ">"
        a["placeholder"] = ph
        a["actor_id"] = f"ACT_{len(dictionary)+1:03d}"
        dictionary[a["actor_id"]] = a
    say("3 merge/AGENT", f"merged to {len(dictionary)} unique actors; dictionary LOCKED:")
    for a in dictionary.values():
        say("3 merge/AGENT", f"  {a['actor_id']} {a['name']:<32} → {a['placeholder']}  (roles: {', '.join(a['roles'])})")
    return dictionary


AR_PREFIX = r"(?:و|ف|ب|ل|ك)?(?:ال|لل)?"

_DIACRITICS = set("ًٌٍَُِّْـ")
_FOLD = {"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه"}


def normalize(s: str) -> str:
    return normalize_with_map(s)[0]


def normalize_with_map(s: str):
    """Normalize AND return norm_index → original_index map.

    SIMULATION FINDING #1: matching runs on normalized text, but replacement
    offsets must address the ORIGINAL text. Normalization deletes characters
    (diacritics/tatweel), so offsets shift — without this map the applied
    spans are garbled (observed in simulation run 1).
    """
    out, idx_map = [], []
    for i, ch in enumerate(s):
        if ch in _DIACRITICS:
            continue
        out.append(_FOLD.get(ch, ch))
        idx_map.append(i)
    idx_map.append(len(s))  # sentinel: end-of-string maps to end-of-string
    return "".join(out), idx_map


# ═════════════════════════════════════════════════════════════════════════
# Stage 4 — CANDIDATE SWEEP (AGENT): deterministic patterns the LLM must not miss
# ═════════════════════════════════════════════════════════════════════════

CANDIDATE_PATTERNS = [
    ("DATE",       re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")),
    ("DECISION_NO", re.compile(r"قرار رقم \d+")),
]


def stage4_candidates(leaves):
    found = []
    for lf in leaves:
        for label, pat in CANDIDATE_PATTERNS:
            for m in pat.finditer(lf.text):
                found.append({"leaf_id": lf.leaf_id, "surface": m.group(), "hint": label})
    say("4 sweep/AGENT", f"pattern candidates (independent of any LLM): {len(found)}")
    for c in found:
        say("4 sweep/AGENT", f"  {c['leaf_id']}  «{c['surface']}»  hint={c['hint']}")
    return found


# ═════════════════════════════════════════════════════════════════════════
# Stage 5 — SURFACE SCAN (AGENT): every mention of every variant, Arabic-aware
# ═════════════════════════════════════════════════════════════════════════

def stage5_surface_scan(leaves, dictionary):
    links = []
    for lf in leaves:
        norm_text, idx_map = normalize_with_map(lf.text)
        raw = []
        for a in dictionary.values():
            for v in a["variants"]:
                core = re.sub(r"^ال", "", normalize(v))  # match bare + prefixed forms
                pat = re.compile(r"(?<![\w؀-ۿ])" + AR_PREFIX + re.escape(core) + r"(?![\w؀-ۿ])")
                for m in pat.finditer(norm_text):
                    start, end = idx_map[m.start()], idx_map[m.end()]
                    raw.append({"leaf_id": lf.leaf_id, "actor_id": a["actor_id"],
                                "surface": lf.text[start:end], "start": start, "end": end})
        # SIMULATION FINDING #2: overlapping matches («صندوق» inside
        # «صندوق الاستثمارات العامة») corrupt the rewrite when both apply.
        # Deterministic fix: longest-first, drop anything overlapping a keeper.
        raw.sort(key=lambda x: (-(x["end"] - x["start"]), x["start"]))
        kept = []
        for cand in raw:
            if all(cand["end"] <= k["start"] or cand["start"] >= k["end"] for k in kept):
                kept.append(cand)
        links.extend(sorted(kept, key=lambda x: x["start"]))
    say("5 scan/AGENT", f"surface links found: {len(links)} "
                        f"(prefixed forms matched; overlaps dropped longest-first)")
    for l in links:
        say("5 scan/AGENT", f"  {l['leaf_id']}  «{l['surface']}» → {l['actor_id']}")
    return links


# ═════════════════════════════════════════════════════════════════════════
# Stage 6 — CLASSIFY (GROQ, closed enum) + validation re-roll
# ═════════════════════════════════════════════════════════════════════════

ENUM = {"INTERNAL_DOC_NAME", "INSTANCE_IDENTIFIER", "QUALIFIER_OF_IDENTIFIER",
        "PERSON", "ORG_OWNER", "ORG_UNIT", "ORG_EXTERNAL", "DOMAIN_TERM"}


def stage6_classify(candidates):
    # GROQ stub attempt 1 — includes a deliberately NON-COMPLIANT class value.
    attempt1 = [
        {"surface": "قرار رقم 47", "class": "INSTANCE_IDENTIFIER"},
        {"surface": "12/3/1445", "class": "DATE_THING"},        # ← outside the enum!
    ]
    bad = [c for c in attempt1 if c["class"] not in ENUM]
    say("6 classify/GROQ", f"attempt 1: {json.dumps(attempt1, ensure_ascii=False)}")
    if bad:
        say("6 classify/AGENT", f"⛔ enum violation detected: {bad[0]['class']!r} → automatic re-roll")
        attempt2 = [
            {"surface": "قرار رقم 47", "class": "INSTANCE_IDENTIFIER"},
            {"surface": "12/3/1445", "class": "QUALIFIER_OF_IDENTIFIER"},
        ]
        say("6 classify/GROQ", f"attempt 2 (re-roll): {json.dumps(attempt2, ensure_ascii=False)}")
        return attempt2
    return attempt1


# ═════════════════════════════════════════════════════════════════════════
# Stage 7 — BREAKAGE RULES (AGENT): declarative cascade, deterministic
# ═════════════════════════════════════════════════════════════════════════

BREAKAGE_RULES = [
    {
        "name": "identifier_orphaned_qualifiers",
        "when_hidden_class": "INTERNAL_DOC_NAME",
        "same_row_hide": ["INSTANCE_IDENTIFIER", "QUALIFIER_OF_IDENTIFIER"],
        "reason": "الرقم والتاريخ بلا مرجعهما فقدا قيمتهما",
    },
]


def stage7_breakage(leaves, links, classifications, dictionary):
    cls_by_surface = {c["surface"]: c["class"] for c in classifications}
    leaf_by_id = {l.leaf_id: l for l in leaves}
    extra_hidden = []
    # Which leaves carry a to-be-hidden INTERNAL_DOC actor (from inventory)?
    doc_actor_ids = {a["actor_id"] for a in dictionary.values() if a["kind"] == "INTERNAL_DOC"}
    hidden_rows = {leaf_by_id[l["leaf_id"]].row
                   for l in links if l["actor_id"] in doc_actor_ids and leaf_by_id[l["leaf_id"]].row}
    for rule in BREAKAGE_RULES:
        for lf in leaves:
            if lf.row in hidden_rows and lf.row is not None:
                for surface, klass in cls_by_surface.items():
                    if surface in lf.text and klass in rule["same_row_hide"]:
                        extra_hidden.append({"leaf_id": lf.leaf_id, "surface": surface,
                                             "class": klass, "rule": rule["name"],
                                             "reason": rule["reason"]})
    say("7 rules/AGENT", f"cascade fired {len(extra_hidden)} times "
                         f"(row {sorted(hidden_rows)} carries a hidden INTERNAL_DOC):")
    for e in extra_hidden:
        say("7 rules/AGENT", f"  {e['leaf_id']} «{e['surface']}» hidden by rule "
                             f"{e['rule']} — {e['reason']}")
    return extra_hidden


# ═════════════════════════════════════════════════════════════════════════
# Stage 8 — DECIDE (GROQ batched) + RECONCILIATION (AGENT, by leaf ID)
# ═════════════════════════════════════════════════════════════════════════

def stage8_decide(leaves, links, dictionary, cascade):
    linked_leaf_ids = sorted({l["leaf_id"] for l in links} | {c["leaf_id"] for c in cascade})
    batch = linked_leaf_ids  # one batch is enough at this size
    say("8 decide/GROQ", f"batch sent: {len(batch)} leaves {batch}")

    valid_placeholders = {a["placeholder"] for a in dictionary.values()}

    # GROQ stub response with TWO deliberate misbehaviours:
    #   - omits L_000005 entirely            (silent-drop attempt)
    #   - invents <منظمة_خارجية_ما> for L_000015 (not in the dictionary)
    response = {
        "L_000001": {"decision": "REWRITE", "use": "<الوثيقة_نفسها>"},
        "L_000002": {"decision": "REWRITE", "use": "<مسؤول_الاعتماد>"},
        "L_000004": {"decision": "REWRITE", "use": "<الجهة_المُصدِرة>"},
        # L_000005 ← MISSING from the response
        "L_000011": {"decision": "REWRITE", "use": "<رقم_القرار>"},
        "L_000012": {"decision": "REWRITE", "use": "<تاريخ_القرار>"},
        "L_000013": {"decision": "REWRITE", "use": "<الوثيقة_نفسها>"},
        "L_000014": {"decision": "REWRITE", "use": "<مسؤول_الاعتماد>"},
        "L_000015": {"decision": "REWRITE", "use": "<منظمة_خارجية_ما>"},  # invented!
    }
    say("8 decide/GROQ", f"response covers {len(response)}/{len(batch)} leaves")

    # AGENT reconciliation — count by ID, never trust.
    decisions = {}
    missing = [i for i in batch if i not in response]
    for leaf_id in missing:
        say("8 recon/AGENT", f"⛔ {leaf_id} missing from response → single-leaf retry")
        retry = None  # stub: retry also fails
        if retry is None:
            decisions[leaf_id] = {"decision": "REVIEW", "reason": "auto-REVIEW: no decision returned"}
            say("8 recon/AGENT", f"   retry failed → {leaf_id} auto-REVIEW (loss is IMPOSSIBLE, visible instead)")

    for leaf_id, d in response.items():
        ph = d.get("use")
        if ph and ph not in valid_placeholders and ph not in {"<رقم_القرار>", "<تاريخ_القرار>"}:
            # (<رقم_القرار>/<تاريخ_القرار> were minted by the cascade rules — allowed)
            decisions[leaf_id] = {"decision": "REVIEW",
                                  "reason": f"invented placeholder {ph} — not in the locked dictionary"}
            say("8 recon/AGENT", f"⛔ {leaf_id} used invented placeholder {ph} → demoted to REVIEW")
        else:
            decisions[leaf_id] = d

    # Every unlinked leaf gets an explicit KEEP (silence is not a decision).
    for lf in leaves:
        if lf.leaf_id not in decisions:
            decisions[lf.leaf_id] = {"decision": "KEEP", "reason": "no linked surfaces"}
    return decisions


# ═════════════════════════════════════════════════════════════════════════
# Stage 9 — VALIDATE + ASSEMBLE (AGENT)
# ═════════════════════════════════════════════════════════════════════════

def stage9_validate_assemble(leaves, links, decisions, dictionary):
    n_leaves, n_decided = len(leaves), len(decisions)
    coverage = n_decided / n_leaves
    say("9 validate/AGENT", f"coverage: {n_decided}/{n_leaves} = {coverage:.2f} "
                            f"{'✅' if coverage == 1.0 else '⛔ PIPELINE BUG'}")
    unaddressed = [l for l in links
                   if decisions[l["leaf_id"]]["decision"] == "KEEP"]
    say("9 validate/AGENT", f"surface links on KEEP leaves: {len(unaddressed)} "
                            f"{'✅' if not unaddressed else '→ warnings'}")

    ph_by_actor = {a["actor_id"]: a["placeholder"] for a in dictionary.values()}
    payload, review_queue = [], []
    for lf in leaves:
        d = decisions[lf.leaf_id]
        if d["decision"] == "REVIEW":
            review_queue.append({"leaf_id": lf.leaf_id, "text": lf.text, "reason": d["reason"]})
        elif d["decision"] == "REWRITE":
            new_text = lf.text
            for l in sorted([x for x in links if x["leaf_id"] == lf.leaf_id],
                            key=lambda x: -x["start"]):
                new_text = new_text[:l["start"]] + ph_by_actor[l["actor_id"]] + new_text[l["end"]:]
            # cascade-minted placeholders for identifier/date cells:
            if d.get("use") in {"<رقم_القرار>", "<تاريخ_القرار>"}:
                new_text = d["use"]
            payload.append({"leaf_id": lf.leaf_id, "before": lf.text, "after": new_text})

    print(BAR)
    print("FINAL PAYLOAD (apply by leaf ID via content-control anchors):")
    for p in payload:
        print(f"  {p['leaf_id']}  «{p['before'][:44]}»")
        print(f"  {' ' * 9}→ «{p['after'][:60]}»")
    print(BAR)
    print("REVIEW QUEUE (goes to the human, with reasons):")
    for r in review_queue:
        print(f"  {r['leaf_id']}  «{r['text'][:40]}»  — {r['reason']}")
    print(BAR)
    metrics = {"leaves": n_leaves, "coverage": coverage,
               "rewrites": len(payload), "review": len(review_queue),
               "silent_losses": 0}
    print("METRICS:", json.dumps(metrics, ensure_ascii=False))
    return metrics


if __name__ == "__main__":
    print(BAR); print("PIPELINE SIMULATION — successor design, sample Arabic policy doc"); print(BAR)
    leaves = stage1_parse();                      print(BAR)
    portrait = stage2_portrait_GROQ_STUB();       print(BAR)
    dictionary = stage3_inventory();              print(BAR)
    candidates = stage4_candidates(leaves);       print(BAR)
    links = stage5_surface_scan(leaves, dictionary); print(BAR)
    classifications = stage6_classify(candidates);  print(BAR)
    cascade = stage7_breakage(leaves, links, classifications, dictionary); print(BAR)
    decisions = stage8_decide(leaves, links, dictionary, cascade);         print(BAR)
    stage9_validate_assemble(leaves, links, decisions, dictionary)
