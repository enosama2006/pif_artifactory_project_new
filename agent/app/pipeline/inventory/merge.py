"""Stage 3b (AGENT): deterministic actor merge + placeholder minting.

The LLM's per-section extractions arrive as loose dicts; this module unifies
duplicates across sections and mints ONE placeholder per actor. The dictionary
is LOCKED here, before any rewriting — structural guarantee that the same
person/org never gets two different placeholders in two batches.
"""
from dataclasses import dataclass, field

from ..surface_scan.scan import normalize


@dataclass
class Actor:
    actor_id: str
    name: str
    kind: str                       # PERSON | ORG_OWNER | ORG_UNIT | ORG_EXTERNAL | INTERNAL_DOC | SYSTEM
    roles: list[str]
    variants: list[str]
    placeholder: str = ""
    status: str = "confirmed"       # confirmed | candidate (decide-stage discoveries)


def merge_actors(section_extractions: list[list[dict]]) -> dict[str, Actor]:
    """[[{name, kind, role, variants}, …] per section] → {actor_id: Actor}."""
    merged: dict[str, Actor] = {}
    for extraction in section_extractions:
        for raw in extraction:
            key = normalize(raw["name"])
            if key in merged:
                a = merged[key]
                a.variants = sorted(set(a.variants) | set(raw.get("variants", [raw["name"]])))
                role = raw.get("role", "")
                if role and role not in a.roles:
                    a.roles.append(role)
            else:
                merged[key] = Actor(
                    actor_id="",
                    name=raw["name"],
                    kind=raw["kind"],
                    roles=[raw.get("role", "")] if raw.get("role") else [],
                    variants=sorted(set(raw.get("variants", []) or [raw["name"]])),
                )
    out: dict[str, Actor] = {}
    for i, a in enumerate(merged.values(), 1):
        a.actor_id = f"ACT_{i:03d}"
        primary = a.roles[0] if a.roles else a.kind.lower()
        a.placeholder = "<" + primary.replace(" ", "_") + ">"
        out[a.actor_id] = a
    _dedupe_placeholders(out)
    return out


def _dedupe_placeholders(actors: dict[str, Actor]) -> None:
    """Two different actors sharing a role label must not collapse into one tag."""
    used: set[str] = set()
    for a in actors.values():
        base = a.placeholder
        n = 2
        while a.placeholder in used:
            a.placeholder = f"{base[:-1]}_{n}>"
            n += 1
        used.add(a.placeholder)
