"""Stage 3b (AGENT): deterministic actor merge + placeholder minting.

The LLM's per-section extractions arrive as loose dicts; this module unifies
them into ONE locked dictionary. Rules added from real-run findings
(3e6163a5156e — 61 tiny chunks fragmented and polluted the inventory):

  1. IDENTITY MERGE — actors are the same if their names match after
     normalization (leading articles stripped) OR they share ANY variant
     ("Chief of Staff" + "CoS DH" extracted separately must not get two
     placeholders; that breaks the core consistency guarantee).
  2. VARIANT TRIMMING — a variant that merely wraps a shorter variant in
     extra words is dropped ("PIF data systems", "owning divisions of NDMO
     functional domains"): the scan matches the core inside the phrase
     anyway, and replacing the whole phrase destroys the sentence.
  3. GENERIC-ACTOR DROP — an "actor" whose every variant is built purely
     from generic vocabulary ("This Policy", "Data Strategy", "Change
     Management Plan") carries no identity; replacing it damages the
     document for zero privacy gain.
  4. PLACEHOLDER SANITIZATION — placeholders must never leak identity
     ("<PIF_personnel>") and must stay a clean tag charset.
"""
import re
from dataclasses import dataclass

from .._lexicon import GENERIC_TOKENS
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


_ARTICLES = re.compile(r"^(?:the|a|an|ال)\s+", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z؀-ۿ][\w؀-ۿ]*")


def _key(name: str) -> str:
    return _ARTICLES.sub("", normalize(name).strip()).lower()


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def merge_actors(section_extractions: list[list[dict]]) -> dict[str, Actor]:
    """[[{name, kind, role, variants}, …] per section] → {actor_id: Actor}."""
    merged: dict[str, Actor] = {}
    for extraction in section_extractions:
        for raw in extraction:
            name = str(raw.get("name", "")).strip()
            if not name:
                continue
            variants = sorted({v.strip() for v in (raw.get("variants") or [name])
                               if v and v.strip()} | {name})
            target = merged.get(_key(name)) or _find_by_variant(merged, variants)
            if target is not None:
                target.variants = sorted(set(target.variants) | set(variants))
                role = str(raw.get("role", "")).strip()
                if role and role not in target.roles:
                    target.roles.append(role)
                merged.setdefault(_key(name), target)
            else:
                merged[_key(name)] = Actor(
                    actor_id="", name=name, kind=str(raw.get("kind", "ORG_UNIT")),
                    roles=[str(raw["role"]).strip()] if raw.get("role") else [],
                    variants=variants)

    out: dict[str, Actor] = {}
    seen: set[int] = set()
    for a in merged.values():
        if id(a) in seen:
            continue
        seen.add(id(a))
        a.variants = _trim_wrapping_variants(a.variants)
        if _is_generic(a):
            continue
        a.actor_id = f"ACT_{len(out) + 1:03d}"
        out[a.actor_id] = a

    identity_tokens = _identity_tokens(out)
    for a in out.values():
        name_tokens = {t.lower() for v in [a.name, *a.variants] for t in _tokens(v)}
        a.placeholder = _mint_placeholder(_pick_role(a, identity_tokens),
                                          identity_tokens, name_tokens, a.kind)
    _dedupe_placeholders(out)
    return out


_GLUE = {"of", "for", "and", "the", "a", "an", "in", "to"}

_KIND_FALLBACK = {
    "PERSON": "person", "ORG_OWNER": "owner_organisation",
    "ORG_UNIT": "organisational_unit", "ORG_EXTERNAL": "external_authority",
    "INTERNAL_DOC": "internal_document", "SYSTEM": "internal_system",
}


def _pick_role(a: Actor, identity_tokens: set[str]) -> str:
    """Prefer the role that FUNCTIONS, not the one that echoes the name.

    Real-run finding (72d2c2e3b84a): the LLM often restates the entity's name
    as its 'role' ("Digital & Technology Department"), and stripping identity
    tokens then leaves a husk ("<Department>", "<Board_of>",
    "<for_Data_and_Intelligence>"). Score every extracted role: fewest
    identity/name-echo tokens wins, meaningful (non-glue) remainder required.
    """
    def score(role: str):
        toks = _tokens(role)
        echo = sum(1 for t in toks if t.lower() in identity_tokens)
        meaningful = [t for t in toks
                      if t.lower() not in identity_tokens and t.lower() not in _GLUE]
        return (0 if meaningful else 1, echo, -len(meaningful))

    return min(a.roles, key=score) if a.roles else ""


def _mint_placeholder(role: str, identity_tokens: set[str],
                      name_tokens: set[str], kind: str) -> str:
    toks = [t for t in _tokens(role) if t.lower() not in identity_tokens]
    substantive = [t for t in toks if t.lower() not in _GLUE]
    # A role whose every substantive word comes from the actor's own name is a
    # description, not a function ("Saudi Authority for Data and Artificial
    # Intelligence" → "for Data and Intelligence") — fall back to the kind.
    if not substantive or all(t.lower() in name_tokens for t in substantive):
        toks = [_KIND_FALLBACK.get(kind, "actor")]
    text = re.sub(r"[^\w؀-ۿ]+", "_", "_".join(toks)).strip("_") or "actor"
    return f"<{text}>"


def _find_by_variant(merged: dict[str, Actor], variants: list[str]):
    keys = {_key(v) for v in variants}
    for a in merged.values():
        if keys & {_key(v) for v in a.variants}:
            return a
    return None


def _trim_wrapping_variants(variants: list[str]) -> list[str]:
    """Drop a variant that contains a shorter kept variant as a whole-word
    substring — the scan will match the core inside it anyway."""
    kept: list[str] = []
    for v in sorted(variants, key=lambda x: len(normalize(x))):
        nv = normalize(v).lower()
        if any(re.search(rf"(?<![\w؀-ۿ]){re.escape(normalize(k).lower())}(?![\w؀-ۿ])", nv)
               for k in kept):
            continue
        kept.append(v)
    return sorted(kept)


def _is_generic(a: Actor) -> bool:
    for v in [a.name, *a.variants]:
        toks = _tokens(v)
        if toks and any(t.lower() not in GENERIC_TOKENS for t in toks):
            return False
    return True


def _identity_tokens(actors: dict[str, Actor]) -> set[str]:
    """Tokens that ARE identity (non-generic variant tokens) — they must
    never appear inside any placeholder (found leaked: <PIF_personnel>)."""
    out: set[str] = set()
    for a in actors.values():
        for v in [a.name, *a.variants]:
            for t in _tokens(v):
                if t.lower() not in GENERIC_TOKENS and len(t) >= 2:
                    out.add(t.lower())
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
