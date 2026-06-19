"""Loader for the modern-agent Component Explorer knowledge base.

Resolves a component key (and optional enum value) to a source-grounded
explanation. Missing entries return the KB's sentinel so the UI never shows an
invented explanation. See `data/component_explainer.yaml`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from loguru import logger

_KB_PATH = Path(__file__).parent / "data" / "component_explainer.yaml"
_FALLBACK_SENTINEL = "Not yet documented in the explainer KB — verify against Microsoft Learn."


@dataclass(frozen=True)
class Explanation:
    summary: str
    doc: str | None
    documented: bool  # False = sentinel (no primary source)


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        data = yaml.safe_load(_KB_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning(f"Component explainer KB not found at {_KB_PATH}")
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def sentinel() -> str:
    return _load().get("sentinel", _FALLBACK_SENTINEL)


def explain(key: str, value: str | None = None) -> Explanation:
    """Resolve a component key to its grounded explanation.

    When `value` matches a documented enum option, its note is appended to the
    summary. Unknown keys return the sentinel with `documented=False`.
    """
    entry = _load().get("entries", {}).get(key)
    if not isinstance(entry, dict):
        return Explanation(summary=sentinel(), doc=None, documented=False)

    summary = " ".join((entry.get("summary") or "").split())
    doc = entry.get("doc")

    if value:
        vobj = (entry.get("values") or {}).get(value)
        if isinstance(vobj, dict) and vobj.get("summary"):
            vsum = " ".join(vobj["summary"].split())
            summary = f"{summary} ({value}: {vsum})" if summary else vsum

    if not summary:
        return Explanation(summary=sentinel(), doc=doc, documented=False)
    return Explanation(summary=summary, doc=doc, documented=True)
