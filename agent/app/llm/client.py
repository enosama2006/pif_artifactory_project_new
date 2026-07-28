"""Provider-agnostic LLM boundary.

Design rule (docs/ANALYSIS_v1_to_v10.md, failure F1): provider quirks are a
quarantined adapter concern, never a pipeline design driver. The pipeline only
sees `json_call(prompt, payload=…)`. `get_llm()` picks the adapter:
GROQ_API_KEY set → GroqAdapter (litellm, JSON mode, quirk handling); otherwise
StubLlm so the agent runs end-to-end with no key (0-actor inventory, mechanical
classify/decide from the payload) — useful for wiring tests and demos.
"""
import json
import os
import time
from typing import Protocol

from .. import config


class LlmClient(Protocol):
    is_stub: bool

    def json_call(self, prompt: str, *, payload: dict | None = None,
                  max_tokens: int = 4096, temperature: float = 0.0) -> dict: ...


class EnumViolation(ValueError):
    pass


def closed_enum_call(client: LlmClient, prompt: str, *, enum: set[str],
                     items_key: str, class_key: str = "class",
                     payload: dict | None = None,
                     rerolls: int = config.ENUM_REROLLS) -> list[dict]:
    """Force the model to choose from a closed enum; out-of-enum → re-roll →
    exhausted → EnumViolation (caller routes to REVIEW)."""
    last_bad = None
    for _ in range(rerolls + 1):
        data = client.json_call(prompt, payload=payload)
        items = data.get(items_key, data if isinstance(data, list) else [])
        bad = [i for i in items
               if not isinstance(i, dict) or i.get(class_key) not in enum]
        if not bad:
            return items
        last_bad = bad
    raise EnumViolation(f"enum violations after {rerolls} re-rolls: {last_bad!r}")


# ── Groq (litellm) — the three documented lineage quirks live HERE only ─────

_TRANSIENT_FRAGMENTS = (
    "json_validate_failed",      # Groq emits malformed JSON despite JSON mode;
    "Failed to generate JSON",   # re-rolling (fresh sampling) almost always fixes it
    "Connection reset", "WinError 10054", "timed out", "Temporary failure",
    "rate_limit", "429", "500", "502", "503",
)
_BACKOFF = (2, 5, 12, 25, 45)


class GroqAdapter:
    is_stub = False

    def __init__(self, model: str = config.LLM_MODEL):
        self.model = model

    def json_call(self, prompt: str, *, payload: dict | None = None,
                  max_tokens: int = 4096, temperature: float = 0.0) -> dict:
        import litellm
        last = None
        for wait in (0,) + _BACKOFF:
            if wait:
                time.sleep(wait)
            try:
                resp = litellm.completion(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=config.DECIDE_HARD_TIMEOUT * 2,
                )
                data = json.loads(resp.choices[0].message.content)
                if isinstance(data, list):  # observed drift: top-level array
                    merged: dict = {}
                    for el in data:
                        if isinstance(el, dict):
                            merged.update(el)
                    data = merged
                return data if isinstance(data, dict) else {}
            except Exception as exc:  # noqa: BLE001 — fragment-matched below
                last = exc
                if not any(f in str(exc) for f in _TRANSIENT_FRAGMENTS):
                    raise
        raise RuntimeError(f"LLM call failed after retries: {last!r}")


# ── Stub — no key needed; mechanical answers derived from the payload ───────

class StubLlm:
    is_stub = True

    _HINT_CLASS = {"DATE": "QUALIFIER_OF_IDENTIFIER",
                   "DECISION_NO": "INSTANCE_IDENTIFIER",
                   "EMAIL": "CONTACT_DETAIL"}

    def json_call(self, prompt: str, *, payload: dict | None = None,
                  max_tokens: int = 4096, temperature: float = 0.0) -> dict:
        task = (payload or {}).get("task", "")
        if task == "inventory":
            return {"actors": []}
        if task == "classify":
            return {"items": [
                {"surface": it["surface"],
                 "class": self._HINT_CLASS.get(it.get("hint"), "DOMAIN_TERM")}
                for it in payload.get("items", [])]}
        if task == "decide":
            return {"decisions": {
                lf["id"]: {"decision": "REWRITE" if (lf["mentions"] or lf["cascade"]) else "KEEP",
                           "use": None,
                           "reason": "stub: pre-linked mentions applied mechanically"}
                for lf in payload.get("leaves", [])}}
        if task == "retry_leaf":
            return {"decisions": {}}
        return {}


def get_llm() -> LlmClient:
    if os.environ.get("GROQ_API_KEY"):
        return GroqAdapter()
    return StubLlm()
