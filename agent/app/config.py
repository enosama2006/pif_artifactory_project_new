"""All tunables in one place; every knob env-overridable, never hard-coded at call sites."""
import os
from pathlib import Path

# agent/.env (gitignored) is loaded automatically — GROQ_API_KEY lives there.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


LLM_MODEL = os.environ.get("ANONYMIZER_LLM_MODEL", "groq/openai/gpt-oss-120b")
DB_URL = os.environ.get("ANONYMIZER_DB_URL", "")  # empty → ./anonymizer.db

# gpt-oss-120b is a reasoning model: the token budget must cover the hidden
# reasoning BEFORE the JSON starts, or Groq returns json_validate_failed with
# an empty failed_generation (real-run finding). Lineage values: 8192/16000.
LLM_MAX_TOKENS = _int_env("ANONYMIZER_LLM_MAX_TOKENS", 8192)
INVENTORY_MAX_TOKENS = _int_env("ANONYMIZER_INVENTORY_MAX_TOKENS", 16000)
# a "section" bigger than this is sub-split into multiple inventory calls
INVENTORY_CHUNK_CHARS = _int_env("ANONYMIZER_INVENTORY_CHUNK_CHARS", 12000)

# the portrait stage reads the skeleton + this much opening text (one call)
PORTRAIT_SAMPLE_CHARS = _int_env("ANONYMIZER_PORTRAIT_SAMPLE_CHARS", 9000)

BATCH_CHAR_BUDGET = _int_env("ANONYMIZER_BATCH_CHAR_BUDGET", 4000)
DECIDE_BATCH_LEAVES = _int_env("ANONYMIZER_DECIDE_BATCH_LEAVES", 10)
MAX_CONCURRENCY = _int_env("ANONYMIZER_MAX_CONCURRENCY", 6)
SECTION_ANALYSIS_TIMEOUT = _int_env("ANONYMIZER_SECTION_TIMEOUT", 120)
DECIDE_TIMEOUT = _int_env("ANONYMIZER_DECIDE_TIMEOUT", 30)
DECIDE_HARD_TIMEOUT = _int_env("ANONYMIZER_DECIDE_HARD_TIMEOUT", 60)
LLM_RETRIES = _int_env("ANONYMIZER_LLM_RETRIES", 2)
ENUM_REROLLS = _int_env("ANONYMIZER_ENUM_REROLLS", 2)
