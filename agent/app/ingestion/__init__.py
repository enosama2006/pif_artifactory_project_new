"""Ingestion gateway: every input format is a block `(raw bytes, mime) -> UnifiedDocument`.

The pipeline imports ONLY the USD contract — never a format module.
Adding a format = one new folder + one register() call. Zero pipeline changes.
"""
from ._contract import Leaf, UnifiedDocument

_REGISTRY: dict[str, object] = {}


def register(fmt: str, block) -> None:
    _REGISTRY[fmt] = block


def ingest(raw: bytes, fmt: str) -> UnifiedDocument:
    if fmt not in _REGISTRY:
        raise ValueError(f"no ingestion block for format {fmt!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[fmt].to_usd(raw)


from . import ooxml  # noqa: E402  (self-registers "docx" and "ooxml")
