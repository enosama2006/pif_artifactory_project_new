"""Stage 3b (AGENT): deterministic actor merge + placeholder minting.

The LLM's per-section extractions arrive as loose dicts; this module unifies
them into ONE locked dictionary. Rules added from real-run findings
(3e6163a5156e — 61 tiny chunks fragmented and polluted the inventory;
a30d8030eb59 — "stupid placeholder" regression, see below):

  1. IDENTITY MERGE — actors are the same if their names match after
     normalization (leading articles stripped, "&" folded to "and") OR they
     share ANY variant ("Chief of Staff" + "CoS DH" extracted separately must
     not get two placeholders; that breaks the core consistency guarantee).
  2. VARIANT TRIMMING — a variant that merely wraps a shorter variant in
     extra words is dropped ("PIF data systems", "owning divisions of NDMO
     functional domains"): the scan matches the core inside the phrase
     anyway, and replacing the whole phrase destroys the sentence.
     A parenthetical variant is SPLIT first ("Chief Data Officer (CDO)" →
     both parts become variants; run 5: the bare title never matched).
  3. GENERIC-ACTOR DROP — an "actor" whose every variant is built purely
     from generic vocabulary ("This Policy", "Data Strategy", "Change
     Management Plan") carries no identity; replacing it damages the
     document for zero privacy gain.
  4. PLACEHOLDER SANITIZATION — placeholders must never leak identity
     ("<PIF_personnel>") and must stay a clean tag charset.

Run-5 placeholder-quality lessons (a30d8030eb59) baked into role ranking:
  - the old score preferred the LONGEST identity-free role, so PIF got
    "data training participants" over "owner organization" → rank now
    prefers ~2 substantive words, then extraction frequency (consensus
    across chunks), then first-seen order;
  - FUNCTION_TOKENS (technology, cybersecurity, legal, …) are no longer
    identity, so descriptive unit roles ("technology department") survive
    minting instead of collapsing into <organisational_unit_N>;
  - a PERSON whose "name" is itself a job title keeps the title as its
    placeholder (<chief_data_officer>, not <person>) — a title carries no
    personal identity;
  - a non-PERSON role that merely restates >3 words of the actor's own name
    is a reconstruction, not a function ("Saudi Authority for Data and
    Artificial Intelligence") — try the next-ranked role, else kind fallback.
"""
import re
from dataclasses import dataclass

from .._lexicon import FUNCTION_TOKENS, GENERIC_TOKENS
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
_PARENTHETICAL = re.compile(r"^(.*?[\w؀-ۿ])\s*\(([^()]{1,60})\)\s*$")


def _key(name: str) -> str:
    # "&" folds to "and": "Records & Administration…" and "Records and
    # Administration…" are the same department (run-5: two actors, two tags)
    text = normalize(name).replace("&", " and ")
    text = re.sub(r"\s+", " ", text).strip()
    return _ARTICLES.sub("", text).lower()


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _has_identity(text: str) -> bool:
    """True if the text carries at least one identity token (not generic,
    not function vocabulary) — 'DASC', 'CoS DH', 'the Fund' do; a purely
    descriptive phrase like 'Advanced Analytics & AI' does not."""
    return any(t.lower() not in GENERIC_TOKENS and t.lower() not in FUNCTION_TOKENS
               and len(t) >= 2 for t in _tokens(text))


def _split_parenthetical(variants: set[str]) -> set[str]:
    """"Chief Data Officer (CDO)" → keep it AND add both parts — the scan
    must match the bare title and the bare acronym (run-5 missed surface)."""
    out = set(variants)
    for v in variants:
        m = _PARENTHETICAL.match(v)
        if m:
            out.add(m.group(1).strip())
            out.add(m.group(2).strip())
    return out


def merge_actors(section_extractions: list[list[dict]],
                 portrait: dict | None = None) -> dict[str, Actor]:
    """[[{name, kind, role, variants}, …] per section] → {actor_id: Actor}.

    `portrait` (optional, from the portrait stage) supplies document-level
    role hints: {"actors": [{"name", "function"}]} — a matching hint joins
    the actor's roles with a frequency bonus, so the document-context view
    of the actor's function wins ties in placeholder minting.
    """
    merged: dict[str, Actor] = {}
    role_counts: dict[int, dict[str, int]] = {}   # id(actor) → {role.lower(): n}

    def add_role(actor: Actor, role: str, weight: int = 1):
        if not role:
            return
        counts = role_counts.setdefault(id(actor), {})
        counts[role.lower()] = counts.get(role.lower(), 0) + weight
        if role not in actor.roles:
            actor.roles.append(role)

    for extraction in section_extractions:
        for raw in extraction:
            name = str(raw.get("name", "")).strip()
            if not name:
                continue
            variants = sorted(_split_parenthetical(
                {v.strip() for v in (raw.get("variants") or [name])
                 if v and v.strip()} | {name}))
            target = merged.get(_key(name)) or _find_by_variant(merged, variants)
            if target is not None:
                target.variants = sorted(set(target.variants) | set(variants))
                add_role(target, str(raw.get("role", "")).strip())
                merged.setdefault(_key(name), target)
            else:
                actor = Actor(actor_id="", name=name,
                              kind=str(raw.get("kind", "ORG_UNIT")),
                              roles=[], variants=variants)
                add_role(actor, str(raw.get("role", "")).strip())
                merged[_key(name)] = actor

    out: dict[str, Actor] = {}
    seen: set[int] = set()
    for a in merged.values():
        if id(a) in seen:
            continue
        seen.add(id(a))
        a.variants = _trim_wrapping_variants(a.variants)
        a.variants = _drop_polluted_variants(a)
        if _is_generic(a):
            continue
        a.actor_id = f"ACT_{len(out) + 1:03d}"
        out[a.actor_id] = a

    # portrait hints: the document-level view of each actor's function
    hints: dict[str, str] = {}
    for pa in (portrait or {}).get("actors", []) or []:
        if isinstance(pa, dict) and pa.get("name") and pa.get("function"):
            hints[_key(str(pa["name"]))] = str(pa["function"]).strip()
    for a in out.values():
        hint = next((hints[k] for k in map(_key, [a.name, *a.variants])
                     if k in hints), None)
        if hint:
            add_role(a, hint, weight=3)

    identity_tokens = _identity_tokens(out)
    for a in out.values():
        name_tokens = {t.lower() for v in [a.name, *a.variants] for t in _tokens(v)}
        ranked = _rank_roles(a, identity_tokens, role_counts.get(id(a), {}))
        a.placeholder = _mint_placeholder(a, ranked, identity_tokens, name_tokens)
    _dedupe_placeholders(out)
    return out


_GLUE = {"of", "for", "and", "the", "a", "an", "in", "to"}

_KIND_FALLBACK = {
    "PERSON": "person", "ORG_OWNER": "owner_organisation",
    "ORG_UNIT": "organisational_unit", "ORG_EXTERNAL": "external_authority",
    "INTERNAL_DOC": "internal_document", "SYSTEM": "internal_system",
}


def _rank_roles(a: Actor, identity_tokens: set[str],
                counts: dict[str, int]) -> list[str]:
    """Best role first. Run-5 lesson: `-len(meaningful)` preferred the
    LONGEST role ("data training participants" beat "owner organization" for
    PIF). Rank instead by: survives identity-stripping → fewest identity
    echoes → closest to two substantive words (a good tag length) → highest
    extraction frequency (consensus across chunks) → first seen."""
    def score(item):
        idx, role = item
        toks = _tokens(role)
        echo = sum(1 for t in toks if t.lower() in identity_tokens)
        meaningful = [t for t in toks
                      if t.lower() not in identity_tokens and t.lower() not in _GLUE]
        return (0 if meaningful else 1, echo, abs(len(meaningful) - 2),
                -counts.get(role.lower(), 1), idx)

    return [role for _, role in sorted(enumerate(a.roles), key=score)]


def _mint_placeholder(a: Actor, ranked_roles: list[str],
                      identity_tokens: set[str], name_tokens: set[str]) -> str:
    """Mint from the best role that yields a clean, non-reconstructing tag;
    exhausted → kind fallback (the run-4 husk guard, now a last resort)."""
    for role in ranked_roles:
        toks = [t for t in _tokens(role) if t.lower() not in identity_tokens]
        substantive = [t for t in toks if t.lower() not in _GLUE]
        if not substantive:
            continue                        # identity-stripping left a husk
        restates = all(t.lower() in name_tokens for t in substantive)
        if restates and a.kind != "PERSON" and len(substantive) > 3:
            # >3 name words back-to-back is a reconstruction of the proper
            # name, not a function ("Saudi Authority for Data and Artificial
            # Intelligence"). Short restatements are fine ("technology
            # department"); a PERSON title restated IS the function.
            continue
        text = re.sub(r"[^\w؀-ۿ]+", "_", "_".join(toks)).strip("_")
        if text:
            return f"<{text}>"
    return f"<{_KIND_FALLBACK.get(a.kind, 'actor')}>"


def _find_by_variant(merged: dict[str, Actor], variants: list[str]):
    """Same actor iff a shared variant CARRIES IDENTITY. Run-5 latent bug:
    the LLM listed the all-generic phrase "Advanced Analytics & AI" as a
    variant of the Digital & Technology Department, and a generic shared
    variant then silently merged two REAL departments (order-dependent).
    'DASC'/'CoS DH'-style identity variants still merge as before."""
    keys = {_key(v) for v in variants if _has_identity(v)}
    for a in merged.values():
        if keys & {_key(v) for v in a.variants if _has_identity(v)}:
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


def _drop_polluted_variants(a: Actor) -> list[str]:
    """An all-generic variant that is unrelated to the actor's own name is
    LLM pollution (run 5: "Advanced Analytics & AI" attached to the D&T
    department) — replacing it would hit ANOTHER actor's mentions. Related
    means: the variant's key sits inside the name key or vice versa."""
    name_key = _key(a.name)
    kept = []
    for v in a.variants:
        vk = _key(v)
        related = (vk == name_key or vk in name_key or name_key in vk)
        if _has_identity(v) or related:
            kept.append(v)
    return kept or a.variants


def _is_generic(a: Actor) -> bool:
    for v in [a.name, *a.variants]:
        toks = _tokens(v)
        if toks and any(t.lower() not in GENERIC_TOKENS for t in toks):
            return False
    return True


def _identity_tokens(actors: dict[str, Actor]) -> set[str]:
    """Tokens that ARE identity (non-generic, non-function variant tokens) —
    they must never appear inside any placeholder (found leaked:
    <PIF_personnel>). Function vocabulary (technology, legal, …) is NOT
    identity even when it appears in a unit's proper name (run-5 lesson)."""
    out: set[str] = set()
    for a in actors.values():
        for v in [a.name, *a.variants]:
            for t in _tokens(v):
                tl = t.lower()
                if tl not in GENERIC_TOKENS and tl not in FUNCTION_TOKENS and len(t) >= 2:
                    out.add(tl)
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
