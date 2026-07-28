"""Provider-agnostic LLM boundary.

Design rule (docs/ANALYSIS_v1_to_v10.md, failure F1): provider quirks are a
quarantined adapter concern, never a pipeline design driver. The pipeline only
ever sees this interface; swapping Groq for a structured-output model is a
one-line config change, decided by golden-corpus numbers.
"""
from typing import Protocol

from .. import config


class LlmClient(Protocol):
    def json_call(self, prompt: str, *, max_tokens: int = 4096,
                  temperature: float = 0.0) -> dict: ...


class EnumViolation(ValueError):
    pass


def closed_enum_call(client: LlmClient, prompt: str, *, enum: set[str],
                     items_key: str, class_key: str = "class",
                     rerolls: int = config.ENUM_REROLLS) -> list[dict]:
    """Stage-6 pattern: force the model to choose from a closed enum.

    Any out-of-enum value → automatic re-roll (fresh sampling); exhausted
    re-rolls raise EnumViolation so the caller routes the item to REVIEW.
    """
    last_bad = None
    for _ in range(rerolls + 1):
        data = client.json_call(prompt)
        items = data.get(items_key, data if isinstance(data, list) else [])
        bad = [i for i in items
               if not isinstance(i, dict) or i.get(class_key) not in enum]
        if not bad:
            return items
        last_bad = bad
    raise EnumViolation(f"enum violations after {rerolls} re-rolls: {last_bad!r}")


class GroqAdapter:
    """LiteLLM/Groq implementation. Wire in Milestone 2.

    Must carry over the three documented Groq quirk handlers from the lineage:
    thought-part stripping, json_validate_failed-as-transient, JSON-drift
    normalisation — all quarantined HERE, invisible to the pipeline.
    """

    def json_call(self, prompt: str, *, max_tokens: int = 4096,
                  temperature: float = 0.0) -> dict:
        raise NotImplementedError("Milestone 2: real Groq wiring")
