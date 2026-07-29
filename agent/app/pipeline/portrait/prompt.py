# -*- coding: utf-8 -*-
"""Prompt for the portrait LLM call — ONE call, right after ingest.

The portrait is the document-context stage the owner designed: before any
extraction or rewriting, the model reads the skeleton and the opening sample
and states what the document IS — its function, owner, audience, and the key
actors with their generic functions. Later stages consume it as context:
inventory extracts with the document's purpose in mind, placeholder minting
prefers the portrait's view of an actor's function, and decide rewrites
knowing what kind of text it is editing.

Same integrity rule as every prompt here: no corpus-drawn examples, closed
output shape, validated server-side (a failed/empty portrait never blocks
the run — the pipeline degrades to context-free behavior).
"""
import json


def build_portrait_prompt(payload: dict) -> str:
    return f"""You are the document-portrait stage of a document anonymization pipeline.
You see the document's full heading skeleton and an opening sample of its
text. Produce a compact PORTRAIT so later stages understand the context they
are rewriting in.

Describe:
- summary: 2–3 sentences — what this document is and what it regulates/does;
- document_function: one line — the document's function (its genre and
  purpose inside the owning organisation);
- owner: the organisation that owns/issues the document, as written;
- audience: who the document addresses;
- actors: the KEY actors (owning organisation, main org units, key role
  holders, external authorities) with the generic FUNCTION each performs.
  The function must fit ANY organisation and NEVER echo the actor's own
  name or its distinctive words — think "what would this actor be called
  in an anonymous org chart" (2–4 words).

Return ONE JSON object, nothing else:
{{"portrait": {{"summary": str, "document_function": str, "owner": str,
"audience": str, "actors": [{{"name": str, "function": str}}]}}}}

DOCUMENT SKELETON:
{json.dumps(payload.get("skeleton", []), ensure_ascii=False)}

OPENING SAMPLE (document order):
{json.dumps(payload.get("sample", []), ensure_ascii=False)}"""
