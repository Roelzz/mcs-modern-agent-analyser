"""Generic, LLM-free tool-call narration + failure diagnosis.

Two pure, tool-agnostic entry points, unit-testable without Reflex:

* ``describe(tc)`` — a plain-language "what happened" one-liner for **every** call
  (success or failure), derived from generic verb inference + salient params + outcome.
* ``diagnose(tc, siblings)`` — for **failed** calls only, a tiered heuristic that
  **always** returns a populated ``ToolDiagnosis`` (category + cause + fix + evidence).

The diagnosis engine is generic-first: it works for *any* tool and *any* error by
mining structural signals (HTTP status, embedded JSON, placeholders, param echoes,
signal words, cross-call behaviour). A named-signature KB (``data/tool_diagnosis.yaml``)
only **sharpens** wording for recognised errors — a missing rule never means "no
diagnosis". See the plan for the full tier design.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import yaml
from loguru import logger

from models import ToolCall, ToolDiagnosis

_KB_PATH = Path(__file__).parent / "data" / "tool_diagnosis.yaml"

# Fixed, generic category vocabulary (never invent categories outside this set).
CATEGORIES = frozenset(
    {
        "authentication",
        "permission",
        "not-found",
        "validation",
        "configuration",
        "rate-limit",
        "timeout",
        "conflict",
        "parameter-schema",
        "server-error",
        "unknown",
    }
)

# --- verb lexicon (generic read/write inference) ----------------------------

# token -> (kind, human past-tense verb)
_VERB_LEXICON: dict[str, tuple[str, str]] = {
    "search": ("read", "Searched"),
    "retrieve": ("read", "Retrieved"),
    "download": ("read", "Downloaded"),
    "lookup": ("read", "Looked up"),
    "list": ("read", "Listed"),
    "find": ("read", "Searched"),
    "fetch": ("read", "Fetched"),
    "query": ("read", "Queried"),
    "read": ("read", "Read"),
    "load": ("read", "Loaded"),
    "get": ("read", "Read"),
    "update": ("write", "Updated"),
    "create": ("write", "Created"),
    "delete": ("write", "Deleted"),
    "remove": ("write", "Removed"),
    "insert": ("write", "Inserted"),
    "upsert": ("write", "Upserted"),
    "patch": ("write", "Patched"),
    "post": ("write", "Posted"),
    "send": ("write", "Sent"),
    "save": ("write", "Saved"),
    "write": ("write", "Wrote"),
    "add": ("write", "Added"),
    "set": ("write", "Set"),
    "put": ("write", "Sent"),
}
# match longer tokens first so "search" wins over nothing, "update" over "add", etc.
_VERB_TOKENS = sorted(_VERB_LEXICON, key=len, reverse=True)

_JSON_FIELDS = (
    "message",
    "error",
    "error_description",
    "errorMessage",
    "reason",
    "detail",
    "description",
    "code",
    "errorCode",
    "title",
    "status",
    "clientRequestId",
    "fromPolicy",
)

# Signal words -> a coarse category hint (used when no HTTP status is present).
_SIGNAL_WORDS: list[tuple[str, str]] = [
    ("could not be found", "not-found"),
    ("does not exist", "not-found"),
    ("not found", "not-found"),
    ("no longer exists", "not-found"),
    ("unauthorized", "authentication"),
    ("not authenticated", "authentication"),
    ("invalid credentials", "authentication"),
    ("token expired", "authentication"),
    ("access denied", "permission"),
    ("forbidden", "permission"),
    ("permission", "permission"),
    ("not allowed", "permission"),
    ("already exists", "conflict"),
    ("conflict", "conflict"),
    ("rate limit", "rate-limit"),
    ("too many requests", "rate-limit"),
    ("throttl", "rate-limit"),
    ("timed out", "timeout"),
    ("timeout", "timeout"),
    ("deadline exceeded", "timeout"),
    ("is required", "parameter-schema"),
    ("required parameter", "parameter-schema"),
    ("missing", "parameter-schema"),
    ("could not be found", "parameter-schema"),
    ("not valid", "validation"),
    ("invalid", "validation"),
    ("malformed", "validation"),
    ("bad request", "validation"),
]

_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_]\w*\}")
_STATUS_NEAR_RE = re.compile(
    r"(?:status|code|returned|http|error)\D{0,12}\b([1-5]\d\d)\b", re.IGNORECASE
)
_STATUS_ANY_RE = re.compile(r"\b([1-5]\d\d)\b")

# HTTP status lexicon: code -> (category, cause, fix)
_HTTP_LEXICON: dict[int, tuple[str, str, str]] = {
    400: (
        "validation",
        "The service rejected the request as invalid (HTTP 400).",
        "Check the parameters and request body against the tool's expected schema and value formats.",
    ),
    401: (
        "authentication",
        "The request was not authenticated (HTTP 401).",
        "Reconnect the tool/connector or refresh its credentials so a valid token is sent.",
    ),
    403: (
        "permission",
        "Authentication succeeded but the caller lacks permission for this operation (HTTP 403).",
        "Grant the connection/identity the required permission (or scope/role) on the target resource.",
    ),
    404: (
        "not-found",
        "The target resource was not found (HTTP 404).",
        "Verify the id/path/address points at an existing resource and that any placeholders are resolved.",
    ),
    405: (
        "validation",
        "The HTTP method is not allowed for this endpoint (HTTP 405).",
        "Use the operation/verb the tool actually supports for this resource.",
    ),
    408: (
        "timeout",
        "The request timed out before completing (HTTP 408).",
        "Retry; if it persists, reduce the payload or check the target service's responsiveness.",
    ),
    409: (
        "conflict",
        "The request conflicts with the current state of the resource (HTTP 409).",
        "Reconcile the conflicting state (e.g. an existing record or a stale version) before retrying.",
    ),
    422: (
        "validation",
        "The request was well-formed but semantically invalid (HTTP 422).",
        "Correct the field values that violate the tool's validation rules.",
    ),
    429: (
        "rate-limit",
        "The tool was throttled for sending too many requests (HTTP 429).",
        "Back off and retry with a delay; reduce call frequency or batch the work.",
    ),
    500: (
        "server-error",
        "The target service failed internally (HTTP 500).",
        "This is server-side; retry later and check the service's health if it persists.",
    ),
    502: (
        "server-error",
        "The gateway received an invalid response from the upstream service (HTTP 502).",
        "Retry later; the upstream/connector service is unhealthy.",
    ),
    503: (
        "server-error",
        "The target service is unavailable (HTTP 503).",
        "Retry later; the service is down or overloaded.",
    ),
    504: (
        "timeout",
        "The gateway timed out waiting for the upstream service (HTTP 504).",
        "Retry; if it persists the upstream service is too slow or unreachable.",
    ),
}


# --- KB loader (mirrors explainer.py: lru_cache + graceful fallback) ---------


@lru_cache(maxsize=1)
def _load_rules() -> list[dict]:
    """Load the optional named-signature refinement rules. Absent/invalid KB -> []."""
    try:
        data = yaml.safe_load(_KB_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning(f"Tool-diagnosis KB not found at {_KB_PATH} — using generic tiers only")
        return []
    if not isinstance(data, dict):
        return []
    rules = data.get("rules", [])
    return [r for r in rules if isinstance(r, dict)]


# ============================================================================
# describe() — activity narration for EVERY call
# ============================================================================


def _humanize(name: str, display_name: str | None) -> tuple[str | None, str, str]:
    """Return (kind, verb_phrase, object_phrase) from a tool name, generically.

    kind is "read" / "write" / None. Falls back to the display name when no verb
    token is recognised (e.g. skill invocations).
    """
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+", name or "")
    lowered = [w.lower() for w in words]
    joined = "".join(lowered)
    for token in _VERB_TOKENS:
        for i, word in enumerate(lowered):
            if word.startswith(token) or token == word:
                kind, verb = _VERB_LEXICON[token]
                remainder = word[len(token):]
                obj_parts = ([remainder] if remainder else []) + lowered[i + 1 :] + lowered[:i]
                obj = " ".join(p for p in obj_parts if p).strip()
                return kind, verb, obj
        # also catch verb embedded in a single lowercase blob (e.g. "getitem")
        idx = joined.find(token)
        if idx == 0:
            kind, verb = _VERB_LEXICON[token]
            obj = joined[len(token):]
            return kind, verb, obj
    return None, "", ""


def _short(value: object, limit: int = 48) -> str:
    """Collapse whitespace and shorten a value for the one-line summary only.

    JSON-ish structured values are replaced by a compact marker; the full,
    untruncated value is surfaced elsewhere (the Params drop-down).
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    stripped = text.strip()
    if stripped[:1] in "{[":
        return "{…}" if stripped[:1] == "{" else "[…]"
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


_SALIENT_KEYS = ("query", "id", "name", "key", "path", "url", "table", "dataset", "filter")


def _salient_inputs(params: dict) -> str:
    """A short 'with id=…, query=…' clause from the most meaningful params."""
    if not isinstance(params, dict) or not params:
        return ""
    ordered: list[str] = [k for k in _SALIENT_KEYS if k in params]
    ordered += [k for k in params if k not in ordered]
    parts: list[str] = []
    for key in ordered[:3]:
        val = _short(params[key])
        if val == "":
            continue
        quote = isinstance(params[key], str) and val not in ("{…}", "[…]") and " " in val
        parts.append(f"{key}='{val}'" if quote else f"{key}={val}")
    return ", ".join(parts)


def _first_line(text: str, limit: int = 80) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _summarize_result(tc: ToolCall) -> str:
    """Plain-language outcome for a **successful** call."""
    if tc.result_count is not None:
        n = tc.result_count
        return "returned no results" if n == 0 else f"returned {n} result{'s' if n != 1 else ''}"
    if tc.zero_result:
        return "returned no results"
    body = tc.result
    if not body:
        return "completed"
    stripped = body.strip()
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        line = _first_line(stripped)
        return f"returned: {line}" if line else "completed"
    if isinstance(parsed, list):
        return f"returned {len(parsed)} item{'s' if len(parsed) != 1 else ''}"
    if isinstance(parsed, dict):
        for key in ("message", "summary", "description"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                return f"returned: {_first_line(parsed[key])}"
        label = ""
        for key in ("Title", "title", "name", "Name", "id", "ID"):
            if key in parsed and parsed[key] not in (None, ""):
                label = f" ({key}={_short(parsed[key], 32)})"
                break
        return f"returned 1 record{label}"
    return "completed"


def describe(tc: ToolCall) -> str:
    """A tool-agnostic, plain-language summary of what a call did — for any outcome."""
    name = tc.name or "tool"
    kind, verb, obj = _humanize(name, tc.display_name)

    if verb:
        action = f"{verb} {obj}".strip() if obj else verb
    elif tc.display_name:
        action = tc.display_name.strip()
    else:
        action = f"Ran {name}"

    inputs = _salient_inputs(tc.params)
    clause = f"{action} with {inputs}" if inputs else action

    if tc.failed or (tc.error and tc.error.strip()):
        return f"{clause} → failed (see diagnosis)"
    return f"{clause} → {_summarize_result(tc)}"


# ============================================================================
# diagnose() — tiered, generic, always-populated failure diagnosis
# ============================================================================


def is_failure(tc: ToolCall) -> bool:
    """True when a call failed outright OR carries an error signature behind a
    'completed' status. Generic — no tool-specific knowledge."""
    if tc.failed:
        return True
    if tc.error and tc.error.strip():
        return True
    if tc.result and _EMBEDDED_ERROR_RE.search(tc.result):
        return True
    return False


_EMBEDDED_ERROR_RE = re.compile(
    r"(error executing tool|Connector returned\s+[1-5]\d\d|\"?error\"?\s*[:=]|"
    r"exception|failed to|could not|not valid|not found|unauthorized|forbidden)",
    re.IGNORECASE,
)


def _http_status(text: str) -> int | None:
    if not text:
        return None
    m = _STATUS_NEAR_RE.search(text)
    if m:
        return int(m.group(1))
    m = _STATUS_ANY_RE.search(text)
    return int(m.group(1)) if m else None


def _harvest_json(text: str) -> dict:
    """Best-effort parse of the first {...} block; return the fields of interest."""
    if not text or "{" not in text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if end <= start:
        return {}
    blob = text[start : end + 1]
    for candidate in (blob, blob.replace("\r", "").replace("\n", " ")):
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return {k: parsed[k] for k in _JSON_FIELDS if k in parsed}
    return {}


def _placeholders(tc: ToolCall) -> list[str]:
    found: list[str] = []
    text = tc.error or tc.result or ""
    found += _PLACEHOLDER_RE.findall(text)
    if isinstance(tc.params, dict):
        for val in tc.params.values():
            if isinstance(val, str):
                found += _PLACEHOLDER_RE.findall(val)
    # de-dupe, preserve order
    seen: dict[str, None] = {}
    for tok in found:
        seen.setdefault(tok, None)
    return list(seen)


def _echoed_params(tc: ToolCall, text: str) -> list[str]:
    if not isinstance(tc.params, dict) or not text:
        return []
    low = text.lower()
    return [k for k in tc.params if k and k.lower() in low]


def _signals(text: str) -> list[tuple[str, str]]:
    if not text:
        return []
    low = text.lower()
    return [(w, cat) for (w, cat) in _SIGNAL_WORDS if w in low]


def _verb_kind(name: str) -> str | None:
    kind, _, _ = _humanize(name, None)
    return kind


def _sibling_read_succeeded(tc: ToolCall, siblings) -> bool:
    for other in siblings or ():
        if other is tc:
            continue
        if not other.failed and not (other.error and other.error.strip()):
            if _verb_kind(other.name or "") == "read":
                return True
    return False


def _retry_count(tc: ToolCall, siblings) -> int:
    """How many calls in the turn share this tool's base name (retry family)."""
    base = (tc.name or "").split("_")[0].lower()
    if not base:
        return 1
    return sum(1 for o in (siblings or ()) if (o.name or "").split("_")[0].lower() == base)


def _match_rule(tc: ToolCall, text: str, status: int | None) -> dict | None:
    """First matching named rule wins the wording override."""
    name = tc.name or ""
    for rule in _load_rules():
        when = rule.get("when", {}) if isinstance(rule.get("when"), dict) else {}
        nr = when.get("name_regex")
        if nr and not re.search(nr, name, re.IGNORECASE):
            continue
        er = when.get("error_regex")
        if er and not re.search(er, text or "", re.IGNORECASE | re.DOTALL):
            continue
        st = when.get("status")
        if st is not None and st != status:
            continue
        pp = when.get("param_present")
        if pp and not (isinstance(tc.params, dict) and pp in tc.params):
            continue
        pa = when.get("param_absent")
        if pa and isinstance(tc.params, dict) and pa in tc.params:
            continue
        return rule
    return None


def diagnose(tc: ToolCall, siblings=()) -> ToolDiagnosis:
    """Return a populated diagnosis for a failed call — never empty.

    For a non-failed call returns the neutral sentinel (category='none').
    """
    if not is_failure(tc):
        return ToolDiagnosis(category="none", cause="", fix="", severity="info")

    text = (tc.error or tc.result or "").strip()
    evidence: list[str] = []

    # --- Tier 0: universal payload mining -----------------------------------
    status = _http_status(text)
    jfields = _harvest_json(text)
    placeholders = _placeholders(tc)
    echoed = _echoed_params(tc, text)
    signals = _signals(text)

    if status:
        evidence.append(f"HTTP status {status}")
    for key, val in jfields.items():
        if key in ("message", "error", "detail", "description", "reason", "title"):
            evidence.append(f'{key}: "{_first_line(str(val))}"')
        else:
            evidence.append(f"{key}={val}")
    if placeholders:
        evidence.append(f"unresolved placeholder(s): {', '.join(placeholders)}")
    if echoed:
        evidence.append(f"error references parameter(s): {', '.join(echoed)}")

    harvested_msg = ""
    for key in ("message", "error", "detail", "description", "reason", "title"):
        val = jfields.get(key)
        if isinstance(val, str) and val.strip():
            harvested_msg = _first_line(val)
            break
    if not harvested_msg:
        harvested_msg = _first_line(text)

    # --- Tier 1: HTTP status lexicon ----------------------------------------
    category, cause, fix, severity = "unknown", "", "", "error"
    if status in _HTTP_LEXICON:
        category, cause, fix = _HTTP_LEXICON[status]
    elif status is not None and 500 <= status <= 599:
        category, cause, fix = _HTTP_LEXICON[500]
    elif status is not None and 400 <= status <= 499:
        category, cause, fix = _HTTP_LEXICON[400]

    # --- Tier 1b: signal words when no status decided a category ------------
    if category == "unknown" and signals:
        category = signals[0][1]
        cause = f"The tool reported: {harvested_msg}" if harvested_msg else "The tool reported a failure."
        fix = "Address the condition named in the error above, then retry."

    # attach harvested message to the cause when we have a status-based category
    if status is not None and harvested_msg:
        cause = f"{cause} The service said: {harvested_msg}"

    # --- Tier 2: structural / behavioural heuristics ------------------------
    if placeholders:
        category = "configuration"
        toks = ", ".join(placeholders)
        cause = (
            f"An unresolved placeholder ({toks}) was sent literally — a configuration "
            f"value was never substituted, so the request never reached a real target."
        )
        fix = (
            f"Set the connection/environment configuration so {toks} resolves to a real "
            f"value; it is not something the request body can override."
        )

    if _sibling_read_succeeded(tc, siblings) and _verb_kind(tc.name or "") == "write":
        evidence.append("a read on the same resource in this turn succeeded (read-ok / write-fail)")
        if category in ("unknown", "validation"):
            category = "permission" if not placeholders else category
            if category == "permission":
                cause = (
                    "Reads on this resource succeed but the write failed — the connection "
                    "likely lacks write permission or write-side configuration."
                )
                fix = "Grant write permission (or fix the write-path config) for the connection on this resource."

    retries = _retry_count(tc, siblings)
    if retries >= 2:
        evidence.append(f"tool retried {retries}× in this turn, all failing")

    if not cause:
        cause = f"The tool reported a failure: {harvested_msg}" if harvested_msg else "The tool reported a failure."
    if not fix:
        fix = "Inspect the full error body above and verify the parameters and the tool/connector configuration."

    # --- Tier 3: named-signature refinement (wording override only) ---------
    matched_rule = None
    first = _match_rule(tc, text, status)
    if first:
        matched_rule = first.get("id")
        if first.get("category") in CATEGORIES:
            category = first["category"]
        if first.get("cause"):
            cause = " ".join(str(first["cause"]).split())
        if first.get("fix"):
            fix = " ".join(str(first["fix"]).split())
        if first.get("severity"):
            severity = first["severity"]
        evidence.append(f"matched rule: {matched_rule}")

    if category not in CATEGORIES:
        category = "unknown"

    return ToolDiagnosis(
        category=category,
        cause=cause.strip(),
        fix=fix.strip(),
        severity=severity,
        evidence=evidence,
        matched_rule=matched_rule,
    )
