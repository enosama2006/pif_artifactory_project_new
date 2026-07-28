"""Stage 5 (AGENT, no LLM): find EVERY mention of every actor variant in every leaf.

This is the no-drop guarantee for known actors: even if the decide-stage LLM
ignores a mention, the link exists here and the validator will flag it.

Two requirements discovered by simulation (docs/reference_simulation.py):
  #1 normalization deletes characters (diacritics/tatweel), so matching runs on
     normalized text but spans must address the ORIGINAL text → index map.
  #2 overlapping matches («صندوق» inside «صندوق الاستثمارات العامة») corrupt
     the rewrite if both apply → longest-first, drop overlaps.
"""
import re
from dataclasses import dataclass

AR_PREFIX = r"(?:و|ف|ب|ل|ك)?(?:ال|لل)?"
_DIACRITICS = set("ًٌٍَُِّْـ")
_FOLD = {"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه"}
# Letters/underscore bound a word; DIGITS do NOT — Word run-concatenation
# produces text like "2025Data Governance Policy" and the second mention must
# still match (real-document finding, run 646d065f6ea4).
_BOUND_L = r"(?<![A-Za-z_؀-ۿ])"
_BOUND_R = r"(?![A-Za-z_؀-ۿ])"


@dataclass
class SurfaceLink:
    leaf_id: str
    actor_id: str
    surface: str        # verbatim slice of the ORIGINAL text
    start: int          # offsets into the ORIGINAL text
    end: int


def normalize(s: str) -> str:
    return normalize_with_map(s)[0]


def normalize_with_map(s: str) -> tuple[str, list[int]]:
    out, idx_map = [], []
    for i, ch in enumerate(s):
        if ch in _DIACRITICS:
            continue
        out.append(_FOLD.get(ch, ch))
        idx_map.append(i)
    idx_map.append(len(s))
    return "".join(out), idx_map


def scan(leaves, actors) -> list[SurfaceLink]:
    """leaves: iterable with .leaf_id/.text ; actors: {actor_id: Actor}."""
    links: list[SurfaceLink] = []
    patterns = []
    for a in actors.values():
        for v in a.variants:
            core = re.sub(r"^ال", "", normalize(v))
            if not core:
                continue
            patterns.append((a.actor_id, re.compile(
                _BOUND_L + AR_PREFIX + re.escape(core) + _BOUND_R)))

    for lf in leaves:
        norm_text, idx_map = normalize_with_map(lf.text)
        raw = []
        for actor_id, pat in patterns:
            for m in pat.finditer(norm_text):
                start, end = idx_map[m.start()], idx_map[m.end()]
                raw.append(SurfaceLink(lf.leaf_id, actor_id,
                                       lf.text[start:end], start, end))
        raw.sort(key=lambda x: (-(x.end - x.start), x.start))
        kept: list[SurfaceLink] = []
        for cand in raw:
            if all(cand.end <= k.start or cand.start >= k.end for k in kept):
                kept.append(cand)
        links.extend(sorted(kept, key=lambda x: x.start))
    return links
