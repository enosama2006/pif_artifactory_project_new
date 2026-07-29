"""Closed-operation validation for the comment arbiter (docs/DESIGN_hitl_comments.md).

The arbiter LLM translates ONE user comment into ONE operation from a CLOSED
set. This module is the gate: anything outside the enum, or referencing an
unknown actor/leaf, comes back as an error string that the UI shows verbatim
— it is NEVER executed (the invented-placeholder philosophy applied to ops).
"""
import re
from dataclasses import dataclass, field

ARBITER_OPS = {
    "add_surface",          # surface joins an actor's variants (or a new actor)
    "rename_placeholder",   # placeholder replaced everywhere on assemble
    "merge_actors",         # keep absorbs drop
    "correct_role",         # role prepended (wins future minting)
    "ignore_actor",         # excluded from scan → its rewrites dissolve
    "edit_leaf",            # user's wording is final for one leaf
    "rewrite_leaf",         # leaf re-decided with the comment as guidance
    "comment",              # no-op: guidance memory only
}

_ACTOR_KINDS = {"PERSON", "ORG_OWNER", "ORG_UNIT", "ORG_EXTERNAL",
                "INTERNAL_DOC", "SYSTEM"}
_PLACEHOLDER = re.compile(r"^<[a-z][a-z0-9_]*>$")


@dataclass
class ArbiterOp:
    op: str
    fields: dict = field(default_factory=dict)
    reason: str = ""


def validate_op(data, actors: dict, leaf_ids: set[str]) -> tuple[ArbiterOp | None, str]:
    """LLM output dict → (op, "") or (None, error). Errors are user-facing."""
    if not isinstance(data, dict):
        return None, f"arbiter returned {type(data).__name__}, not an object"
    op = data.get("op")
    if op not in ARBITER_OPS:
        return None, f"operation {op!r} is not in the closed set {sorted(ARBITER_OPS)}"

    def actor_ok(key="actor_id"):
        aid = data.get(key)
        if aid not in actors:
            return None, f"{op}: unknown {key} {aid!r} (dictionary has {sorted(actors)})"
        return aid, ""

    reason = str(data.get("reason", ""))

    if op == "add_surface":
        surface = str(data.get("surface", "")).strip()
        if not surface:
            return None, "add_surface: empty surface"
        new_actor = data.get("new_actor")
        if new_actor is not None:
            if not isinstance(new_actor, dict) or not str(new_actor.get("name", "")).strip():
                return None, "add_surface: new_actor needs at least a name"
            kind = str(new_actor.get("kind", "ORG_UNIT"))
            if kind not in _ACTOR_KINDS:
                return None, f"add_surface: unknown kind {kind!r} (allowed: {sorted(_ACTOR_KINDS)})"
            return ArbiterOp(op, {"surface": surface, "new_actor": {
                "name": str(new_actor["name"]).strip(), "kind": kind,
                "role": str(new_actor.get("role", "")).strip()}}, reason), ""
        aid, err = actor_ok()
        if err:
            return None, err
        return ArbiterOp(op, {"surface": surface, "actor_id": aid}, reason), ""

    if op == "rename_placeholder":
        aid, err = actor_ok()
        if err:
            return None, err
        ph = str(data.get("placeholder", "")).strip()
        if not _PLACEHOLDER.match(ph):
            return None, f"rename_placeholder: {ph!r} is not <snake_case>"
        return ArbiterOp(op, {"actor_id": aid, "placeholder": ph}, reason), ""

    if op == "merge_actors":
        keep, drop = data.get("keep"), data.get("drop")
        if keep not in actors:
            return None, f"merge_actors: unknown keep {keep!r}"
        if drop not in actors:
            return None, f"merge_actors: unknown drop {drop!r}"
        if keep == drop:
            return None, "merge_actors: keep and drop are the same actor"
        return ArbiterOp(op, {"keep": keep, "drop": drop}, reason), ""

    if op == "correct_role":
        aid, err = actor_ok()
        if err:
            return None, err
        role = str(data.get("role", "")).strip()
        if not role:
            return None, "correct_role: empty role"
        return ArbiterOp(op, {"actor_id": aid, "role": role}, reason), ""

    if op == "ignore_actor":
        aid, err = actor_ok()
        if err:
            return None, err
        return ArbiterOp(op, {"actor_id": aid}, reason), ""

    if op in ("edit_leaf", "rewrite_leaf"):
        lid = data.get("leaf_id")
        if lid not in leaf_ids:
            return None, f"{op}: unknown leaf_id {lid!r}"
        if op == "edit_leaf":
            after = data.get("after")
            if not isinstance(after, str) or not after.strip():
                return None, "edit_leaf: empty after text"
            return ArbiterOp(op, {"leaf_id": lid, "after": after}, reason), ""
        guidance = str(data.get("guidance", "")).strip()
        if not guidance:
            return None, "rewrite_leaf: empty guidance"
        return ArbiterOp(op, {"leaf_id": lid, "guidance": guidance}, reason), ""

    return ArbiterOp("comment", {"note": str(data.get("note", ""))}, reason), ""
