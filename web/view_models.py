"""View-models: flatten AnalysisReport + Conversation into typed, UI-friendly
structures the Reflex layer can `rx.foreach` over.

Pure module (dataclasses only) so it is unit-testable without Reflex.
"""

import re
from dataclasses import dataclass, field

from explainer import explain
from models import AnalysisReport, Conversation, Message, ToolCall
from renderer import render_sequence_diagram

_CITATION_RE = re.compile(r"\[(\d+)\]")
_ACTION_PARAM_KEYS = {"content", "contenttype", "memberupns", "useridorupn", "recipient", "chatid", "messageid"}
_ACTION_NAME_PREFIXES = ("send", "list", "create", "update", "delete", "post", "get", "add", "remove")

_SEVERITY_STYLE = {
    "critical": ("octagon-alert", "red"),
    "warning": ("triangle-alert", "amber"),
    "info": ("info", "blue"),
}
_CHECK_STYLE = {
    "pass": ("circle-check", "grass"),
    "fail": ("circle-x", "red"),
    "unknown": ("circle-help", "gray"),
}

_CITATION_STATUS_STYLE = {
    "resolved": ("Resolved", "circle-check", "grass"),
    "dangling": ("Dangling", "circle-x", "red"),
    "uncited_retrieval": ("Uncited retrieval", "circle-minus", "amber"),
}
_CREDIT_KIND_STYLE = {
    "generative_answer": ("Generative answer", "sparkles", "blue"),
    "agent_action": ("Agent action", "zap", "amber"),
    "classic_answer": ("Classic answer", "message-square", "gray"),
}
_COMPONENT_CATEGORY_ICON = {
    "Agent settings": "settings",
    "Knowledge": "book-open",
    "Environment variables": "braces",
    "Tools & actions": "wrench",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class KV:
    key: str = ""
    value: str = ""


@dataclass
class DocVM:
    title: str = ""
    url: str = ""
    reference_id: str = ""
    cited: bool = False
    unused: bool = False


@dataclass
class CitationVM:
    label: str = ""  # e.g. "[1]"
    reference_id: str = ""  # mapped doc, best-effort (may be empty)


@dataclass
class ToolCallVM:
    kind: str = "other"  # retrieval / action / skill / other
    name: str = ""
    display_name: str = ""
    status: str = ""
    failed: bool = False
    icon: str = "wrench"
    # retrieval
    query: str = ""
    result_count: int = 0
    zero_result: bool = False
    docs: list[DocVM] = field(default_factory=list)
    # action
    recipient: str = ""
    content_type: str = ""
    content_html: str = ""
    content_text: str = ""
    # generic
    params: list[KV] = field(default_factory=list)
    raw_result: str = ""


@dataclass
class ChatBlockVM:
    idx: int = 0
    kind: str = "agent"  # user / agent
    text: str = ""
    thoughts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallVM] = field(default_factory=list)
    citations: list[CitationVM] = field(default_factory=list)
    search_text: str = ""  # lowercased haystack for the transcript filter


@dataclass
class TurnVM:
    index: int = 0
    question: str = ""
    answer: str = ""
    searches: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    doc_count: int = 0


@dataclass
class FindingVM:
    severity: str = "info"
    category: str = ""
    title: str = ""
    detail: str = ""
    icon: str = "info"
    color: str = "blue"


@dataclass
class ToolRowVM:
    name: str = ""
    kind: str = "other"
    count: int = 0
    completed: int = 0
    failed: int = 0


@dataclass
class KnowledgeQueryVM:
    query: str = ""
    result_count: int = 0
    zero_result: bool = False
    docs: list[DocVM] = field(default_factory=list)


@dataclass
class CheckVM:
    instruction: str = ""
    check: str = ""
    status: str = "unknown"
    evidence: str = ""
    icon: str = "circle-help"
    color: str = "gray"


@dataclass
class KSourceVM:
    name: str = ""
    type: str = ""
    url: str = ""
    state: str = ""
    unused: bool = False


@dataclass
class EnvVarVM:
    name: str = ""
    type: str = ""
    default: str = ""


@dataclass
class SourceEffVM:
    name: str = ""
    type: str = ""
    configured: bool = True
    retrieved: int = 0
    cited: int = 0
    rate_pct: int = 0
    status: str = "active"  # active / no-citations / dead / runtime
    icon: str = "circle-check"
    color: str = "grass"


@dataclass
class CitationRowVM:
    marker: str = ""
    status: str = "resolved"  # resolved / dangling / uncited_retrieval
    status_label: str = ""
    icon: str = "circle-check"
    color: str = "grass"
    doc_title: str = ""
    doc_url: str = ""
    source: str = ""
    turn_index: str = ""
    provenance: str = ""


@dataclass
class CreditLineVM:
    label: str = ""
    kind: str = ""
    kind_label: str = ""
    credits: str = ""
    detail: str = ""
    icon: str = "circle"
    color: str = "gray"


@dataclass
class CreditKindVM:
    kind_label: str = ""
    credits: str = ""
    icon: str = "circle"
    color: str = "gray"


@dataclass
class ComponentVM:
    id: str = ""
    category: str = ""
    label: str = ""
    value: str = ""
    summary: str = ""
    doc: str = ""
    documented: bool = True
    icon: str = "settings"
    search_text: str = ""


@dataclass
class ReportVM:
    # agent
    has_agent: bool = False
    agent_name: str = "Modern agent"
    model_label: str = "—"
    template: str = ""
    recognizer: str = ""
    auth: str = ""
    memory: bool = False
    instructions: str = ""
    conversation_starters: list[str] = field(default_factory=list)
    knowledge_sources: list[KSourceVM] = field(default_factory=list)
    env_vars: list[EnvVarVM] = field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""
    # conversation
    has_convo: bool = False
    # overview scalars
    m_turns: int = 0
    m_user: int = 0
    m_bot: int = 0
    m_tools: int = 0
    m_searches: int = 0
    m_thoughts: int = 0
    m_failed: int = 0
    m_zero: int = 0
    # findings
    findings: list[FindingVM] = field(default_factory=list)
    f_critical: int = 0
    f_warning: int = 0
    f_info: int = 0
    # tools / actions
    tool_rows: list[ToolRowVM] = field(default_factory=list)
    skill_loads: list[str] = field(default_factory=list)
    retry_signals: list[str] = field(default_factory=list)
    tool_failures: list[str] = field(default_factory=list)
    # knowledge
    knowledge_queries: list[KnowledgeQueryVM] = field(default_factory=list)
    uncited_docs: list[DocVM] = field(default_factory=list)
    sources_seen: list[str] = field(default_factory=list)
    zero_result_queries: list[str] = field(default_factory=list)
    # citations
    citation_markers: int = 0
    uncited_answer_count: int = 0
    # citation audit (#4)
    citation_rows: list[CitationRowVM] = field(default_factory=list)
    cit_resolved: int = 0
    cit_dangling: int = 0
    cit_uncited: int = 0
    # knowledge effectiveness (#3)
    source_effectiveness: list[SourceEffVM] = field(default_factory=list)
    eff_total_searches: int = 0
    eff_distinct_docs: int = 0
    eff_avg_docs: str = "0"
    eff_unattributed: int = 0
    # credit estimate (#9)
    credit_lines: list[CreditLineVM] = field(default_factory=list)
    credit_by_kind: list[CreditKindVM] = field(default_factory=list)
    credit_total: str = "0"
    credit_notes: list[str] = field(default_factory=list)
    has_credits: bool = False
    # component explorer (#8)
    components: list[ComponentVM] = field(default_factory=list)
    # reasoning
    premise_corrections: list[str] = field(default_factory=list)
    thoughts_per_turn: list[int] = field(default_factory=list)
    # groundedness
    grounded: int = 0
    ungrounded: int = 0
    hallucination_risk: list[str] = field(default_factory=list)
    honest_grounding: list[str] = field(default_factory=list)
    groundedness_notes: list[str] = field(default_factory=list)
    # instruction compliance
    checks: list[CheckVM] = field(default_factory=list)
    # cross reference
    unused_knowledge_sources: list[str] = field(default_factory=list)
    contributing_knowledge_sources: list[str] = field(default_factory=list)
    tools_used_not_defined: list[str] = field(default_factory=list)
    # conversation views
    chat: list[ChatBlockVM] = field(default_factory=list)
    turns: list[TurnVM] = field(default_factory=list)
    mermaid: str = ""
    raw_transcript: str = ""


# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------


def classify_tool(tc: ToolCall) -> str:
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


_KIND_ICON = {"retrieval": "search", "action": "send", "skill": "puzzle", "other": "wrench"}


def _docs_from_tool(tc: ToolCall, cited_ids: set[str], uncited_ids: set[str]) -> list[DocVM]:
    out: list[DocVM] = []
    for d in tc.retrieved_docs:
        rid = d.reference_id or ""
        out.append(
            DocVM(
                title=d.title or rid or "(untitled)",
                url=d.url or "",
                reference_id=rid,
                cited=rid in cited_ids,
                unused=rid in uncited_ids,
            )
        )
    return out


def _tool_to_vm(tc: ToolCall, cited_ids: set[str], uncited_ids: set[str]) -> ToolCallVM:
    kind = classify_tool(tc)
    params = tc.params if isinstance(tc.params, dict) else {}
    content = params.get("content") if isinstance(params.get("content"), str) else ""
    ctype = params.get("contentType") or params.get("contenttype") or ""
    recipient = params.get("userIdOrUpn") or params.get("memberUpns") or params.get("recipient") or ""
    kv = [KV(key=str(k), value=str(v)[:200]) for k, v in params.items() if str(k).lower() != "content"]
    is_html = "html" in str(ctype).lower()
    return ToolCallVM(
        kind=kind,
        name=tc.name or "",
        display_name=tc.display_name or tc.name or "",
        status=tc.status or "",
        failed=tc.failed,
        icon=_KIND_ICON.get(kind, "wrench"),
        query=tc.query or "",
        result_count=tc.result_count or len(tc.retrieved_docs),
        zero_result=tc.zero_result,
        docs=_docs_from_tool(tc, cited_ids, uncited_ids),
        recipient=str(recipient),
        content_type=str(ctype),
        content_html=content if is_html else "",
        content_text="" if is_html else content,
        params=kv,
        raw_result=(tc.result or "")[:4000],
    )


# ---------------------------------------------------------------------------
# Citation mapping (cross-turn: nearest preceding search)
# ---------------------------------------------------------------------------


def _map_citations(text: str, current_docs: list) -> list[CitationVM]:
    out: list[CitationVM] = []
    seen: set[str] = set()
    for m in _CITATION_RE.finditer(text or ""):
        label = m.group(0)
        if label in seen:
            continue
        seen.add(label)
        n = int(m.group(1))
        rid = ""
        if current_docs and 1 <= n <= len(current_docs):
            rid = current_docs[n - 1].reference_id or ""
        out.append(CitationVM(label=label, reference_id=rid))
    return out


# ---------------------------------------------------------------------------
# Chat + turn views
# ---------------------------------------------------------------------------


def _build_chat(convo: Conversation, cited_ids: set[str], uncited_ids: set[str]) -> list[ChatBlockVM]:
    chat: list[ChatBlockVM] = []
    current_docs: list = []  # docs of the nearest preceding KnowledgeSearch
    for i, m in enumerate(convo.messages):
        if m.is_user:
            chat.append(ChatBlockVM(idx=i, kind="user", text=m.text, search_text=(m.text or "").lower()))
            continue
        tool_vms = [_tool_to_vm(tc, cited_ids, uncited_ids) for tc in m.tool_calls]
        for tc in m.tool_calls:
            if tc.is_knowledge_search and tc.retrieved_docs:
                current_docs = tc.retrieved_docs
        citations = _map_citations(m.text, current_docs)
        thoughts = [t.text for t in m.thoughts if t.text.strip()]
        hay = " ".join([m.text or ""] + thoughts + [tv.query for tv in tool_vms] + [tv.display_name for tv in tool_vms])
        chat.append(
            ChatBlockVM(
                idx=i,
                kind="agent",
                text=m.text,
                thoughts=thoughts,
                tool_calls=tool_vms,
                citations=citations,
                search_text=hay.lower(),
            )
        )
    return chat


def _turn_doc_count(bot_messages: list[Message]) -> int:
    rids: set[str] = set()
    for m in bot_messages:
        for tc in m.tool_calls:
            for d in tc.retrieved_docs:
                if d.reference_id:
                    rids.add(d.reference_id)
    return len(rids)


def _build_turns(convo: Conversation) -> list[TurnVM]:
    turns: list[TurnVM] = []
    for t in convo.turns:
        searches = [tc.query for tc in t.tool_calls if tc.is_knowledge_search and tc.query]
        actions = [
            (tc.display_name or tc.name or "action")
            for tc in t.tool_calls
            if classify_tool(tc) in {"action", "skill"}
        ]
        cites: list[str] = []
        for m in t.bot_messages:
            for c in _CITATION_RE.findall(m.text or ""):
                if c not in cites:
                    cites.append(c)
        turns.append(
            TurnVM(
                index=t.index,
                question=(t.user_message.text if t.user_message else "Session start"),
                answer=t.final_bot_text,
                searches=searches,
                actions=actions,
                citations=cites,
                doc_count=_turn_doc_count(t.bot_messages),
            )
        )
    return turns


# ---------------------------------------------------------------------------
# Feature builders (#3 effectiveness, #4 citation audit, #8 explorer, #9 credits)
# ---------------------------------------------------------------------------


def _build_source_effectiveness(report: AnalysisReport) -> list[SourceEffVM]:
    eff = report.knowledge_effectiveness
    if eff is None:
        return []
    out: list[SourceEffVM] = []
    for s in eff.sources:
        if s.never_retrieved:
            status, icon, color = "dead", "circle-x", "red"
        elif s.zero_contribution:
            status, icon, color = "no-citations", "circle-minus", "amber"
        elif not s.configured:
            status, icon, color = "runtime", "radio", "blue"
        else:
            status, icon, color = "active", "circle-check", "grass"
        out.append(
            SourceEffVM(
                name=s.display_name,
                type=s.source_kind or "",
                configured=s.configured,
                retrieved=s.docs_retrieved,
                cited=s.docs_cited,
                rate_pct=int(round(s.contribution_rate * 100)),
                status=status,
                icon=icon,
                color=color,
            )
        )
    return out


def _build_citation_rows(report: AnalysisReport) -> list[CitationRowVM]:
    audit = report.citation_audit
    if audit is None:
        return []
    rank = {"dangling": 0, "resolved": 1, "uncited_retrieval": 2}
    rows = sorted(audit.rows, key=lambda r: (rank.get(r.status, 3), r.turn_index or 0))
    out: list[CitationRowVM] = []
    for r in rows:
        label, icon, color = _CITATION_STATUS_STYLE.get(r.status, ("?", "circle-help", "gray"))
        out.append(
            CitationRowVM(
                marker=r.marker,
                status=r.status,
                status_label=label,
                icon=icon,
                color=color,
                doc_title=r.doc_title or "",
                doc_url=r.doc_url or "",
                source=r.source or "",
                turn_index=str(r.turn_index) if r.turn_index is not None else "",
                provenance=r.provenance or "",
            )
        )
    return out


def _build_credits(report: AnalysisReport) -> tuple[list[CreditLineVM], list[CreditKindVM], str, list[str], bool]:
    est = report.credit_estimate
    if est is None:
        return [], [], "0", [], False

    def _fmt(n: float) -> str:
        return f"{n:g}"

    lines = [
        CreditLineVM(
            label=it.label,
            kind=it.kind,
            kind_label=_CREDIT_KIND_STYLE.get(it.kind, (it.kind, "circle", "gray"))[0],
            credits=_fmt(it.credits),
            detail=it.detail,
            icon=_CREDIT_KIND_STYLE.get(it.kind, (it.kind, "circle", "gray"))[1],
            color=_CREDIT_KIND_STYLE.get(it.kind, (it.kind, "circle", "gray"))[2],
        )
        for it in est.line_items
    ]
    by_kind = [
        CreditKindVM(
            kind_label=_CREDIT_KIND_STYLE.get(k, (k, "circle", "gray"))[0],
            credits=_fmt(v),
            icon=_CREDIT_KIND_STYLE.get(k, (k, "circle", "gray"))[1],
            color=_CREDIT_KIND_STYLE.get(k, (k, "circle", "gray"))[2],
        )
        for k, v in sorted(est.by_kind.items(), key=lambda kv: -kv[1])
    ]
    return lines, by_kind, _fmt(est.total_credits), list(est.notes), True


def _comp(idx: int, category: str, label: str, value: str, key: str, kvalue: str | None = None) -> ComponentVM:
    ex = explain(key, kvalue)
    icon = _COMPONENT_CATEGORY_ICON.get(category, "settings")
    return ComponentVM(
        id=f"{category}-{idx}",
        category=category,
        label=label,
        value=value,
        summary=ex.summary,
        doc=ex.doc or "",
        documented=ex.documented,
        icon=icon,
        search_text=f"{label} {value} {category} {ex.summary}".lower(),
    )


def _knowledge_component(idx: int, ks) -> ComponentVM:
    """Pick the most specific documented KB entry for a knowledge source kind."""
    specific = f"knowledge.{ks.source_kind}" if ks.source_kind else "knowledge"
    ex = explain(specific)
    if not ex.documented:
        ex = explain("knowledge")
    cat = "Knowledge"
    return ComponentVM(
        id=f"{cat}-{idx}",
        category=cat,
        label=ks.display_name or "(knowledge source)",
        value=ks.source_kind or "",
        summary=ex.summary,
        doc=ex.doc or "",
        documented=ex.documented,
        icon=_COMPONENT_CATEGORY_ICON[cat],
        search_text=f"{ks.display_name} {ks.source_kind} {ks.source_site} {ex.summary}".lower(),
    )


def _build_components(report: AnalysisReport, convo: Conversation | None) -> list[ComponentVM]:
    out: list[ComponentVM] = []
    p = report.agent
    i = 0

    if p is not None:
        settings: list[tuple[str, str, str, str | None]] = []  # (label, value, key, kvalue)
        if p.model_label or p.model_series:
            settings.append(("Model", p.model_label or p.model_series or "", "model", None))
        if p.is_modern:
            settings.append(("Orchestration", "Generative orchestration", "orchestration", None))
        if p.instructions:
            n = len(p.instruction_segments) or 1
            settings.append(("Instructions", f"{n} segment(s)", "instructions", None))
        if p.authentication_mode:
            settings.append(("Authentication mode", p.authentication_mode, "authenticationMode", p.authentication_mode))
        if p.authentication_trigger:
            settings.append(
                ("Authentication trigger", p.authentication_trigger, "authenticationTrigger", p.authentication_trigger)
            )
        if p.access_control_policy:
            settings.append(
                ("Access control", p.access_control_policy, "accessControlPolicy", p.access_control_policy)
            )
        settings.append(("Memory", "Enabled" if p.enable_memory else "Disabled", "enableMemory", None))
        if p.conversation_starters:
            settings.append(
                ("Conversation starters", f"{len(p.conversation_starters)} starter(s)", "conversationStarters", None)
            )
        if p.recognizer_kind:
            settings.append(("Recognizer", p.recognizer_kind, "recognizer", None))
        if p.template:
            settings.append(("Template", p.template, "template", None))
        if p.runtime_provider:
            settings.append(("Runtime provider", p.runtime_provider, "runtimeProvider", None))
        for label, value, key, kvalue in settings:
            out.append(_comp(i, "Agent settings", label, value, key, kvalue))
            i += 1

        for ks in p.knowledge_sources:
            out.append(_knowledge_component(i, ks))
            i += 1

        for ev in p.environment_variables:
            label = ev.display_name or ev.schema_name or "(env var)"
            value = ev.type or (ev.default_value or "")
            out.append(_comp(i, "Environment variables", label, value, "environmentVariable", None))
            i += 1

        for tc in p.tool_components:
            out.append(_comp(i, "Tools & actions", tc.display_name or tc.kind, tc.kind, "tool", None))
            i += 1

    # Runtime-observed action/skill tools (covers transcript-only inputs).
    defined = {(tc.display_name or "").lower() for tc in (p.tool_components if p else [])}
    seen: set[str] = set()
    if convo is not None:
        for tcall in convo.tool_calls:
            kind = classify_tool(tcall)
            if kind not in {"action", "skill"}:
                continue
            label = tcall.display_name or tcall.name or "action"
            lk = label.lower()
            if lk in defined or lk in seen:
                continue
            seen.add(lk)
            out.append(_comp(i, "Tools & actions", label, "observed at runtime", "tool", None))
            i += 1

    return out


# ---------------------------------------------------------------------------
# Top-level mapping
# ---------------------------------------------------------------------------


def map_report(report: AnalysisReport, convo: Conversation | None, raw_transcript: str = "") -> ReportVM:
    vm = ReportVM(raw_transcript=raw_transcript[:200000])

    p = report.agent
    if p is not None:
        vm.has_agent = True
        vm.agent_name = p.display_name
        vm.model_label = p.model_label or p.model_series or "—"
        vm.template = p.template or ""
        vm.recognizer = p.recognizer_kind or ""
        vm.auth = p.authentication_mode or ""
        vm.memory = p.enable_memory
        vm.instructions = p.instructions
        vm.conversation_starters = list(p.conversation_starters)
        vm.created_at = p.created_at or ""
        vm.modified_at = p.modified_at or ""
        unused_names = set(report.cross_reference.unused_knowledge_sources) if report.cross_reference else set()
        vm.knowledge_sources = [
            KSourceVM(
                name=ks.display_name or "",
                type=ks.source_kind or "",
                url=ks.source_site or "",
                state=ks.state or "",
                unused=(ks.display_name in unused_names),
            )
            for ks in p.knowledge_sources
        ]
        vm.env_vars = [
            EnvVarVM(name=ev.display_name or ev.schema_name or "", type=ev.type or "", default=ev.default_value or "")
            for ev in p.environment_variables
        ]

    cited_ids: set[str] = set()
    uncited_ids: set[str] = set()
    if report.knowledge is not None:
        cited_ids = set(report.knowledge.cited_reference_ids)
        uncited_ids = {d.reference_id for d in report.knowledge.uncited_docs if d.reference_id}
        vm.knowledge_queries = [
            KnowledgeQueryVM(
                query=q.query,
                result_count=q.result_count,
                zero_result=q.zero_result,
                docs=[
                    DocVM(
                        title=d.title or d.reference_id or "(untitled)",
                        url=d.url or "",
                        reference_id=d.reference_id or "",
                        cited=(d.reference_id in cited_ids),
                        unused=(d.reference_id in uncited_ids),
                    )
                    for d in q.docs
                ],
            )
            for q in report.knowledge.queries
        ]
        vm.uncited_docs = [
            DocVM(title=d.title or d.reference_id or "(untitled)", url=d.url or "", reference_id=d.reference_id or "")
            for d in report.knowledge.uncited_docs
        ]
        vm.sources_seen = list(report.knowledge.sources_seen)
        vm.zero_result_queries = list(report.knowledge.zero_result_queries)

    ov = report.overview
    if ov is not None:
        vm.has_convo = True
        vm.m_turns, vm.m_user, vm.m_bot = ov.turn_count, ov.user_message_count, ov.bot_message_count
        vm.m_tools, vm.m_searches, vm.m_thoughts = ov.tool_call_count, ov.knowledge_search_count, ov.thought_count
        vm.m_failed, vm.m_zero = ov.failed_tool_count, ov.zero_result_search_count

    for f in report.findings:
        icon, color = _SEVERITY_STYLE.get(f.severity, ("info", "blue"))
        vm.findings.append(
            FindingVM(severity=f.severity, category=f.category, title=f.title, detail=f.detail, icon=icon, color=color)
        )
    vm.f_critical = sum(1 for f in report.findings if f.severity == "critical")
    vm.f_warning = sum(1 for f in report.findings if f.severity == "warning")
    vm.f_info = sum(1 for f in report.findings if f.severity == "info")

    if report.tools is not None:
        kind_by_name = _kind_by_name(convo)
        vm.tool_rows = [
            ToolRowVM(
                name=u.name,
                kind=kind_by_name.get(u.name, "other"),
                count=u.count,
                completed=u.completed,
                failed=u.failed,
            )
            for u in report.tools.usage
        ]
        vm.skill_loads = list(report.tools.skill_loads)
        vm.retry_signals = list(report.tools.retry_signals)
        vm.tool_failures = list(report.tools.failures)

    if report.citations is not None:
        vm.citation_markers = report.citations.total_markers
        vm.uncited_answer_count = report.citations.uncited_answer_count

    if report.citation_audit is not None:
        vm.citation_rows = _build_citation_rows(report)
        vm.cit_resolved = report.citation_audit.resolved
        vm.cit_dangling = report.citation_audit.dangling
        vm.cit_uncited = report.citation_audit.uncited_retrievals

    if report.knowledge_effectiveness is not None:
        eff = report.knowledge_effectiveness
        vm.source_effectiveness = _build_source_effectiveness(report)
        vm.eff_total_searches = eff.total_searches
        vm.eff_distinct_docs = eff.distinct_docs
        vm.eff_avg_docs = f"{eff.avg_docs_per_search:g}"
        vm.eff_unattributed = eff.unattributed_docs

    vm.credit_lines, vm.credit_by_kind, vm.credit_total, vm.credit_notes, vm.has_credits = _build_credits(report)
    vm.components = _build_components(report, convo)

    if report.reasoning is not None:
        vm.premise_corrections = list(report.reasoning.premise_corrections)
        vm.thoughts_per_turn = list(report.reasoning.thoughts_per_turn)

    if report.groundedness is not None:
        g = report.groundedness
        vm.grounded, vm.ungrounded = g.grounded_answers, g.ungrounded_answers
        vm.hallucination_risk = list(g.hallucination_risk)
        vm.honest_grounding = list(g.honest_grounding)
        vm.groundedness_notes = list(g.notes)

    if report.instructions is not None:
        for c in report.instructions.checks:
            icon, color = _CHECK_STYLE.get(c.status, ("circle-help", "gray"))
            vm.checks.append(
                CheckVM(
                    instruction=c.instruction, check=c.check, status=c.status, evidence=c.evidence, icon=icon, color=color
                )
            )

    if report.cross_reference is not None:
        x = report.cross_reference
        vm.unused_knowledge_sources = list(x.unused_knowledge_sources)
        vm.contributing_knowledge_sources = list(x.contributing_knowledge_sources)
        vm.tools_used_not_defined = list(x.tools_used_not_defined)

    if convo is not None:
        vm.chat = _build_chat(convo, cited_ids, uncited_ids)
        vm.turns = _build_turns(convo)
        vm.mermaid = _strip_fence(render_sequence_diagram(convo, vm.agent_name))

    return vm


def _strip_fence(md: str) -> str:
    """`render_sequence_diagram` returns a ```mermaid fenced block; the web layer
    feeds the bare diagram into <pre class="mermaid">, so drop the fence."""
    s = md.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    return s.strip()


def _kind_by_name(convo: Conversation | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if convo is None:
        return out
    for tc in convo.tool_calls:
        if tc.name and tc.name not in out:
            out[tc.name] = classify_tool(tc)
    return out
