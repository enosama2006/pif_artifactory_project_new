# -*- coding: utf-8 -*-
"""Reviser prompt — the comment-driven text change, as its OWN call.

HITL runs 1–2 proved that piggybacking a user's rewrite request on the
general decide prompt is unreliable: the model acknowledged the comment in
its reason while returning no changed text. This call has ONE job: produce
the corrected full text of one leaf per the user's comment, using only the
allowed placeholder vocabulary (locked dictionary + the standard breakage
placeholders). Validation and leak gates run in the engine.
"""
import json


def build_revise_prompt(payload: dict) -> str:
    return f"""You revise ONE text block of an anonymized document per a human
reviewer's comment. The document replaces identity-bearing surfaces with
functional placeholders so another organisation can ADOPT it later — a good
revision both anonymizes and ABSTRACTS: it tells the future adopter what
belongs in this spot (e.g. their own resolution number and its date).

Return ONE JSON object, nothing else:
{{"rewrite": "<the block's FULL corrected text>", "reason": "<one line>"}}

Rules:
- Follow the USER COMMENT — it is binding.
- Use ONLY placeholders from ALLOWED PLACEHOLDERS. Never invent a tag.
- Keep everything the comment does not touch faithful to the original.
- No real names, numbers or dates of the hidden identities may survive —
  mask them with the fitting allowed placeholder (<reference_number>,
  <date>, …).

USER COMMENT:
{json.dumps(payload.get("comment", ""), ensure_ascii=False)}

THE BLOCK (kind: {payload.get("leaf", {}).get("kind", "?")}, column: {payload.get("leaf", {}).get("column") or "-"}):
ORIGINAL TEXT:
{json.dumps(payload.get("leaf", {}).get("text", ""), ensure_ascii=False)}
CURRENT REWRITE (what the pipeline produced — the comment says it is wrong):
{json.dumps(payload.get("current_rewrite", ""), ensure_ascii=False)}

LINKED MENTIONS (already replaced by these placeholders):
{json.dumps(payload.get("mentions", []), ensure_ascii=False)}

ROW CELLS (this block is one cell of a table row — the row is ONE record;
use the siblings to understand what this cell represents):
{json.dumps(payload.get("row_cells", []), ensure_ascii=False)}

ALLOWED PLACEHOLDERS:
{json.dumps(payload.get("allowed_placeholders", []), ensure_ascii=False)}

DOCUMENT PORTRAIT (context):
{json.dumps(payload.get("portrait", {}), ensure_ascii=False)}
"""
