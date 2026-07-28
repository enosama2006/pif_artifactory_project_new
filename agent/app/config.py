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

BATCH_CHAR_BUDGET = _int_env("ANONYMIZER_BATCH_CHAR_BUDGET", 4000)
MAX_CONCURRENCY = _int_env("ANONYMIZER_MAX_CONCURRENCY", 6)
SECTION_ANALYSIS_TIMEOUT = _int_env("ANONYMIZER_SECTION_TIMEOUT", 120)
DECIDE_TIMEOUT = _int_env("ANONYMIZER_DECIDE_TIMEOUT", 30)
DECIDE_HARD_TIMEOUT = _int_env("ANONYMIZER_DECIDE_HARD_TIMEOUT", 60)
LLM_RETRIES = _int_env("ANONYMIZER_LLM_RETRIES", 2)
ENUM_REROLLS = _int_env("ANONYMIZER_ENUM_REROLLS", 2)
