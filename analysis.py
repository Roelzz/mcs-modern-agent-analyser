"""Heuristic analysis for modern Copilot Studio agents.

Pure functions over an `AgentProfile` and/or a `Conversation`, producing the
result models in `models.py`. No timing/routing data exists in modern
transcripts, so every signal here is count/structure/text based.

Either input may be ``None``; the orchestrator `analyze()` degrades gracefully.
"""

from __future__ import annotations

import re

from loguru import logger

from models import (
    AgentProfile,
    AnalysisReport,
    AnswerGroundedness,
    AnswerGroundednessAnalysis,
    CitationAnalysis,
    CitationAudit,
    CitationAuditRow,
    Conversation,
    ConversationOverview,
    CoverageGap,
    CoverageGapAnalysis,
    CreditEstimate,
    CreditLineItem,
    CrossReference,
    DuplicateGroup,
    Finding,
    GroundednessAssessment,
    InstructionCheck,
    InstructionCompliance,
    KnowledgeAnalysis,
    KnowledgeEffectiveness,
    KnowledgeQuery,
    QuoteCheck,
    QuoteFaithfulness,
    ReasoningTrace,
    RepetitionAnalysis,
    RepetitionSignal,
    RetrievedDoc,
    SourceEffectiveness,
    ToolAnalysis,
    ToolCall,
    ToolEfficiency,
    ToolFailure,
    ToolFailureAnalysis,
    ToolOperation,
    ToolProvider,
    ToolUsage,
    TurnEconomy,
)
from models import (
    CodeInterpreterAnalysis,
    DocRetrieval,
    GeneratedArtifact,
    GeneratedArtifacts,
    GroundingDoc,
    GroundingPipeline,
    KnowledgeFolder,
    RecallTurn,
    RetrievalDepth,
    SandboxFriction,
    SandboxSignal,
    SearchPrecision,
    SearchStrategy,
    SkillGap,
    SkillUse,
)

from config import (
    CREDIT_ESTIMATOR_URL,
    CREDIT_SOURCE_URL,
    chars_per_token,
    code_interpreter_keywords,
    credit_rates,
    heuristic_thresholds,
    reasoning_model_series,
)

_THRESH = heuristic_thresholds()

# Tool names that are runtime built-ins rather than YAML-defined actions.
_BUILTIN_TOOLS = {
    "knowledgesearch",
    "skill",
    "bash",
    "view",
    "grep",
    "create",
    "edit",
    "str_replace",
    "str_replace_editor",
    "python",
    "read_file",
    "write_file",
}

# Taxonomy mirrors web.view_models.classify_tool (kept independent so the
# analysis layer never imports from web/). retrieval / action / skill / other.
_ACTION_PARAM_KEYS = {"content", "contenttype", "memberupns", "useridorupn", "recipient", "chatid", "messageid"}
_ACTION_NAME_PREFIXES = ("send", "list", "create", "update", "delete", "post", "get", "add", "remove")


def classify_tool_kind(tc: ToolCall) -> str:
    """retrieval (knowledge search) / action (side-effect) / skill / other."""
    if tc.is_knowledge_search:
        return "retrieval"
    name = (tc.name or "").lower()
    display = (tc.display_name or "").lower()
    if name == "skill" or "loaded skill" in display or "skill:" in display:
        return "skill"
    params = tc.params if isinstance(tc.params, dict) else {}
    if any(k.lower() in _ACTION_PARAM_KEYS for k in params):
        return "action"
    if name.startswith(_ACTION_NAME_PREFIXES):
        return "action"
    return "other"


# Provider grouping for the component hierarchy ----------------------------------
# Precise badge + lucide icon + KB key per provider kind. Shared by the
# view-model and the markdown renderer so both render the same hierarchy.
PROVIDER_META = {
    "mcpServer": ("MCP server", "server", "mcpServer"),
    "connector": ("Connector", "plug", "connector"),
    "connectedAgent": ("Connected agent", "bot", "connectedAgent"),
    "flow": ("Flow", "workflow", "flow"),
    "skill": ("Skill", "puzzle", "skill"),
    "action": ("Action", "wrench", "tool"),
}
_SKILL_PREFIXES = ("loaded skill", "skill")


def classify_runtime_provider(tc: ToolCall) -> tuple[str, str, str, str | None] | None:
    """Infer (provider_kind, provider_name, op_name, op_display) for one runtime
    tool call, or None if it isn't an action/skill. MCP servers expose tools as
    ``Server:tool``; skills surface as ``Loaded Skill: <name>``; everything else
    is grouped as a neutral agent action."""
    if tc.is_knowledge_search:
        return None
    kind = classify_tool_kind(tc)
    if kind not in {"action", "skill"}:
        return None
    name = (tc.name or "").strip()
    display = (tc.display_name or "").strip()
    for cand in (name, display):
        if ":" in cand:
            left, right = cand.split(":", 1)
            left, right = left.strip(), right.strip()
            if left and right and left.lower() not in _SKILL_PREFIXES:
                return ("mcpServer", left, right, None)
    if kind == "skill":
        op = display.split(":", 1)[1].strip() if ":" in display else (name or "skill")
        return ("skill", "Skills", op or "skill", None)
    op_name = name or display or "action"
    op_disp = display if display and display != op_name else None
    return ("action", "Agent actions", op_name, op_disp)


def _merge_operation(provider: ToolProvider, name: str, display: str | None, desc: str | None, configured: bool) -> None:
    key = (name or "").lower()
    for op in provider.operations:
        if (op.name or "").lower() == key:
            op.configured = op.configured or configured
            if not op.display_name and display:
                op.display_name = display
            if not op.description and desc:
                op.description = desc
            return
    provider.operations.append(ToolOperation(name=name, display_name=display, description=desc, configured=configured))


def build_tool_hierarchy(profile: AgentProfile | None, convo: Conversation | None) -> list[ToolProvider]:
    """Merge YAML-declared tool providers with runtime-observed tool calls into a
    single Provider→Operation hierarchy. Fresh model instances are returned, so
    the input ``profile`` is never mutated."""
    providers: list[ToolProvider] = []
    index: dict[tuple[str, str], ToolProvider] = {}
    norm_index: dict[str, ToolProvider] = {}

    def _norm(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (name or "").lower())

    def get_or_make(kind: str, name: str, configured: bool, **kw) -> ToolProvider:
        # Match an existing provider by normalized name first, so a runtime call
        # like ``ZavaExpenseMCP:tool`` attaches to the declared ``Zava Expense MCP``
        # provider (and inherits its precise kind) instead of forking a duplicate.
        existing = norm_index.get(_norm(name)) or index.get((kind, (name or "").lower()))
        if existing is not None:
            return existing
        pr = ToolProvider(
            kind=kind,
            display_name=name or kind,
            configured=configured,
            schema_name=kw.get("schema_name"),
            description=kw.get("description"),
            source=kw.get("source"),
        )
        index[(kind, (name or "").lower())] = pr
        norm_index[_norm(name)] = pr
        providers.append(pr)
        return pr

    if profile is not None:
        for cp in profile.tool_providers:
            pr = get_or_make(
                cp.kind, cp.display_name, configured=True,
                schema_name=cp.schema_name, description=cp.description, source=cp.source,
            )
            pr.configured = True
            for op in cp.operations:
                _merge_operation(pr, op.name, op.display_name, op.description, configured=True)

    if convo is not None:
        for tc in convo.tool_calls:
            inferred = classify_runtime_provider(tc)
            if inferred is None:
                continue
            kind, pname, opname, opdisp = inferred
            pr = get_or_make(kind, pname, configured=False)
            _merge_operation(pr, opname, opdisp, None, configured=False)

    return providers


_CITATION_RE = re.compile(r"\[(\d+)\]")
_REFID_RE = re.compile(r"\bturn\d+doc\d+\b", re.IGNORECASE)

_RETRY_RE = re.compile(
    r"(different approach|try again|didn'?t work|that failed|let me try|read .* properly|"
    r"a different|another approach|retry|wasn'?t able)",
    re.IGNORECASE,
)
_PREMISE_RE = re.compile(
    r"(premise of your question|small correction|actually,|that'?s not quite|"
    r"isn'?t quite right|to clarify|correction:)",
    re.IGNORECASE,
)
_HONEST_GAP_RE = re.compile(
    r"(does not mention|doesn'?t mention|not mention|couldn'?t find|could not find|"
    r"no specific|does not (specify|contain)|doesn'?t (specify|contain)|isn'?t mentioned|"
    r"i (don'?t|do not) have|not available in)",
    re.IGNORECASE,
)
_INTERMEDIATE_RE = re.compile(
    r"^(let me|i'?ll|i will|one moment|searching|good question|great[!,. ]|sure[!,. ]|"
    r"let'?s|on it|looking|checking|hold on)",
    re.IGNORECASE,
)

_SUBSTANTIVE_MIN = 120  # chars; below this an answer is too short to judge groundedness


def _looks_intermediate(text: str) -> bool:
    """A short streaming/filler message ('Good question! Let me search...')."""
    t = text.strip()
    if not t:
        return True
    if t.endswith("...") and len(t) < 200:
        return True
    return bool(_INTERMEDIATE_RE.match(t)) and len(t) < 200


def _normalize_title(title: str | None) -> str:
    """Strip extension + punctuation, lowercase — for loose title matching."""
    if not title:
        return ""
    base = re.sub(r"\.[a-z0-9]{1,5}$", "", title.strip(), flags=re.IGNORECASE)
    base = re.sub(r"[^a-z0-9]+", " ", base.lower())
    return re.sub(r"\s+", " ", base).strip()


def _site_root(url: str | None) -> str | None:
    """Return scheme://host[/sites/<site>] for grouping retrieved docs by source."""
    if not url:
        return None
    m = re.match(r"(https?://[^/]+(?:/sites/[^/]+)?)", url, re.IGNORECASE)
    return m.group(1) if m else None


def _truncate(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def estimate_tokens(text: str | None) -> int:
    """Heuristic token count for an answer (≈ chars / 4). Estimates only."""
    if not text:
        return 0
    return int(len(text) / chars_per_token())


def parse_sharepoint_path(url: str | None) -> tuple[str, str] | None:
    """Extract (folder_path, area) from a SharePoint doc URL.

    e.g. ``.../HR-Policies/Recruitment-and-Onboarding/Hiring/Job-Posting.docx``
    → (``Recruitment-and-Onboarding/Hiring``, ``Recruitment-and-Onboarding``).
    Returns None when no meaningful folder structure is present."""
    if not url:
        return None
    path = re.sub(r"^https?://[^/]+", "", url)  # drop scheme+host
    path = path.split("?", 1)[0].split("#", 1)[0]
    segs = [s for s in path.split("/") if s]
    # Drop SharePoint plumbing + the trailing filename.
    drop = {"sites", "shared documents", "documents", "_layouts", "forms"}
    cleaned: list[str] = []
    for s in segs[:-1]:  # exclude filename
        dec = s.replace("%20", " ").strip()
        if dec.lower() in drop:
            cleaned = []  # restart after a plumbing marker (site root)
            continue
        cleaned.append(dec)
    # Strip a leading library/root container that's the same for every doc.
    if len(cleaned) > 1 and cleaned[0].lower().endswith(("-policies", " policies", "-library")):
        cleaned = cleaned[1:]
    if not cleaned:
        return None
    return ("/".join(cleaned), cleaned[0])


def reference_index(convo: Conversation | None) -> dict[str, dict]:
    """Map each retrieved doc's ReferenceId → {title, url, turns, count, queries}.

    Lets us measure cross-search overlap (B2) and which turn first saw a doc (C1)."""
    idx: dict[str, dict] = {}
    if convo is None:
        return idx
    for turn in convo.turns:
        for tc in turn.tool_calls:
            if not tc.is_knowledge_search:
                continue
            for d in tc.retrieved_docs:
                key = d.reference_id or (d.url or d.title or "")
                if not key:
                    continue
                rec = idx.setdefault(
                    key,
                    {"title": d.title or "", "url": d.url, "turns": [], "count": 0, "first_turn": turn.index},
                )
                rec["count"] += 1
                if turn.index not in rec["turns"]:
                    rec["turns"].append(turn.index)
                if not rec["title"] and d.title:
                    rec["title"] = d.title
    return idx


_SANDBOX_PREAMBLE_RE = re.compile(
    r"(full documents? saved|/app/uploads|use bash|use grep|view the file|saved to .*sandbox|"
    r"snippets? (are|below are) summar)",
    re.IGNORECASE,
)


def code_interpreter_signals(turn) -> list[SandboxSignal]:
    """Detect sandbox / code-interpreter activity in a turn's thoughts + tool-result
    preambles. Heuristic keyword match — these tools never appear as toolCalls in
    modern transcripts, so this is the only way to surface them."""
    patterns = _code_interpreter_patterns()
    hay: list[str] = [t.text for t in turn.thoughts if t.text]
    for tc in turn.tool_calls:
        if tc.is_knowledge_search:
            for d in tc.retrieved_docs:
                if d.snippet:
                    hay.append(d.snippet)
        if tc.result:
            hay.append(tc.result)
    signals: list[SandboxSignal] = []
    seen: set[tuple[int, str, str]] = set()
    for text in hay:
        for category, rx in patterns:
            m = rx.search(text)
            if m:
                tool = m.group(0).strip().lower()
                key = (turn.index, category, tool)
                if key in seen:
                    break
                seen.add(key)
                signals.append(
                    SandboxSignal(
                        turn_index=turn.index,
                        category=category,
                        tool=tool,
                        excerpt=_truncate(text, 200),
                    )
                )
                break  # most-specific category wins for this fragment
    return signals


_CI_PATTERNS_CACHE: list[tuple[str, re.Pattern]] | None = None


def _code_interpreter_patterns() -> list[tuple[str, re.Pattern]]:
    """Compile the code-interpreter keyword sets into (category, regex) pairs.
    Alphanumeric tokens get word boundaries to avoid false hits (e.g. 'review'
    must not match 'view'); path-like tokens match as substrings."""
    global _CI_PATTERNS_CACHE
    if _CI_PATTERNS_CACHE is not None:
        return _CI_PATTERNS_CACHE
    kw = code_interpreter_keywords()
    order = [
        ("authoring", kw.get("authoring", [])),
        ("permissions", kw["perms"]),
        ("read-document", kw["tools"]),
        ("preprocess", kw["code"]),
        ("inspect-fs", kw["fs"]),
        ("shell-other", kw["shell"]),
    ]
    compiled: list[tuple[str, re.Pattern]] = []
    for category, words in order:
        parts: list[str] = []
        for w in words:
            w = w.strip()
            if not w:
                continue
            parts.append(rf"\b{re.escape(w)}\b" if re.fullmatch(r"[a-z0-9]+", w) else re.escape(w))
        if parts:
            compiled.append((category, re.compile("|".join(parts), re.IGNORECASE)))
    _CI_PATTERNS_CACHE = compiled
    return compiled


# Embedded tool error: a call can report status "completed" yet carry an
# "Error executing tool: …" message in its result text (modern action tools wrap
# failures in their JSON result). Detect both the explicit-status and embedded forms.
_TOOL_ERROR_RE = re.compile(r"error executing tool", re.IGNORECASE)
_ERROR_LINE_RE = re.compile(r"Error executing tool:\s*(.+)", re.IGNORECASE)


def _unescape_lite(s: str) -> str:
    return (
        s.replace("\\r\\n", " ")
        .replace("\\n", " ")
        .replace("\\r", " ")
        .replace("\\u0027", "'")
        .replace("\\u0022", '"')
        .replace("\\u2026", "…")
    )


def tool_failed(tc: ToolCall) -> bool:
    """Semantic failure: an explicit failed status OR an embedded error in the result."""
    if tc.failed:
        return True
    return bool(tc.result and _TOOL_ERROR_RE.search(tc.result))


def _is_embedded_failure(tc: ToolCall) -> bool:
    """A failure that the status field hides behind 'completed'."""
    return (not tc.failed) and bool(tc.result and _TOOL_ERROR_RE.search(tc.result))


def _extract_error_text(tc: ToolCall, limit: int = 180) -> str:
    if not tc.result:
        return tc.status or "failed"
    m = _ERROR_LINE_RE.search(tc.result)
    if m:
        return _truncate(_unescape_lite(m.group(1)), limit)
    return _truncate(_unescape_lite(tc.result), limit)


def _params_summary(tc: ToolCall, limit: int = 90) -> str:
    if not isinstance(tc.params, dict) or not tc.params:
        return ""
    parts: list[str] = []
    for k, v in tc.params.items():
        vs = " ".join(str(v).split())
        vs = vs if len(vs) <= 40 else vs[:39] + "…"
        parts.append(f"{k}={vs}")
    return _truncate(" ".join(parts), limit)


def _norm_params(tc: ToolCall) -> str:
    """Stable normalized signature of a call's params, for redundancy grouping."""
    if not isinstance(tc.params, dict) or not tc.params:
        return ""
    items = sorted((str(k), " ".join(str(v).split()).lower()) for k, v in tc.params.items())
    return "|".join(f"{k}={v}" for k, v in items)


def _normalize_text(text: str) -> str:
    """Lowercase, strip markup/punctuation, collapse whitespace — for substring/quote matching."""
    if not text:
        return ""
    base = re.sub(r"[`*_>#\[\]()]+", " ", text.lower())
    base = re.sub(r"[^a-z0-9 ]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# Sentence + claim + quote heuristics for per-answer grounding (#2) and quotes (#11).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_FACTUAL_RE = re.compile(
    r"\b(is|are|was|were|must|will|shall|require[sd]?|include[sd]?|allow[sed]*|"
    r"prohibit[sed]*|provide[sd]?|consist[s]?|equal[s]?|\d)\b",
    re.IGNORECASE,
)
_NONFACTUAL_START_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|sure|of course|let me|i'?ll|i can|i'?d|would you|"
    r"could you|do you|is there anything|happy to|great|certainly|no problem|here'?s|"
    r"absolutely|got it|understood)",
    re.IGNORECASE,
)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.+)$", re.MULTILINE)
_DQUOTE_RE = re.compile(r"[\u201c\"]([^\u201d\"]{15,300})[\u201d\"]")


def _factual_claim(sentence: str) -> bool:
    s = sentence.strip()
    if len(s) < 25 or s.endswith("?"):
        return False
    if _NONFACTUAL_START_RE.match(s):
        return False
    return bool(_FACTUAL_RE.search(s))


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def build_overview(convo: Conversation) -> ConversationOverview:
    tool_calls = convo.tool_calls
    searches = [t for t in tool_calls if t.is_knowledge_search]
    return ConversationOverview(
        turn_count=len(convo.turns),
        user_message_count=len(convo.user_messages),
        bot_message_count=len(convo.bot_messages),
        tool_call_count=len(tool_calls),
        knowledge_search_count=len(searches),
        thought_count=len(convo.thoughts),
        failed_tool_count=sum(1 for t in tool_calls if tool_failed(t)),
        zero_result_search_count=sum(1 for t in searches if t.zero_result),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def analyze_tools(convo: Conversation) -> ToolAnalysis:
    usage: dict[str, ToolUsage] = {}
    skill_loads: list[str] = []
    failures: list[str] = []

    for tc in convo.tool_calls:
        name = tc.name or "(unnamed)"
        u = usage.setdefault(name, ToolUsage(name=name))
        u.count += 1
        if tc.failed:
            u.failed += 1
            failures.append(f"{name}: {tc.display_name or tc.status}")
        elif (tc.status or "").lower() == "completed":
            u.completed += 1

        disp = tc.display_name or ""
        if name.lower() == "skill" or disp.lower().startswith("loaded skill"):
            skill_loads.append(disp or name)

    retry_signals: list[str] = []
    for th in convo.thoughts:
        if _RETRY_RE.search(th.text):
            retry_signals.append(_truncate(th.text))

    return ToolAnalysis(
        usage=sorted(usage.values(), key=lambda u: -u.count),
        skill_loads=skill_loads,
        retry_signals=retry_signals,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


def analyze_knowledge(convo: Conversation) -> KnowledgeAnalysis:
    queries: list[KnowledgeQuery] = []
    distinct: dict[str, RetrievedDoc] = {}
    sources: set[str] = set()

    for tc in convo.tool_calls:
        if not tc.is_knowledge_search:
            continue
        queries.append(
            KnowledgeQuery(
                query=tc.query or "(no query)",
                result_count=tc.result_count if tc.result_count is not None else len(tc.retrieved_docs),
                docs=tc.retrieved_docs,
                zero_result=tc.zero_result,
            )
        )
        for doc in tc.retrieved_docs:
            key = doc.reference_id or doc.title or doc.url or ""
            if key and key not in distinct:
                distinct[key] = doc
            root = _site_root(doc.url)
            if root:
                sources.add(root)

    # A retrieved doc is "used" if its (normalised) title or reference id shows
    # up anywhere in the bot's text. Everything else is an unused retrieval.
    bot_blob = " ".join(_normalize_title(m.text) for m in convo.bot_messages)
    bot_raw = " ".join(m.text for m in convo.bot_messages)
    cited_refids = sorted(set(_REFID_RE.findall(bot_raw)))

    uncited: list[RetrievedDoc] = []
    for doc in distinct.values():
        norm = _normalize_title(doc.title)
        used = (norm and norm in bot_blob) or (doc.reference_id and doc.reference_id in bot_raw)
        if not used:
            uncited.append(doc)

    return KnowledgeAnalysis(
        queries=queries,
        distinct_docs=list(distinct.values()),
        sources_seen=sorted(sources),
        cited_reference_ids=cited_refids,
        uncited_docs=uncited,
        zero_result_queries=[q.query for q in queries if q.zero_result],
    )


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def analyze_citations(convo: Conversation) -> CitationAnalysis:
    total_markers = 0
    refids_in_results: set[str] = set()

    for m in convo.bot_messages:
        total_markers += len(_CITATION_RE.findall(m.text))
        for tc in m.tool_calls:
            if tc.result:
                refids_in_results.update(r.lower() for r in _REFID_RE.findall(tc.result))

    bot_raw = " ".join(m.text for m in convo.bot_messages)
    cited = sorted({r.lower() for r in _REFID_RE.findall(bot_raw)})

    # An "uncited answer" = a substantive final answer in a user-led turn that
    # carries no [n] citation and whose turn ran no knowledge search.
    uncited_answers = 0
    for turn in convo.turns:
        if turn.user_message is None:
            continue
        final = turn.final_bot_text
        if len(final.strip()) < _SUBSTANTIVE_MIN or _looks_intermediate(final):
            continue
        has_citation = bool(_CITATION_RE.search(final))
        ran_search = any(tc.is_knowledge_search for tc in turn.tool_calls)
        if not has_citation and not ran_search:
            uncited_answers += 1

    return CitationAnalysis(
        total_markers=total_markers,
        reference_ids_in_results=sorted(refids_in_results),
        cited_reference_ids=cited,
        uncited_answer_count=uncited_answers,
    )


# ---------------------------------------------------------------------------
# Reasoning
# ---------------------------------------------------------------------------


def analyze_reasoning(convo: Conversation) -> ReasoningTrace:
    retry_signals = [_truncate(th.text) for th in convo.thoughts if _RETRY_RE.search(th.text)]

    premise: list[str] = []
    for m in convo.bot_messages:
        if _PREMISE_RE.search(m.text):
            premise.append(_truncate(m.text, 200))

    return ReasoningTrace(
        total_thoughts=len(convo.thoughts),
        thoughts_per_turn=[len(t.thoughts) for t in convo.turns],
        retry_signals=retry_signals,
        premise_corrections=premise,
    )


# ---------------------------------------------------------------------------
# Groundedness
# ---------------------------------------------------------------------------


def assess_groundedness(convo: Conversation, knowledge: KnowledgeAnalysis | None = None) -> GroundednessAssessment:
    grounded = 0
    ungrounded = 0
    hallucination: list[str] = []
    honest: list[str] = []
    notes: list[str] = []

    used_titles = set()
    if knowledge:
        used_doc_keys = {d.reference_id or d.title for d in knowledge.uncited_docs}
        used_titles = {_normalize_title(d.title) for d in knowledge.distinct_docs} - {
            _normalize_title(d.title) for d in knowledge.uncited_docs if (d.reference_id or d.title) in used_doc_keys
        }

    for turn in convo.turns:
        if turn.user_message is None:
            continue
        final = turn.final_bot_text
        if not final.strip() or _looks_intermediate(final):
            continue

        searches = [tc for tc in turn.tool_calls if tc.is_knowledge_search]
        got_docs = any(tc.retrieved_docs for tc in searches)
        zero_results = searches and all(tc.zero_result or not tc.retrieved_docs for tc in searches)
        substantive = len(final.strip()) >= _SUBSTANTIVE_MIN
        label = _truncate(turn.user_message.text, 80)

        if _HONEST_GAP_RE.search(final):
            honest.append(label)
            grounded += 1
            continue

        if zero_results and substantive:
            hallucination.append(f"{label} — answered despite a zero-result search")
            ungrounded += 1
            continue

        if got_docs:
            grounded += 1
        elif not searches and substantive and not _CITATION_RE.search(final):
            ungrounded += 1
            notes.append(f"{label} — substantive answer with no knowledge search or citation")
        else:
            grounded += 1

    if used_titles:
        notes.append(f"{len([t for t in used_titles if t])} retrieved document(s) were referenced in answers")

    return GroundednessAssessment(
        grounded_answers=grounded,
        ungrounded_answers=ungrounded,
        hallucination_risk=hallucination,
        honest_grounding=honest,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Instruction compliance
# ---------------------------------------------------------------------------


def check_instructions(profile: AgentProfile | None, convo: Conversation | None) -> InstructionCompliance:
    checks: list[InstructionCheck] = []
    if profile is None or not profile.instructions.strip():
        return InstructionCompliance(checks=checks)

    text = profile.instructions
    low = text.lower()

    def add(instruction: str, check: str, status: str, evidence: str) -> None:
        checks.append(InstructionCheck(instruction=instruction, check=check, status=status, evidence=evidence))

    # 1. Intermediate chain-of-thought / "explain what you're doing".
    if any(k in low for k in ("chain of thought", "intermediate", "explain what you", "step by step", "thinking")):
        if convo is None:
            add(
                "Emit intermediate / chain-of-thought messages",
                "thoughts or streamed messages present",
                "unknown",
                "No transcript provided",
            )
        else:
            thoughts = len(convo.thoughts)
            multi = sum(1 for t in convo.turns if t.user_message is not None and len(t.bot_messages) > 1)
            intermediate = sum(
                1
                for t in convo.turns
                for msg in t.bot_messages
                if msg is not t.bot_messages[-1] and _looks_intermediate(msg.text)
            )
            ok = thoughts > 0 or multi > 0 or intermediate > 0
            add(
                "Emit intermediate / chain-of-thought messages",
                "reasoning blocks or multi-message streaming present",
                "pass" if ok else "fail",
                f"{thoughts} reasoning block(s); {multi} turn(s) streamed multiple messages; "
                f"{intermediate} intermediate 'thinking' message(s)",
            )

    # 2. Citations / cite your sources.
    if any(k in low for k in ("cite", "citation", "reference the source", "provide sources")):
        if convo is None:
            add("Cite sources in answers", "citation markers present", "unknown", "No transcript provided")
        else:
            markers = sum(len(_CITATION_RE.findall(m.text)) for m in convo.bot_messages)
            add(
                "Cite sources in answers",
                "citation markers present in answers",
                "pass" if markers else "fail",
                f"{markers} citation marker(s) found",
            )

    # 3. Conciseness.
    if any(k in low for k in ("concise", "brief", "short answer", "keep it short")):
        if convo is None:
            add("Keep answers concise", "average answer length", "unknown", "No transcript provided")
        else:
            finals = [t.final_bot_text for t in convo.turns if t.user_message is not None and t.final_bot_text.strip()]
            avg = sum(len(f) for f in finals) / len(finals) if finals else 0
            add(
                "Keep answers concise",
                "average final-answer length under 600 chars",
                "pass" if avg and avg < 600 else "fail",
                f"avg final answer ≈ {int(avg)} chars",
            )

    # 4. Language requirement (cannot verify reliably).
    lang = re.search(r"respond(?:ing)?\s+(?:only\s+)?in\s+([A-Z][a-z]+)", text)
    if lang or "in dutch" in low or "in english" in low:
        add(
            "Respond in the required language",
            "language of answers",
            "unknown",
            "Language compliance is not heuristically verified",
        )

    # 5. Scope restrictions (cannot verify reliably) — surface for human review.
    if any(k in low for k in ("only answer", "do not answer", "never", "must not", "out of scope", "refuse")):
        add(
            "Honour scope restrictions",
            "stay within allowed scope",
            "unknown",
            "Scope adherence needs human/LLM review",
        )

    if not checks:
        add(
            "General instruction adherence",
            "instructions present but no auto-checkable directives detected",
            "unknown",
            _truncate(text, 200),
        )

    return InstructionCompliance(checks=checks)


# ---------------------------------------------------------------------------
# Cross-reference (YAML + transcript)
# ---------------------------------------------------------------------------


def cross_reference(
    profile: AgentProfile | None,
    convo: Conversation | None,
    knowledge: KnowledgeAnalysis | None,
) -> CrossReference:
    xref = CrossReference()
    if profile is not None:
        xref.model_in_use = profile.model_label or profile.model_series
        xref.defined_knowledge_sources = [ks.display_name for ks in profile.knowledge_sources]

        retrieved_urls = [d.url for d in (knowledge.distinct_docs if knowledge else []) if d.url]
        contributing: list[str] = []
        for ks in profile.knowledge_sources:
            site = (ks.source_site or "").lower()
            hit = bool(site) and any(site in (u or "").lower() for u in retrieved_urls)
            if hit:
                contributing.append(ks.display_name)
        xref.contributing_knowledge_sources = contributing
        xref.unused_knowledge_sources = [s for s in xref.defined_knowledge_sources if s not in contributing]

    if convo is not None:
        defined_tool_names = set()
        if profile is not None:
            for tc in profile.tool_components:
                defined_tool_names.add(tc.display_name.lower())
                defined_tool_names.add(tc.kind.lower())
        used = {(tc.name or "").lower() for tc in convo.tool_calls if tc.name}
        xref.tools_used_not_defined = sorted(
            n for n in used if n and n not in _BUILTIN_TOOLS and n not in defined_tool_names
        )

    return xref


# ---------------------------------------------------------------------------
# Knowledge source effectiveness (#3)
# ---------------------------------------------------------------------------


def _site_match(doc_url: str | None, source_site: str | None) -> bool:
    """True when a retrieved doc URL traces to a configured knowledge source."""
    if not doc_url or not source_site:
        return False
    dr, sr = _site_root(doc_url), _site_root(source_site)
    if dr and sr:
        return dr.lower() == sr.lower()
    return source_site.lower() in doc_url.lower()


def analyze_knowledge_effectiveness(
    profile: AgentProfile | None,
    knowledge: KnowledgeAnalysis | None,
) -> KnowledgeEffectiveness | None:
    """Per-source hit/contribution, reconstructed by mapping retrieved doc URLs
    to configured knowledge sources (or to observed site-roots when no YAML)."""
    if knowledge is None:
        return None

    eff = KnowledgeEffectiveness(
        total_searches=len(knowledge.queries),
        zero_result_searches=len(knowledge.zero_result_queries),
        distinct_docs=len(knowledge.distinct_docs),
    )
    if eff.total_searches:
        eff.avg_docs_per_search = round(
            sum(len(q.docs) for q in knowledge.queries) / eff.total_searches, 2
        )

    def _key(doc: RetrievedDoc) -> str:
        return doc.reference_id or doc.title or doc.url or ""

    uncited_keys = {_key(d) for d in knowledge.uncited_docs}
    cited = lambda d: _key(d) not in uncited_keys  # noqa: E731

    configured = list(profile.knowledge_sources) if profile else []
    attributed: set[str] = set()
    stats: list[SourceEffectiveness] = []

    for ks in configured:
        retrieved = [d for d in knowledge.distinct_docs if _site_match(d.url, ks.source_site)]
        attributed.update(_key(d) for d in retrieved)
        n_ret = len(retrieved)
        n_cit = sum(1 for d in retrieved if cited(d))
        stats.append(
            SourceEffectiveness(
                display_name=ks.display_name,
                source_kind=ks.source_kind,
                source_site=ks.source_site,
                configured=True,
                docs_retrieved=n_ret,
                docs_cited=n_cit,
                contribution_rate=round(n_cit / n_ret, 2) if n_ret else 0.0,
                zero_contribution=n_ret > 0 and n_cit == 0,
                never_retrieved=n_ret == 0,
            )
        )

    leftover = [d for d in knowledge.distinct_docs if _key(d) not in attributed]
    eff.unattributed_docs = len(leftover)

    # Transcript-only (no YAML): synthesise sources from observed site-roots so
    # the panel still shows per-source effectiveness.
    if not configured and leftover:
        groups: dict[str, list[RetrievedDoc]] = {}
        for d in leftover:
            groups.setdefault(_site_root(d.url) or "(unknown source)", []).append(d)
        for root, docs in sorted(groups.items()):
            n_ret = len(docs)
            n_cit = sum(1 for d in docs if cited(d))
            stats.append(
                SourceEffectiveness(
                    display_name=root,
                    source_site=root,
                    configured=False,
                    docs_retrieved=n_ret,
                    docs_cited=n_cit,
                    contribution_rate=round(n_cit / n_ret, 2) if n_ret else 0.0,
                    zero_contribution=n_ret > 0 and n_cit == 0,
                )
            )

    eff.sources = stats
    return eff


# ---------------------------------------------------------------------------
# Citation verification (#4)
# ---------------------------------------------------------------------------


def _title_in_context(title: str | None, context_norm: str) -> bool:
    """Loose match: is this doc's (normalised) title referenced near a marker?"""
    nt = _normalize_title(title)
    if not nt:
        return False
    if len(nt) >= 6 and nt in context_norm:
        return True
    words = [w for w in nt.split() if len(w) > 3]
    return len(words) >= 2 and sum(1 for w in words if w in context_norm) >= 2


def verify_citations(convo: Conversation | None, knowledge: KnowledgeAnalysis | None) -> CitationAudit | None:
    """Flat audit of every numeric ``[n]`` citation: resolved (the nearby title
    maps to a retrieved doc), dangling (matches nothing retrieved), plus retrieved
    docs that were never cited."""
    if convo is None:
        return None

    audit = CitationAudit()
    distinct = knowledge.distinct_docs if knowledge else []
    uncited = knowledge.uncited_docs if knowledge else []

    def _key(d: RetrievedDoc) -> str:
        return d.reference_id or d.title or d.url or ""

    # Conversation-level marker memory so a [n] reused in a later turn resolves to
    # the doc retrieved earlier (C1) instead of being mislabelled dangling.
    prior_markers: dict[str, tuple[RetrievedDoc, str | None]] = {}

    for turn in convo.turns:
        # Provenance + candidate docs for this turn's searches (fall back to all).
        turn_docs: list[tuple[RetrievedDoc, str | None]] = []
        turn_keys: set[str] = set()
        for tc in turn.tool_calls:
            if tc.is_knowledge_search:
                for d in tc.retrieved_docs:
                    turn_docs.append((d, tc.query))
                    turn_keys.add(_key(d))
        candidates = turn_docs or [(d, None) for d in distinct]

        resolved_markers: dict[str, tuple[RetrievedDoc, str | None]] = {}
        for msg in turn.bot_messages:
            for m in _CITATION_RE.finditer(msg.text):
                marker = m.group(0)
                ctx = _normalize_title(msg.text[max(0, m.start() - 90) : m.start()])
                hit = next(((d, q) for d, q in candidates if _title_in_context(d.title, ctx)), None)
                # A repeated marker reuses the doc it resolved to earlier — same turn first, then prior turns.
                if hit is None and marker in resolved_markers:
                    hit = resolved_markers[marker]
                if hit is None and marker in prior_markers:
                    hit = prior_markers[marker]
                if hit:
                    doc, query = hit
                    resolved_markers[marker] = hit
                    prior_markers[marker] = hit
                    cross = _key(doc) not in turn_keys
                    audit.rows.append(
                        CitationAuditRow(
                            marker=marker,
                            reference_id=doc.reference_id,
                            status="resolved",
                            doc_title=doc.title,
                            doc_url=doc.url,
                            source=_site_root(doc.url),
                            turn_index=turn.index,
                            provenance=query or ("retrieved in an earlier turn" if cross else None),
                            cross_turn=cross,
                        )
                    )
                    audit.resolved += 1
                else:
                    audit.rows.append(
                        CitationAuditRow(
                            marker=marker,
                            status="dangling",
                            turn_index=turn.index,
                        )
                    )
                    audit.dangling += 1

    for doc in uncited:
        audit.rows.append(
            CitationAuditRow(
                marker="—",
                reference_id=doc.reference_id,
                status="uncited_retrieval",
                doc_title=doc.title,
                doc_url=doc.url,
                source=_site_root(doc.url),
            )
        )
        audit.uncited_retrievals += 1

    return audit


# ---------------------------------------------------------------------------
# Credit / cost estimation (#9)
# ---------------------------------------------------------------------------


def estimate_credits(profile: AgentProfile | None, convo: Conversation | None) -> CreditEstimate | None:
    """Heuristic MCS Copilot Credit estimate from runtime events.

    Feature meters (classic/generative/agent-action) are counted per runtime event.
    Modern agents on a **reasoning-capable** model additionally bill the *premium
    AI-tools* token meter (10 CC/1K tokens) on top of every answered turn, and
    document/content analysis skills bill *content-processing* (8 CC/page). All
    figures are estimates; token counts are heuristic (≈ chars/4)."""
    if convo is None:
        return None

    rates = credit_rates()
    items: list[CreditLineItem] = []
    reasoning = bool(
        profile
        and profile.model_series
        and any(s in profile.model_series.lower() for s in reasoning_model_series())
    )
    total_tokens = 0

    for turn in convo.turns:
        searches = [tc for tc in turn.tool_calls if tc.is_knowledge_search]
        actions = [tc for tc in turn.tool_calls if classify_tool_kind(tc) in {"action", "skill"}]

        for s in searches:
            items.append(
                CreditLineItem(
                    label=f"Turn {turn.index}: knowledge search",
                    kind="generative_answer",
                    credits=rates["generative_answer"],
                    detail=_truncate(s.query or "(no query)", 80),
                    turn_index=turn.index,
                )
            )
        for a in actions:
            items.append(
                CreditLineItem(
                    label=f"Turn {turn.index}: {a.display_name or a.name or 'action'}",
                    kind="agent_action",
                    credits=rates["agent_action"],
                    detail=classify_tool_kind(a),
                    turn_index=turn.index,
                )
            )

        final = turn.final_bot_text
        substantive = (
            turn.user_message is not None
            and len(final.strip()) >= _SUBSTANTIVE_MIN
            and not _looks_intermediate(final)
        )
        if substantive and not searches:
            items.append(
                CreditLineItem(
                    label=f"Turn {turn.index}: generated answer",
                    kind="generative_answer",
                    credits=rates["generative_answer"],
                    detail="Model-generated response (no knowledge search this turn).",
                    turn_index=turn.index,
                )
            )

        # E1 — premium reasoning-token surcharge on every answered turn.
        if reasoning and turn.user_message is not None:
            turn_tokens = estimate_tokens(final) + sum(estimate_tokens(t.text) for t in turn.thoughts)
            if turn_tokens:
                total_tokens += turn_tokens
                prem = round(turn_tokens / 1000 * rates["premium_per_1k"], 2)
                if prem:
                    items.append(
                        CreditLineItem(
                            label=f"Turn {turn.index}: reasoning tokens (premium)",
                            kind="premium_reasoning",
                            credits=prem,
                            detail=f"≈{turn_tokens} tokens × {rates['premium_per_1k']} CC/1K (reasoning model)",
                            turn_index=turn.index,
                            tokens=turn_tokens,
                        )
                    )

        # E2 — content-processing for document/image-analysis skills.
        for tc in turn.tool_calls:
            disp = (tc.display_name or "").lower()
            is_skill = "skill" in (tc.name or "").lower() or disp.startswith("loaded skill")
            if is_skill and any(
                k in disp for k in ("docx", "document", "pdf", "spreadsheet", "xlsx", "image", "analyz")
            ):
                pages = 1  # heuristic floor: ≥1 page per document-analysis skill
                items.append(
                    CreditLineItem(
                        label=f"Turn {turn.index}: document content processing",
                        kind="content_processing",
                        credits=round(rates["content_processing_page"] * pages, 2),
                        detail=f"{_truncate(disp or 'document skill', 60)} — ≈{pages} page × "
                        f"{rates['content_processing_page']} CC",
                        turn_index=turn.index,
                    )
                )

    by_kind: dict[str, float] = {}
    for it in items:
        by_kind[it.kind] = round(by_kind.get(it.kind, 0.0) + it.credits, 2)
    total = round(sum(it.credits for it in items), 2)

    notes = [
        "Heuristic estimate only — real Copilot Credit consumption depends on tenant "
        "configuration, message size and Microsoft's current billing model.",
        f"Feature rates: classic answer {rates['classic_answer']}, generative answer "
        f"{rates['generative_answer']}, agent action {rates['agent_action']} credits.",
        f"Billing rates source: {CREDIT_SOURCE_URL}",
        f"Interactive estimator: {CREDIT_ESTIMATOR_URL}",
    ]
    assumptions: list[str] = []
    if reasoning:
        notes.insert(
            1,
            f"Reasoning model detected ({profile.model_label if profile else '?'}) → premium AI-tools "
            f"token meter applies at {rates['premium_per_1k']} CC/1K tokens on top of feature charges "
            f"(≈{total_tokens} tokens estimated across the conversation).",
        )
        assumptions.append(
            "Premium token cost uses a heuristic token estimate (≈ chars/4) over answers + reasoning; "
            "actual billed tokens will differ."
        )
    if by_kind.get("content_processing"):
        assumptions.append(
            "Content-processing billed at 1 page per document-analysis skill load (a floor); "
            "multi-page documents cost proportionally more."
        )
    if profile is not None and profile.knowledge_sources:
        assumptions.append(
            "SharePoint/Graph-grounded answers are billed here as generative answers (2 CC); if the "
            f"tenant uses tenant-graph grounding they bill at {rates['tenant_graph']} CC each instead."
        )
    if profile is not None and profile.model_label:
        notes.insert(0, f"Agent model: {profile.model_label}.")

    return CreditEstimate(
        line_items=items,
        total_credits=total,
        by_kind=by_kind,
        notes=notes,
        reasoning_model=reasoning,
        total_tokens=total_tokens,
        assumptions=assumptions,
    )


# ---------------------------------------------------------------------------
# #10 Failed-tool & recovery deep-dive
# ---------------------------------------------------------------------------


def analyze_tool_failures(convo: Conversation) -> ToolFailureAnalysis:
    """Detect tool failures (including ones hidden behind a 'completed' status) and
    classify how the agent recovered within the same turn."""
    failures: list[ToolFailure] = []
    for turn in convo.turns:
        calls = turn.tool_calls
        for i, tc in enumerate(calls):
            if not tool_failed(tc):
                continue
            later = calls[i + 1 :]
            later_ok = [c for c in later if not tool_failed(c)]
            same_later = [c for c in later if (c.name or "") == (tc.name or "")]
            diff_ok = [c for c in later_ok if (c.name or "") != (tc.name or "")]

            recovery, next_action = "gave-up", None
            if diff_ok:
                recovery, next_action = "recovered-other-tool", diff_ok[-1].name
            elif same_later:
                recovery, next_action = "retried-same", tc.name
            elif turn.final_bot_text.strip() and not _looks_intermediate(turn.final_bot_text):
                recovery = "unhandled-but-answered"

            failures.append(
                ToolFailure(
                    turn_index=turn.index,
                    name=tc.name or "(unnamed)",
                    params_summary=_params_summary(tc),
                    error_text=_extract_error_text(tc),
                    embedded=_is_embedded_failure(tc),
                    recovery=recovery,
                    next_action=next_action,
                )
            )

    recovered = sum(1 for f in failures if f.recovery in ("retried-same", "recovered-other-tool"))
    return ToolFailureAnalysis(
        failures=failures,
        total_failures=len(failures),
        embedded_failures=sum(1 for f in failures if f.embedded),
        recovered=recovered,
        gave_up=sum(1 for f in failures if f.recovery == "gave-up"),
    )


# ---------------------------------------------------------------------------
# #6 Tool-call redundancy / efficiency
# ---------------------------------------------------------------------------


def analyze_tool_efficiency(convo: Conversation) -> ToolEfficiency:
    groups: dict[tuple[str, str], list[int]] = {}
    rep: dict[tuple[str, str], ToolCall] = {}
    order: list[tuple[str, str]] = []
    total = 0
    for turn in convo.turns:
        for tc in turn.tool_calls:
            total += 1
            key = (tc.name or "(unnamed)", _norm_params(tc))
            if key not in groups:
                groups[key], rep[key] = [], tc
                order.append(key)
            groups[key].append(turn.index)

    dups = [
        DuplicateGroup(
            name=key[0],
            params_summary=_params_summary(rep[key]),
            count=len(groups[key]),
            turns=sorted(set(groups[key])),
        )
        for key in order
        if len(groups[key]) > 1
    ]
    dups.sort(key=lambda d: -d.count)

    answers = sum(1 for t in convo.turns if t.user_message is not None and t.final_bot_text.strip())
    return ToolEfficiency(
        total_calls=total,
        unique_calls=len(groups),
        redundant_calls=sum(len(v) - 1 for v in groups.values() if len(v) > 1),
        calls_per_answer=round(total / answers, 2) if answers else 0.0,
        duplicate_groups=dups,
    )


# ---------------------------------------------------------------------------
# #5 Repetition / loop detection
# ---------------------------------------------------------------------------


def detect_repetition(convo: Conversation) -> RepetitionAnalysis:
    signals: list[RepetitionSignal] = []
    thresh = _THRESH["repetition_jaccard"]
    min_tokens = int(_THRESH["min_repeat_tokens"])
    substantive_min = _THRESH["substantive_min_chars"]

    def _pairs(items: list[tuple[int, str, set[str]]], kind: str) -> None:
        seen: set[int] = set()
        for a in range(len(items)):
            ta, txt_a, tok_a = items[a]
            if len(tok_a) < min_tokens or ta in seen:
                continue
            for b in range(a + 1, len(items)):
                tb, _, tok_b = items[b]
                sim = _jaccard(tok_a, tok_b)
                if sim >= thresh:
                    signals.append(
                        RepetitionSignal(kind=kind, turns=[ta, tb], similarity=round(sim, 2), excerpt=_truncate(txt_a, 140))
                    )
                    seen.add(tb)

    answers = [
        (t.index, t.final_bot_text.strip(), _tokens(t.final_bot_text))
        for t in convo.turns
        if len(t.final_bot_text.strip()) >= substantive_min
    ]
    _pairs(answers, "agent-answer")

    questions = [
        (t.index, t.user_message.text.strip(), _tokens(t.user_message.text))
        for t in convo.turns
        if t.user_message is not None and t.user_message.text.strip()
    ]
    _pairs(questions, "user-question")

    # A tight tool loop: the same call (name + identical params) repeated 3+ times.
    loops: dict[tuple[str, str], list[int]] = {}
    for t in convo.turns:
        for tc in t.tool_calls:
            loops.setdefault((tc.name or "(unnamed)", _norm_params(tc)), []).append(t.index)
    for (name, _), turns in loops.items():
        if len(turns) >= 3:
            signals.append(
                RepetitionSignal(
                    kind="agent-tool",
                    turns=sorted(set(turns)),
                    similarity=1.0,
                    excerpt=f"{name} called {len(turns)}× with identical parameters",
                )
            )

    return RepetitionAnalysis(signals=signals)


# ---------------------------------------------------------------------------
# #2 Per-answer groundedness / hallucination risk
# ---------------------------------------------------------------------------


def assess_answer_groundedness(
    convo: Conversation, knowledge: KnowledgeAnalysis | None = None
) -> AnswerGroundednessAnalysis:
    out: list[AnswerGroundedness] = []
    for turn in convo.turns:
        if turn.user_message is None:
            continue
        final = turn.final_bot_text.strip()
        if not final or _looks_intermediate(final):
            continue
        factual = [s for s in _SENTENCE_SPLIT_RE.split(final) if _factual_claim(s)]
        if not factual:
            continue

        cited = len(_CITATION_RE.findall(final)) + len(_REFID_RE.findall(final))
        searches = [tc for tc in turn.tool_calls if tc.is_knowledge_search]
        had_retrieval = any(tc.retrieved_docs for tc in searches)
        honest = bool(_HONEST_GAP_RE.search(final))

        if honest or cited >= 1:
            risk = "low"
        elif searches and not had_retrieval:
            risk = "high"  # searched, got nothing, still made claims
        elif had_retrieval:
            risk = "medium"  # had docs but cited none
        else:
            risk = "medium"  # claims with no search and no citation
        if not honest and cited == 0 and had_retrieval and len(factual) >= 2:
            risk = "high"

        out.append(
            AnswerGroundedness(
                turn_index=turn.index,
                factual_claims=len(factual),
                cited_claims=cited,
                had_retrieval=had_retrieval,
                risk=risk,
                excerpt=_truncate(factual[0], 140),
            )
        )

    return AnswerGroundednessAnalysis(
        answers=out,
        high_risk=sum(1 for a in out if a.risk == "high"),
        medium_risk=sum(1 for a in out if a.risk == "medium"),
        low_risk=sum(1 for a in out if a.risk == "low"),
    )


# ---------------------------------------------------------------------------
# #11 Citation quote-traceability
# ---------------------------------------------------------------------------


def verify_quote_faithfulness(
    convo: Conversation, knowledge: KnowledgeAnalysis | None = None
) -> QuoteFaithfulness:
    """Check direct quotes in bot answers against the transcript's tool outputs.

    Modern RAG reads full documents in a sandbox, so retrieved-doc text rarely
    reaches the transcript. Verdicts are honest about that limit:
    verified-in-tool-output / attributed-source-in-sandbox / dangling-attribution /
    unattributed-quote.
    """
    tool_blob = _normalize_text(" ".join(tc.result or "" for tc in convo.tool_calls))
    any_retrieved = bool(knowledge and any(q.docs for q in knowledge.queries))

    quotes: list[QuoteCheck] = []
    for turn in convo.turns:
        turn_docs = [d for tc in turn.tool_calls if tc.is_knowledge_search for d in tc.retrieved_docs]
        turn_title = turn_docs[0].title if turn_docs else None
        for m in turn.bot_messages:
            raw_spans = [mm.group(1).strip() for mm in _BLOCKQUOTE_RE.finditer(m.text)]
            raw_spans += [mm.group(1).strip() for mm in _DQUOTE_RE.finditer(m.text)]
            cited = bool(_CITATION_RE.search(m.text) or _REFID_RE.search(m.text))

            # Dedupe overlapping spans (a blockquote that wraps a quoted string, etc.).
            kept: list[tuple[str, str]] = []  # (normalized, display)
            for span in raw_spans:
                if len(span) < 15:
                    continue
                norm = _normalize_text(span)
                if not norm:
                    continue
                replaced = skip = False
                for k, (n, _) in enumerate(kept):
                    if norm in n:
                        skip = True
                        break
                    if n in norm:
                        kept[k] = (norm, span)
                        skip = replaced = True
                        break
                if not skip and not replaced:
                    kept.append((norm, span))

            for norm, span in kept:
                display = _truncate(span.lstrip("*_> \"'").strip(), 160)
                if len(norm) > 12 and norm in tool_blob:
                    verdict, title = "verified-in-tool-output", turn_title
                elif cited and (turn_docs or any_retrieved):
                    verdict, title = "attributed-source-in-sandbox", turn_title
                elif cited:
                    verdict, title = "dangling-attribution", None
                else:
                    verdict, title = "unattributed-quote", None
                quotes.append(
                    QuoteCheck(
                        turn_index=turn.index,
                        excerpt=display,
                        ref_id=(turn_docs[0].reference_id if turn_docs and verdict == "attributed-source-in-sandbox" else None),
                        source_title=title,
                        verdict=verdict,
                    )
                )

    return QuoteFaithfulness(
        quotes=quotes,
        verified=sum(1 for q in quotes if q.verdict == "verified-in-tool-output"),
        attributed=sum(1 for q in quotes if q.verdict == "attributed-source-in-sandbox"),
        dangling=sum(1 for q in quotes if q.verdict == "dangling-attribution"),
        unattributed=sum(1 for q in quotes if q.verdict == "unattributed-quote"),
    )


# ---------------------------------------------------------------------------
# #12 Knowledge coverage-gap report
# ---------------------------------------------------------------------------


def analyze_coverage_gaps(
    convo: Conversation, knowledge: KnowledgeAnalysis | None = None
) -> CoverageGapAnalysis:
    gaps: list[CoverageGap] = []
    for turn in convo.turns:
        if turn.user_message is None:
            continue
        question = _truncate(turn.user_message.text, 120)
        searches = [tc for tc in turn.tool_calls if tc.is_knowledge_search]
        final = turn.final_bot_text.strip()

        for tc in searches:
            if tc.zero_result:
                gaps.append(
                    CoverageGap(
                        turn_index=turn.index, user_question=question, reason="zero-result-search", query=tc.query or ""
                    )
                )

        if final and _HONEST_GAP_RE.search(final):
            gaps.append(
                CoverageGap(
                    turn_index=turn.index,
                    user_question=question,
                    reason="acknowledged-gap",
                    query=searches[0].query if searches else "",
                )
            )
        elif (
            final
            and not turn.tool_calls
            and len(final) >= _THRESH["substantive_min_chars"]
            and not _CITATION_RE.search(final)
            and not _REFID_RE.search(final)
        ):
            gaps.append(CoverageGap(turn_index=turn.index, user_question=question, reason="uncited-answer", query=""))

    return CoverageGapAnalysis(gaps=gaps)


# ---------------------------------------------------------------------------
# #16 Turn-economy
# ---------------------------------------------------------------------------


def analyze_turn_economy(convo: Conversation) -> TurnEconomy:
    user_turns = [t for t in convo.turns if t.user_message is not None]
    tool_calls = len(convo.tool_calls)
    answers = [t for t in user_turns if t.final_bot_text.strip()]

    searches_to_first = 0
    for t in convo.turns:
        searches_to_first += sum(1 for tc in t.tool_calls if tc.is_knowledge_search)
        if t.user_message is not None and t.final_bot_text.strip():
            break

    return TurnEconomy(
        turns=len(convo.turns),
        user_turns=len(user_turns),
        tool_calls=tool_calls,
        calls_per_answer=round(tool_calls / len(answers), 2) if answers else 0.0,
        searches_to_first_answer=searches_to_first,
        avg_bot_msgs_per_turn=round(sum(len(t.bot_messages) for t in convo.turns) / len(convo.turns), 2)
        if convo.turns
        else 0.0,
    )


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


_CI_FRICTION_PERM_RE = re.compile(
    r"(permission denied|operation not permitted|read-?only|cannot write|access denied|"
    r"not writable|not permitted|eacces)",
    re.IGNORECASE,
)
_CI_FRICTION_ALT_RE = re.compile(
    r"(instead|alternativ|another way|different approach|let me try|fall ?back|work ?around|"
    r"temp(?:orary)? dir|/tmp|sudo)",
    re.IGNORECASE,
)


def analyze_code_interpreter(convo: Conversation) -> CodeInterpreterAnalysis:
    """D1/D2/D3 — surface sandbox / code-interpreter activity that lives only in
    the reasoning `thoughts` and tool-result preambles of modern agents."""
    signals: list[SandboxSignal] = []
    friction: list[SandboxFriction] = []
    skills: list[SkillUse] = []

    for turn in convo.turns:
        signals.extend(code_interpreter_signals(turn))

        for th in turn.thoughts:
            txt = th.text or ""
            if _CI_FRICTION_PERM_RE.search(txt):
                friction.append(
                    SandboxFriction(turn_index=turn.index, kind="permission-denied", excerpt=_truncate(txt, 200))
                )
            elif _RETRY_RE.search(txt) or _CI_FRICTION_ALT_RE.search(txt):
                friction.append(
                    SandboxFriction(turn_index=turn.index, kind="alternative-approach", excerpt=_truncate(txt, 200))
                )

        for tc in turn.tool_calls:
            disp = (tc.display_name or "").strip()
            low = disp.lower()
            if "skill" in (tc.name or "").lower() or low.startswith("loaded skill"):
                name = disp.split(":", 1)[-1].strip() if ":" in disp else (disp or tc.name or "skill")
                doc = any(k in low for k in ("docx", "document", "pdf", "spreadsheet", "xlsx", "image", "analyz"))
                skills.append(
                    SkillUse(
                        turn_index=turn.index,
                        name=name,
                        category="document-processing" if doc else "other",
                        note="Document/content analysis skill" if doc else "",
                    )
                )

    # A friction episode is "recovered" if its turn still produced a final answer.
    final_by_turn = {t.index: bool(t.final_bot_text.strip()) for t in convo.turns}
    for f in friction:
        f.recovered = final_by_turn.get(f.turn_index, False)

    skill_gaps = detect_skill_gaps(convo)

    authoring_turns = sorted({s.turn_index for s in signals if s.category == "authoring"})
    analysis_turns = sorted(
        {s.turn_index for s in signals if s.category in ("read-document", "preprocess", "inspect-fs")}
    )

    return CodeInterpreterAnalysis(
        signals=signals,
        friction=friction,
        skills=skills,
        skill_gaps=skill_gaps,
        turns_with_code=len({s.turn_index for s in signals}),
        distinct_tools=sorted({s.tool for s in signals if s.tool}),
        friction_count=len(friction),
        document_processing_skills=sum(1 for s in skills if s.category == "document-processing"),
        authoring_turns=authoring_turns,
        analysis_turns=analysis_turns,
        used=bool(signals or skills),
    )


_SKILL_GAP_RE = re.compile(
    r"(no skill (?:for|available|to|listed)|not (?:a|an)\s+[\"'`]?[\w-]+[\"'`]?\s+skill|"
    r"don'?t have (?:a|an|any)?\s*skill|isn'?t (?:a|any)\s*skill|"
    r"no\s+[\w-]+\s+skill\b|without a skill)",
    re.IGNORECASE,
)
_WANTED_NOT_A_RE = re.compile(r"not (?:a|an)\s+[\"'`]?([\w-]+)[\"'`]?\s+skill", re.IGNORECASE)
_WANTED_FOR_RE = re.compile(r"(?:no )?skill (?:for|to)\s+([\w -]{3,40})", re.IGNORECASE)
_WANTED_GENERIC_RE = re.compile(r"[\"'`]([\w-]+)[\"'`]\s+skill|([\w-]+)\s+skill", re.IGNORECASE)
_WANTED_STOP = {
    "relevant", "available", "suitable", "appropriate", "specific", "dedicated", "a", "an",
    "the", "this", "that", "any", "such", "existing", "built-in", "native", "good", "right",
}
_FALLBACK_RE = re.compile(r"(python|directly|manually|raw code|by hand|write the code|using code)", re.IGNORECASE)


def _extract_wanted_skill(frag: str) -> str:
    """Best-effort name of the skill the agent looked for, skipping filler words."""
    m = _WANTED_NOT_A_RE.search(frag)
    if m and m.group(1).lower() not in _WANTED_STOP:
        return m.group(1).strip()
    m = _WANTED_FOR_RE.search(frag)
    if m:
        cand = m.group(1).strip().rstrip(".,")
        if cand.split()[0].lower() not in _WANTED_STOP:
            return cand
    for m in _WANTED_GENERIC_RE.finditer(frag):
        cand = (m.group(1) or m.group(2) or "").strip()
        if cand and cand.lower() not in _WANTED_STOP:
            return cand
    return ""


def detect_skill_gaps(convo: Conversation) -> list[SkillGap]:
    """G3 — the agent looked for a skill, found none, and fell back to writing code.
    Evidence lives in `thoughts` and sometimes the final answer text."""
    gaps: list[SkillGap] = []
    for turn in convo.turns:
        fragments = [t.text for t in turn.thoughts if t.text]
        fragments += [m.text for m in turn.bot_messages if m.text.strip()]
        for frag in fragments:
            if not _SKILL_GAP_RE.search(frag):
                continue
            fb = _FALLBACK_RE.search(frag)
            fallback = "python" if fb and "python" in fb.group(0).lower() else (fb.group(0).lower() if fb else "")
            gaps.append(
                SkillGap(
                    turn_index=turn.index,
                    wanted=_extract_wanted_skill(frag),
                    fallback=fallback,
                    excerpt=_truncate(frag, 220),
                )
            )
            break  # one gap per turn is enough
    return gaps


def analyze_retrieval_depth(
    convo: Conversation, knowledge: KnowledgeAnalysis | None, code: CodeInterpreterAnalysis | None
) -> RetrievalDepth | None:
    """B1/B2/B3/B4 — folder taxonomy, cross-search overlap, over-retrieval ratio
    and retrieval-mode (snippet+sandbox) for the knowledge the agent pulled."""
    if knowledge is None or not knowledge.queries:
        return None

    idx = reference_index(convo)
    cited_keys = set(knowledge.cited_reference_ids)
    cited_titles = {_normalize_title(d.title) for d in knowledge.distinct_docs} - {
        _normalize_title(d.title) for d in knowledge.uncited_docs
    }

    # B2 — per-doc retrieval counts / overlap.
    doc_retrievals: list[DocRetrieval] = []
    for key, rec in idx.items():
        is_cited = key in cited_keys or (_normalize_title(rec["title"]) in cited_titles if rec["title"] else False)
        doc_retrievals.append(
            DocRetrieval(
                reference_id=key if re.match(r"turn\d+doc\d+", key, re.IGNORECASE) else None,
                title=rec["title"] or "(untitled)",
                retrieval_count=rec["count"],
                turns=sorted(rec["turns"]),
                cited=bool(is_cited),
            )
        )
    doc_retrievals.sort(key=lambda d: (-d.retrieval_count, d.title))

    total_retrieved = sum(d.retrieval_count for d in doc_retrievals)
    unique_docs = len(doc_retrievals)
    overlap_docs = sum(1 for d in doc_retrievals if d.retrieval_count > 1)
    cited_docs = sum(1 for d in doc_retrievals if d.cited)
    over_ratio = round(1 - (cited_docs / unique_docs), 2) if unique_docs else 0.0

    # B1 — SharePoint folder taxonomy.
    folder_map: dict[str, KnowledgeFolder] = {}
    for d in knowledge.distinct_docs:
        parsed = parse_sharepoint_path(d.url)
        if not parsed:
            continue
        path, area = parsed
        f = folder_map.setdefault(path, KnowledgeFolder(path=path, area=area))
        f.count += 1
        if d.title and d.title not in f.doc_titles:
            f.doc_titles.append(d.title)
    folders = sorted(folder_map.values(), key=lambda f: (-f.count, f.path))

    # B4 — retrieval mode: snippets that point to full docs in the sandbox.
    snippet_sandbox = any(
        d.snippet and _SANDBOX_PREAMBLE_RE.search(d.snippet) for q in knowledge.queries for d in q.docs
    )
    full_reads = code.turns_with_code if code else 0
    mode = "snippet+sandbox" if snippet_sandbox or full_reads else "inline"

    return RetrievalDepth(
        folders=folders,
        doc_retrievals=doc_retrievals,
        total_retrieved=total_retrieved,
        unique_docs=unique_docs,
        overlap_docs=overlap_docs,
        cited_docs=cited_docs,
        over_retrieval_ratio=over_ratio,
        retrieval_mode=mode,
        full_doc_reads=full_reads,
    )


_RECALL_RE = re.compile(
    r"(already retrieved|already (?:have|found|pulled)|based on the .{0,40}(?:policy|document|doc|earlier)|"
    r"from (?:the )?(?:earlier|previous|prior) (?:search|retrieval|turn)|as (?:retrieved|found) (?:earlier|above)|"
    r"no need to search again|without (?:a new )?search)",
    re.IGNORECASE,
)


def analyze_search_strategy(convo: Conversation, knowledge: KnowledgeAnalysis | None) -> SearchStrategy | None:
    """A1/A2 — detect answered-from-recall turns and per-search→citation precision."""
    if convo is None:
        return None

    cited_titles: set[str] = set()
    cited_keys = set(knowledge.cited_reference_ids) if knowledge else set()
    if knowledge:
        uncited = {_normalize_title(d.title) for d in knowledge.uncited_docs}
        cited_titles = {_normalize_title(d.title) for d in knowledge.distinct_docs} - uncited

    recall_turns: list[RecallTurn] = []
    searches: list[SearchPrecision] = []

    for turn in convo.turns:
        turn_searches = [tc for tc in turn.tool_calls if tc.is_knowledge_search]
        final = turn.final_bot_text

        # A1 — substantive answer, no search this turn, recall language present.
        if turn.user_message is not None and not turn_searches and len(final.strip()) >= _SUBSTANTIVE_MIN:
            blob = final + " " + " ".join(t.text for t in turn.thoughts)
            m = _RECALL_RE.search(blob)
            if m or _REFID_RE.search(final):
                recall_turns.append(
                    RecallTurn(turn_index=turn.index, excerpt=_truncate(m.group(0) if m else final, 160))
                )

        # A2 — per search, did any retrieved doc get cited?
        for s in turn_searches:
            cited_here = 0
            for d in s.retrieved_docs:
                key = d.reference_id or ""
                if (key and key in cited_keys) or (_normalize_title(d.title) in cited_titles):
                    cited_here += 1
            searches.append(
                SearchPrecision(
                    turn_index=turn.index,
                    query=s.query or "(no query)",
                    retrieved=len(s.retrieved_docs),
                    cited_from_search=cited_here,
                    productive=cited_here > 0,
                )
            )

    return SearchStrategy(
        recall_turns=recall_turns,
        searches=searches,
        productive_searches=sum(1 for s in searches if s.productive),
        unproductive_searches=sum(1 for s in searches if not s.productive),
    )


# --- G1 · Generated file artifacts ------------------------------------------

_ARTIFACT_PY_RE = re.compile(r"(python-pptx|python-docx|openpyxl|reportlab|matplotlib|\.save\(|Presentation\()", re.IGNORECASE)


def analyze_generated_artifacts(convo: Conversation) -> GeneratedArtifacts | None:
    """G1 — files the agent produced (e.g. a generated .pptx deck). These live in
    `fileAttachments` on bot messages and are otherwise invisible in the report."""
    items: list[GeneratedArtifact] = []
    for turn in convo.turns:
        # Did this turn show code-authoring intent (python-pptx etc.)?
        think = " ".join(t.text for t in turn.thoughts if t.text)
        skill_made = any("skill" in (tc.name or "").lower() for tc in turn.tool_calls if "creat" in (tc.display_name or "").lower() or "generat" in (tc.display_name or "").lower())
        py_made = bool(_ARTIFACT_PY_RE.search(think)) or "python" in think.lower()
        for m in turn.bot_messages:
            for a in m.file_attachments:
                if not a.name:
                    continue
                how = "skill" if skill_made else ("python" if py_made else "unknown")
                evidence = ""
                if how == "python":
                    mm = _ARTIFACT_PY_RE.search(think)
                    evidence = _truncate(think[max(0, mm.start() - 40):] if mm else think, 200)
                items.append(
                    GeneratedArtifact(
                        turn_index=turn.index,
                        name=a.name,
                        file_type=a.file_type or (a.name.rsplit(".", 1)[-1] if "." in a.name else ""),
                        content_type=a.content_type,
                        how_made=how,
                        evidence=evidence,
                    )
                )
    if not items:
        return None
    by_type: dict[str, int] = {}
    for it in items:
        by_type[it.file_type or "file"] = by_type.get(it.file_type or "file", 0) + 1
    return GeneratedArtifacts(items=items, count=len(items), by_type=by_type)


# --- G4 · Document grounding pipeline ---------------------------------------

_SNIPPET_STUB_RE = re.compile(
    r"(file downloaded to sandbox|open this file using|most appropriate skill or tool|"
    r"full documents? (have been )?saved|prefer the full files)",
    re.IGNORECASE,
)


def analyze_grounding_pipeline(
    convo: Conversation, knowledge: KnowledgeAnalysis | None, code: CodeInterpreterAnalysis | None
) -> GroundingPipeline | None:
    """G4 — reconstruct how answers were grounded: did the search return real text or
    just a sandbox-download stub, was the doc preprocessed/read, and is the cited span
    observable? Answers 'which part of the document was reviewed?' as far as the
    transcript allows (usually: only at document granularity)."""
    if knowledge is None or not knowledge.queries:
        return None

    # Snippet mode — are search "snippets" real content or download stubs?
    stub = content = 0
    for tc in convo.tool_calls:
        if not tc.is_knowledge_search:
            continue
        for d in tc.retrieved_docs:
            snip = (d.snippet or "").strip()
            if not snip or _SNIPPET_STUB_RE.search(snip):
                stub += 1
            else:
                content += 1
    if stub and content:
        snippet_mode = "mixed"
    elif stub:
        snippet_mode = "stub"
    elif content:
        snippet_mode = "content"
    else:
        snippet_mode = "unknown"

    sandbox_grounded = bool(code and (code.analysis_turns or code.distinct_tools))
    # If the agent works from full files in the sandbox and search returns stubs, the
    # cited passage is not observable from the transcript — grounding is document-level.
    span_visibility = "document-level" if (snippet_mode in ("stub", "mixed") or sandbox_grounded) else "span-level"

    # Per-doc chain. Tie preprocess/read to a doc by matching its title stem in thoughts.
    all_think = " ".join(t.text.lower() for t in convo.thoughts if t.text)
    cited_keys = set(knowledge.cited_reference_ids) if knowledge else set()
    cited_titles = {_normalize_title(d.title) for d in knowledge.distinct_docs} - {
        _normalize_title(d.title) for d in knowledge.uncited_docs
    }
    idx = reference_index(convo)
    docs: list[GroundingDoc] = []
    for key, rec in idx.items():
        title = rec.get("title") or ""
        stem = title.rsplit(".", 1)[0].lower() if title else ""
        mentioned = bool(stem) and stem in all_think
        is_cited = key in cited_keys or _normalize_title(title) in cited_titles
        docs.append(
            GroundingDoc(
                title=title,
                url=rec.get("url"),
                reference_id=key if key.startswith("turn") else None,
                searched=True,
                downloaded=snippet_mode in ("stub", "mixed"),
                preprocessed=mentioned and bool(code and code.analysis_turns),
                read_full=mentioned and bool(code and any(s.category == "read-document" for s in code.signals)),
                cited=is_cited,
            )
        )
    docs.sort(key=lambda d: (not d.cited, d.title))

    notes: list[str] = []
    if snippet_mode == "stub":
        notes.append(
            "Knowledge search returned download stubs, not content — the actual passage was read from the "
            "full file in the sandbox, so the transcript only proves grounding at document level."
        )
    elif snippet_mode == "mixed":
        notes.append("Some search results carried snippet text, others were sandbox-download stubs.")
    if span_visibility == "document-level":
        notes.append(
            "Citations resolve to a whole document (ReferenceId), not a specific span; which paragraph was "
            "used is only inferable from the agent's reasoning, never from citation metadata."
        )

    return GroundingPipeline(
        snippet_mode=snippet_mode,
        span_visibility=span_visibility,
        docs=docs,
        stub_results=stub,
        content_results=content,
        notes=notes,
    )


def collect_findings(report: AnalysisReport) -> list[Finding]:
    out: list[Finding] = []

    if report.agent is None:
        out.append(
            Finding(
                severity="info",
                category="Agent",
                title="No agent YAML provided",
                detail="Profile, instruction-compliance and knowledge cross-reference were skipped.",
            )
        )
    if report.overview is None:
        out.append(
            Finding(
                severity="info",
                category="Agent",
                title="No transcript provided",
                detail="Conversation, tool, knowledge and quality analysis were skipped.",
            )
        )

    if report.tools:
        if report.tools.failures:
            out.append(
                Finding(
                    severity="warning",
                    category="Tools",
                    title=f"{len(report.tools.failures)} tool call(s) failed",
                    detail="; ".join(report.tools.failures[:5]),
                )
            )
        if report.tools.retry_signals:
            out.append(
                Finding(
                    severity="info",
                    category="Tools",
                    title=f"Agent retried or changed approach {len(report.tools.retry_signals)} time(s)",
                    detail=report.tools.retry_signals[0],
                )
            )

    if report.knowledge:
        if report.knowledge.zero_result_queries:
            out.append(
                Finding(
                    severity="warning",
                    category="Knowledge",
                    title=f"{len(report.knowledge.zero_result_queries)} knowledge search(es) returned no results",
                    detail="; ".join(report.knowledge.zero_result_queries[:5]),
                )
            )
        if report.knowledge.uncited_docs:
            titles = ", ".join(d.title or d.reference_id or "?" for d in report.knowledge.uncited_docs[:5])
            out.append(
                Finding(
                    severity="info",
                    category="Knowledge",
                    title=f"{len(report.knowledge.uncited_docs)} retrieved document(s) never used in an answer",
                    detail=titles,
                )
            )

    if report.citations and report.citations.uncited_answer_count:
        out.append(
            Finding(
                severity="warning",
                category="Citations",
                title=f"{report.citations.uncited_answer_count} substantive answer(s) without a citation or search",
                detail="Answers that make claims without grounding in a knowledge search.",
            )
        )

    if report.groundedness:
        for risk in report.groundedness.hallucination_risk:
            out.append(Finding(severity="critical", category="Quality", title="Hallucination risk", detail=risk))
        if report.groundedness.ungrounded_answers:
            out.append(
                Finding(
                    severity="warning",
                    category="Quality",
                    title=f"{report.groundedness.ungrounded_answers} ungrounded answer(s)",
                    detail="Substantive answers without supporting retrieval.",
                )
            )
        for h in report.groundedness.honest_grounding:
            out.append(
                Finding(
                    severity="info",
                    category="Quality",
                    title="Honest knowledge gap (good)",
                    detail=f"Agent acknowledged missing info for: {h}",
                )
            )

    if report.reasoning:
        for p in report.reasoning.premise_corrections:
            out.append(Finding(severity="info", category="Reasoning", title="Premise correction (good)", detail=p))

    if report.instructions:
        for chk in report.instructions.checks:
            if chk.status == "fail":
                out.append(
                    Finding(
                        severity="warning",
                        category="Instructions",
                        title=f"Instruction not met: {chk.instruction}",
                        detail=chk.evidence,
                    )
                )

    if report.cross_reference:
        for s in report.cross_reference.unused_knowledge_sources:
            out.append(
                Finding(
                    severity="warning",
                    category="Knowledge",
                    title=f"Knowledge source '{s}' never contributed",
                    detail="Defined in the agent but no retrieved document came from it in this conversation.",
                )
            )
        if report.cross_reference.tools_used_not_defined:
            out.append(
                Finding(
                    severity="info",
                    category="Tools",
                    title="Tools used but not defined in YAML",
                    detail=", ".join(report.cross_reference.tools_used_not_defined),
                )
            )

    if report.knowledge_effectiveness:
        for src in report.knowledge_effectiveness.sources:
            if src.zero_contribution:
                out.append(
                    Finding(
                        severity="warning",
                        category="Knowledge",
                        title=f"Knowledge source '{src.display_name}' retrieved but never cited",
                        detail=f"{src.docs_retrieved} document(s) retrieved, 0 used in an answer.",
                    )
                )
        if report.knowledge_effectiveness.unattributed_docs and report.agent and report.agent.knowledge_sources:
            out.append(
                Finding(
                    severity="info",
                    category="Knowledge",
                    title=f"{report.knowledge_effectiveness.unattributed_docs} retrieved doc(s) matched no configured source",
                    detail="Their URL did not trace to any knowledge source defined in the agent YAML.",
                )
            )

    if report.citation_audit and report.citation_audit.dangling:
        out.append(
            Finding(
                severity="warning",
                category="Citations",
                title=f"{report.citation_audit.dangling} dangling citation(s)",
                detail="A [n] marker whose referenced document was not found in any knowledge search result.",
            )
        )

    if report.credit_estimate:
        out.append(
            Finding(
                severity="info",
                category="Cost",
                title=f"Estimated {report.credit_estimate.total_credits:g} Copilot Credit(s) for this conversation",
                detail="Heuristic estimate — see the Credit Estimate panel for the per-step breakdown and disclaimer.",
            )
        )

    tf = report.tool_failures
    if tf and tf.total_failures:
        embedded_note = (
            f" ({tf.embedded_failures} hidden behind a 'completed' status)" if tf.embedded_failures else ""
        )
        out.append(
            Finding(
                severity="warning" if tf.gave_up == 0 else "critical",
                category="Tools",
                title=f"{tf.total_failures} tool failure(s){embedded_note}",
                detail=(
                    f"{tf.recovered} recovered, {tf.gave_up} unrecovered. "
                    + "; ".join(f"{f.name}: {f.error_text}" for f in tf.failures[:3])
                ),
            )
        )

    te = report.tool_efficiency
    if te and te.duplicate_groups:
        worst = te.duplicate_groups[0]
        out.append(
            Finding(
                severity="warning",
                category="Tools",
                title=f"{te.redundant_calls} redundant tool call(s)",
                detail=f"e.g. {worst.name} called {worst.count}× with identical parameters.",
            )
        )

    if report.repetition:
        for sig in report.repetition.signals:
            out.append(
                Finding(
                    severity="warning",
                    category="Quality",
                    title=f"Repetition detected ({sig.kind})",
                    detail=f"Turns {', '.join(str(t) for t in sig.turns)} — {sig.excerpt}",
                )
            )

    ag = report.answer_groundedness
    if ag and ag.high_risk:
        worst = next((a for a in ag.answers if a.risk == "high"), None)
        out.append(
            Finding(
                severity="critical",
                category="Quality",
                title=f"{ag.high_risk} answer(s) with high hallucination risk",
                detail=(f"Turn {worst.turn_index}: {worst.excerpt}" if worst else "Factual claims with no citation."),
            )
        )

    if report.coverage_gaps and report.coverage_gaps.gaps:
        gaps = report.coverage_gaps.gaps
        out.append(
            Finding(
                severity="warning",
                category="Knowledge",
                title=f"{len(gaps)} knowledge coverage gap(s)",
                detail="; ".join(f"turn {g.turn_index} ({g.reason})" for g in gaps[:4]),
            )
        )

    qf = report.quote_faithfulness
    if qf and qf.unattributed:
        out.append(
            Finding(
                severity="warning",
                category="Citations",
                title=f"{qf.unattributed} direct quote(s) without attribution",
                detail="A quoted passage with no nearby citation and no matching tool output.",
            )
        )

    ci = report.code_interpreter
    if ci and ci.used:
        out.append(
            Finding(
                severity="info",
                category="Sandbox",
                title=f"Code interpreter used in {ci.turns_with_code} turn(s)",
                detail="Sandbox activity ("
                + (", ".join(ci.distinct_tools[:6]) or "shell")
                + ") detected in reasoning — read/preprocessed documents outside of tool calls.",
            )
        )
    if ci and ci.friction_count:
        unrec = [f for f in ci.friction if not f.recovered]
        out.append(
            Finding(
                severity="warning" if unrec else "info",
                category="Sandbox",
                title=f"{ci.friction_count} sandbox friction episode(s)"
                + (" (all recovered)" if not unrec else ""),
                detail=ci.friction[0].excerpt,
            )
        )
    if ci and ci.authoring_turns:
        out.append(
            Finding(
                severity="info",
                category="Sandbox",
                title=f"Code interpreter authored a file in {len(ci.authoring_turns)} turn(s)",
                detail="The agent wrote code to generate an output file (e.g. python-pptx) rather than "
                "only analysing documents — turn " + ", ".join(str(t) for t in ci.authoring_turns) + ".",
            )
        )
    if ci and ci.skill_gaps:
        g = ci.skill_gaps[0]
        out.append(
            Finding(
                severity="info",
                category="Sandbox",
                title=f"Skill gap — fell back to {g.fallback or 'raw code'}"
                + (f" (wanted a '{g.wanted}' skill)" if g.wanted else ""),
                detail="No suitable skill was available, so the agent wrote code directly: " + g.excerpt,
            )
        )

    ga = report.generated_artifacts
    if ga and ga.items:
        kinds = ", ".join(f"{v}× {k}" for k, v in ga.by_type.items())
        out.append(
            Finding(
                severity="info",
                category="Artifacts",
                title=f"Agent produced {ga.count} downloadable file(s)",
                detail=f"Generated output attached to the conversation ({kinds}): "
                + ", ".join(it.name for it in ga.items[:5])
                + ". Counts toward what the user can take away — verify it is grounded in the cited sources.",
            )
        )

    gp = report.grounding_pipeline
    if gp and gp.snippet_mode == "stub":
        out.append(
            Finding(
                severity="info",
                category="Knowledge",
                title="Citations are document-level (search returned download stubs, not text)",
                detail="Knowledge search returned 'open the file in the sandbox' notices rather than content, "
                "so a citation proves which document was used, not which passage. The exact span is only "
                "inferable from the agent's reasoning.",
            )
        )

    # G5 — answers that cite a source which was never retrieved (grounding unverifiable).
    ca = report.citation_audit
    if ca and ca.rows:
        dangling_turns = sorted({r.turn_index for r in ca.rows if r.status == "dangling" and r.turn_index is not None})
        if dangling_turns:
            out.append(
                Finding(
                    severity="warning",
                    category="Citations",
                    title=f"Unverifiable grounding in {len(dangling_turns)} turn(s)",
                    detail="Answer(s) cite a [n] marker whose source was never returned by any knowledge "
                    "search, so the claim cannot be traced to a document — turn "
                    + ", ".join(str(t) for t in dangling_turns) + ".",
                )
            )

    rd = report.retrieval_depth
    if rd and rd.unique_docs and rd.over_retrieval_ratio >= 0.8:
        out.append(
            Finding(
                severity="info",
                category="Knowledge",
                title=f"Over-retrieval: {rd.cited_docs} of {rd.unique_docs} retrieved doc(s) cited",
                detail=f"{int(rd.over_retrieval_ratio * 100)}% of unique retrieved documents were never "
                f"cited; retrieval mode '{rd.retrieval_mode}'.",
            )
        )

    ss = report.search_strategy
    if ss and ss.recall_turns:
        out.append(
            Finding(
                severity="info",
                category="Knowledge",
                title=f"{len(ss.recall_turns)} turn(s) answered from earlier retrieval",
                detail="Re-used previously retrieved knowledge without a new search (efficient): turn "
                + ", ".join(str(t.turn_index) for t in ss.recall_turns),
            )
        )

    ce = report.credit_estimate
    if ce and ce.reasoning_model:
        out.append(
            Finding(
                severity="info",
                category="Credits",
                title="Reasoning model — premium token meter applies",
                detail=f"≈{ce.total_tokens} tokens estimated; premium AI-tools cost stacks on top of "
                "feature charges. See the Credit Estimate panel.",
            )
        )

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: severity_rank.get(f.severity, 3))
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def analyze(profile: AgentProfile | None, convo: Conversation | None) -> AnalysisReport:
    """Run all heuristic analysis, degrading gracefully when an input is missing."""
    report = AnalysisReport(agent=profile)

    if convo is not None:
        report.overview = build_overview(convo)
        report.tools = analyze_tools(convo)
        report.knowledge = analyze_knowledge(convo)
        report.citations = analyze_citations(convo)
        report.reasoning = analyze_reasoning(convo)
        report.groundedness = assess_groundedness(convo, report.knowledge)
        report.tool_failures = analyze_tool_failures(convo)
        report.tool_efficiency = analyze_tool_efficiency(convo)
        report.repetition = detect_repetition(convo)
        report.answer_groundedness = assess_answer_groundedness(convo, report.knowledge)
        report.quote_faithfulness = verify_quote_faithfulness(convo, report.knowledge)
        report.coverage_gaps = analyze_coverage_gaps(convo, report.knowledge)
        report.turn_economy = analyze_turn_economy(convo)
        report.code_interpreter = analyze_code_interpreter(convo)
        report.retrieval_depth = analyze_retrieval_depth(convo, report.knowledge, report.code_interpreter)
        report.search_strategy = analyze_search_strategy(convo, report.knowledge)
        report.generated_artifacts = analyze_generated_artifacts(convo)
        report.grounding_pipeline = analyze_grounding_pipeline(convo, report.knowledge, report.code_interpreter)

    report.knowledge_effectiveness = analyze_knowledge_effectiveness(profile, report.knowledge)
    report.citation_audit = verify_citations(convo, report.knowledge)
    report.credit_estimate = estimate_credits(profile, convo)

    report.instructions = check_instructions(profile, convo)
    report.cross_reference = cross_reference(profile, convo, report.knowledge)
    report.findings = collect_findings(report)

    logger.info(
        f"Analysis: {len(report.findings)} finding(s)"
        + (f", {report.overview.tool_call_count} tool call(s)" if report.overview else "")
    )
    return report
