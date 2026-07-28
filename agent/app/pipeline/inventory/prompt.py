# -*- coding: utf-8 -*-
"""Prompts for the inventory + classify LLM calls.

Integrity rule (lineage-proven): NO examples drawn from real corpus documents
— concrete examples cause drift and corpus-tailoring. Closed output shapes
only; validation happens server-side.
"""
import json

CLASS_ENUM = {
    "INTERNAL_DOC_NAME", "INSTANCE_IDENTIFIER", "QUALIFIER_OF_IDENTIFIER",
    "PERSON", "ORG_OWNER", "ORG_UNIT", "ORG_EXTERNAL", "CONTACT_DETAIL",
    "DOMAIN_TERM",
}

ACTOR_KINDS = {"PERSON", "ORG_OWNER", "ORG_UNIT", "ORG_EXTERNAL",
               "INTERNAL_DOC", "SYSTEM"}


def build_inventory_prompt(payload: dict) -> str:
    return f"""You are the actor-inventory stage of a document anonymization pipeline.
Below is ONE section of an institutional document as JSON leaves.

Identify every ACTOR: named person, the owning organisation, internal org
units, external bodies, named internal documents, named systems. For each,
report the ROLE the document itself assigns it (the role is what will replace
the name). Do NOT decide what to hide; extraction only. Never skip an actor
because you are unsure — include it with your best role guess.

Return ONE JSON object, nothing else:
{{"actors": [{{"name": str, "kind": one of {sorted(ACTOR_KINDS)},
"role": str (short, in the document's language), "variants": [str, every
surface form seen in THIS section]}}]}}

SECTION:
{json.dumps(payload, ensure_ascii=False)}"""


def build_classify_prompt(payload: dict) -> str:
    return f"""You are the classification stage of a document anonymization pipeline.
For EACH item below, choose exactly one class from this closed list:
{sorted(CLASS_ENUM)}

Definitions: INTERNAL_DOC_NAME = title of a document owned by the issuing
organisation; INSTANCE_IDENTIFIER = a number/code identifying a specific
decision or record; QUALIFIER_OF_IDENTIFIER = a date/detail whose only value
is qualifying such an identifier; DOMAIN_TERM = generic domain vocabulary.

Return ONE JSON object, nothing else:
{{"items": [{{"surface": str (verbatim from input), "class": str (from the list)}}]}}

ITEMS:
{json.dumps(payload["items"], ensure_ascii=False)}"""
