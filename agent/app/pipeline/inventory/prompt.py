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
units, external bodies, named internal documents, and named internal SYSTEMS
or platforms (portals, hubs, intranets, registries, tools with a proper name).
For each, report the ROLE the document itself assigns it (the role is what
will replace the name). Do NOT decide what to hide; extraction only. Never
skip an actor because you are unsure — include it with your best role guess.

VARIANTS are critical: list EVERY surface form of the actor, including
defined abbreviations, acronyms, short forms with a suffix dropped or
changed (a unit may appear without its type word, or with a different one:
"…Department" also appearing bare, or as "…Division"), and possessive or
prefixed uses. A variant you omit will survive anonymization.

ROLE is the GENERIC FUNCTION, never the name: describe what this actor DOES
in terms that fit ANY organisation — imagine the organisation is anonymous
and you must still convey who acts. An IT/technology unit is "IT department";
a person who owns this policy is "policy owner"; a state data regulator is
"national data regulator"; a records unit is "records-management unit".
NEVER echo the entity's own name or its distinctive words in the role — the
role becomes the replacement text, so a role that restates the name defeats
anonymization. It must also stay distinctive, never a bare type word like
"department" or "committee" alone.

Do NOT extract (they carry no identity and replacing them damages the
document):
- generic structural artifacts — table of contents, appendices,
  version/approval/amendment sheets, generic registers, lists, plans,
  frameworks, maps, curricula — unless the artifact's own title contains
  an organisation-identifying name;
- self-references to the current document ("this Policy", "the Policy");
- generic standard-defined role titles used as common nouns (data owners,
  data stewards, data custodians, personnel, stakeholders);
- generic descriptive groups ("portfolio companies", "other affiliates").

Never expand an abbreviation you have not seen expanded in the text —
report the abbreviation as-is with your best functional role.

Return ONE JSON object, nothing else:
{{"actors": [{{"name": str, "kind": one of {sorted(ACTOR_KINDS)},
"role": str (short, distinctive, in the document's language),
"variants": [str, every surface form seen in THIS section]}}]}}

DOCUMENT PORTRAIT (context — what this document is; use it to judge who is
an actor and what generic function each one performs):
{json.dumps(payload.get("portrait", {}), ensure_ascii=False)}

SECTION:
{json.dumps({k: v for k, v in payload.items() if k != "portrait"}, ensure_ascii=False)}"""


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
