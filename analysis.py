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
    CitationAnalysis,
    CitationAudit,
    CitationAuditRow,
    Conversation,
    ConversationOverview,
    CreditEstimate,
    CreditLineItem,
    CrossReference,
    Finding,
    GroundednessAssessment,
    InstructionCheck,
    InstructionCompliance,
    KnowledgeAnalysis,
    KnowledgeEffectiveness,
    KnowledgeQuery,
    ReasoningTrace,
    RetrievedDoc,
    SourceEffectiveness,
    ToolAnalysis,
    ToolCall,
    ToolUsage,
)

from config import CREDIT_SOURCE_URL, credit_rates

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
        failed_tool_count=sum(1 for t in tool_calls if t.failed),
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
            ok = thoughts > 0 or multi > 0
            add(
                "Emit intermediate / chain-of-thought messages",
                "reasoning blocks or multi-message streaming present",
                "pass" if ok else "fail",
                f"{thoughts} reasoning block(s); {multi} turn(s) streamed multiple messages",
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

    for turn in convo.turns:
        # Provenance + candidate docs for this turn's searches (fall back to all).
        turn_docs: list[tuple[RetrievedDoc, str | None]] = []
        for tc in turn.tool_calls:
            if tc.is_knowledge_search:
                for d in tc.retrieved_docs:
                    turn_docs.append((d, tc.query))
        candidates = turn_docs or [(d, None) for d in distinct]

        resolved_markers: dict[str, tuple[RetrievedDoc, str | None]] = {}
        for msg in turn.bot_messages:
            for m in _CITATION_RE.finditer(msg.text):
                marker = m.group(0)
                ctx = _normalize_title(msg.text[max(0, m.start() - 90) : m.start()])
                hit = next(((d, q) for d, q in candidates if _title_in_context(d.title, ctx)), None)
                # A repeated marker that already resolved this turn reuses that doc.
                if hit is None and marker in resolved_markers:
                    hit = resolved_markers[marker]
                if hit:
                    doc, query = hit
                    resolved_markers[marker] = hit
                    audit.rows.append(
                        CitationAuditRow(
                            marker=marker,
                            reference_id=doc.reference_id,
                            status="resolved",
                            doc_title=doc.title,
                            doc_url=doc.url,
                            source=_site_root(doc.url),
                            turn_index=turn.index,
                            provenance=query,
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
    """Heuristic MCS Copilot Credit estimate from runtime events. Each knowledge
    search and each substantive generated answer bills as a generative answer;
    each action/skill tool call bills as an agent action. Rates are configurable
    and the result is explicitly an estimate."""
    if convo is None:
        return None

    rates = credit_rates()
    items: list[CreditLineItem] = []

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
                )
            )
        for a in actions:
            items.append(
                CreditLineItem(
                    label=f"Turn {turn.index}: {a.display_name or a.name or 'action'}",
                    kind="agent_action",
                    credits=rates["agent_action"],
                    detail=classify_tool_kind(a),
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
                )
            )

    by_kind: dict[str, float] = {}
    for it in items:
        by_kind[it.kind] = round(by_kind.get(it.kind, 0.0) + it.credits, 2)
    total = round(sum(it.credits for it in items), 2)

    notes = [
        "Heuristic estimate only — real Copilot Credit consumption depends on tenant "
        "configuration, message size and Microsoft's current billing model.",
        f"Rates: classic answer {rates['classic_answer']}, generative answer "
        f"{rates['generative_answer']}, agent action {rates['agent_action']} credits "
        "(override via CREDIT_CLASSIC_ANSWER / CREDIT_GENERATIVE_ANSWER / CREDIT_AGENT_ACTION).",
        f"Billing rates source: {CREDIT_SOURCE_URL}",
    ]
    if profile is not None and profile.model_label:
        notes.insert(0, f"Agent model: {profile.model_label}.")

    return CreditEstimate(line_items=items, total_credits=total, by_kind=by_kind, notes=notes)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


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
