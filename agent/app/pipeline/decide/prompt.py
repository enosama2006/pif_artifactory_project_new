# -*- coding: utf-8 -*-
"""Prompt for the decide LLM call. Narrow by design:

The model does NOT invent placeholders (the dictionary is locked), does NOT
find mentions (the scan already did), does NOT apply cascade rules (the rule
engine already did). It judges, per leaf: apply the pre-linked replacements
(REWRITE), keep as-is (KEEP), or flag for a human (REVIEW) — plus it may
surface a missed identity as a REVIEW note. Every sent leaf id MUST appear in
the answer; the server reconciles by id and retries the missing ones.
"""
import json


def build_decide_prompt(payload: dict) -> str:
    return f"""You are the decision stage of a document anonymization pipeline.
For EVERY leaf below decide: "REWRITE" (apply its pre-linked mention
replacements / cascade placeholders), "KEEP" (identity-neutral as it stands),
or "REVIEW" (a human must look — say why).

Rules:
- Answer for EVERY leaf id. Omitting an id is an error.
- You may NOT introduce any placeholder not present in DICTIONARY or the
  leaf's cascade entries. "use" is optional and must come from those.
- If you notice an identity-revealing surface with NO mention link, do NOT
  rewrite it yourself: return decision "REVIEW" with reason "missed surface: …".

Return ONE JSON object, nothing else:
{{"decisions": {{"<leaf_id>": {{"decision": "REWRITE"|"KEEP"|"REVIEW",
"use": str|null, "reason": str}}}}}}

DOCUMENT PORTRAIT (context — what this document is, who owns it, what each
key actor does; judge every rewrite against this understanding):
{json.dumps(payload.get("portrait", {}), ensure_ascii=False)}

DOCUMENT SKELETON (structure context only — where the text below sits;
never rewrite the skeleton itself):
{json.dumps(payload.get("skeleton", []), ensure_ascii=False)}

THIS BATCH covers sections: {json.dumps(payload.get("batch_sections", []), ensure_ascii=False)}

TABLE HEADERS (context, like a path: leaves carrying "row"/"column" are
table cells — one row is one record; use the column header to understand
what each cell value means before deciding):
{json.dumps(payload.get("table_headers", {}), ensure_ascii=False)}

DICTIONARY:
{json.dumps(payload["dictionary"], ensure_ascii=False)}

LEAVES (every leaf below needs a decision):
{json.dumps(payload["leaves"], ensure_ascii=False)}"""


def build_retry_prompt(payload: dict) -> str:
    leaf = payload["leaf"]
    return f"""Single-leaf retry of the decision stage (previous batch omitted it).
Same rules as before; answer for THIS leaf only.

Return ONE JSON object, nothing else:
{{"decisions": {{"{leaf['id']}": {{"decision": "REWRITE"|"KEEP"|"REVIEW",
"use": str|null, "reason": str}}}}}}

LEAF:
{json.dumps(leaf, ensure_ascii=False)}"""
