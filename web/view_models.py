"""View-models: flatten AnalysisReport + Conversation into typed, UI-friendly
structures the Reflex layer can `rx.foreach` over.

Pure module (dataclasses only) so it is unit-testable without Reflex.
"""

import re
from dataclasses import dataclass, field

from explainer import explain
from models import AnalysisReport, Conversation, Message, ToolCall
from renderer import render_sequence_diagram

from analysis import PROVIDER_META, build_tool_hierarchy, tool_failed
from config import CREDIT_ESTIMATOR_URL

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
    "premium_reasoning": ("Reasoning tokens (premium)", "brain", "purple"),
    "content_processing": ("Content processing", "file-text", "cyan"),
    "tenant_graph": ("Tenant graph grounding", "share-2", "teal"),
}
_SANDBOX_CATEGORY_STYLE = {
    "read-document": ("Read document", "file-search", "blue"),
    "preprocess": ("Preprocess / convert", "cog", "purple"),
    "inspect-fs": ("Inspect file system", "folder-tree", "amber"),
    "permissions": ("Permissions", "lock", "red"),
    "shell-other": ("Shell command", "terminal", "gray"),
}
_FRICTION_STYLE = {
    "permission-denied": ("Permission denied", "shield-alert", "red"),
    "alternative-approach": ("Changed approach", "rotate-ccw", "amber"),
    "retry": ("Retried", "refresh-cw", "blue"),
}
_COMPONENT_CATEGORY_ICON = {
    "Agent settings": "settings",
    "Knowledge": "book-open",
    "Environment variables": "braces",
    "Tools & actions": "wrench",
}
_GROUP_META = {
    "agent": ("Agent", "settings", "Core agent configuration and system instructions."),
    "knowledge": ("Knowledge sources", "book-open", "Grounding sources the agent can search."),
    "tools": ("Tools", "wrench", "Tools the agent can use, grouped by kind (MCP, connector, connected agent, flow, skill, action)."),
    "env": ("Environment variables", "braces", "Configuration values resolved per environment."),
}

# Styling for the new analysis features (#10/#5/#2/#11/#12).
_RECOVERY_STYLE = {
    "recovered-other-tool": ("Recovered via other tool", "circle-check", "grass"),
    "retried-same": ("Retried same tool", "rotate-ccw", "blue"),
    "unhandled-but-answered": ("Answered without recovery", "message-circle", "amber"),
    "gave-up": ("Gave up", "circle-x", "red"),
}
_REPETITION_STYLE = {
    "agent-answer": ("Repeated answer", "copy", "amber"),
    "agent-tool": ("Tool loop", "repeat", "red"),
    "user-question": ("Repeated question", "messages-square", "amber"),
}
_RISK_STYLE = {
    "low": ("Low risk", "circle-check", "grass"),
    "medium": ("Medium risk", "circle-alert", "amber"),
    "high": ("High risk", "triangle-alert", "red"),
}
_VERDICT_STYLE = {
    "verified-in-tool-output": ("Verified in tool output", "circle-check", "grass"),
    "attributed-source-in-sandbox": ("Attributed — source in sandbox", "file-check", "blue"),
    "dangling-attribution": ("Dangling attribution", "unlink", "red"),
    "unattributed-quote": ("Unattributed quote", "triangle-alert", "amber"),
}
_COVERAGE_STYLE = {
    "zero-result-search": ("Zero-result search", "search-x", "red"),
    "acknowledged-gap": ("Acknowledged gap", "shield-check", "amber"),
    "uncited-answer": ("Uncited answer", "message-square-warning", "amber"),
}
_TIMELINE_TOOL_ICON = {"retrieval": "search", "action": "zap", "skill": "puzzle", "other": "wrench"}

# Styling for the conv8 gap-analysis features (G1–G5).
_ARTIFACT_TYPE_STYLE = {
    "pptx": ("PowerPoint", "presentation", "amber"),
    "docx": ("Word", "file-text", "blue"),
    "xlsx": ("Excel", "table", "grass"),
    "csv": ("CSV", "table", "grass"),
    "pdf": ("PDF", "file-text", "red"),
    "png": ("Image", "image", "purple"),
    "jpg": ("Image", "image", "purple"),
    "txt": ("Text", "file", "gray"),
    "json": ("JSON", "braces", "cyan"),
}
_ARTIFACT_HOW_STYLE = {
    "python": ("Authored with code", "code", "purple"),
    "skill": ("Produced by a skill", "puzzle", "blue"),
    "unknown": ("Generated", "file-output", "gray"),
}
_SNIPPET_MODE_STYLE = {
    "stub": ("Download stubs (no text)", "file-down", "amber"),
    "content": ("Inline snippet text", "file-text", "grass"),
    "mixed": ("Mixed stubs + text", "files", "blue"),
    "unknown": ("Unknown", "circle-help", "gray"),
}
_SPAN_VISIBILITY_STYLE = {
    "document-level": ("Document-level", "file", "amber"),
    "span-level": ("Passage-level", "text-select", "grass"),
    "unknown": ("Unknown", "circle-help", "gray"),
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
    cross_turn: bool = False


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
class ToolFailureVM:
    turn_index: int = 0
    turn_label: str = ""
    name: str = ""
    params_summary: str = ""
    error_text: str = ""
    embedded: bool = False
    recovery_label: str = ""
    next_action: str = ""
    next_label: str = ""
    icon: str = "circle-x"
    color: str = "red"


@dataclass
class DuplicateGroupVM:
    name: str = ""
    params_summary: str = ""
    count: int = 0
    count_label: str = ""
    turns: str = ""
    turns_label: str = ""


@dataclass
class RepetitionVM:
    kind_label: str = ""
    turns: str = ""
    turns_label: str = ""
    similarity: str = ""
    excerpt: str = ""
    icon: str = "repeat"
    color: str = "amber"


@dataclass
class AnswerGroundingVM:
    turn_index: int = 0
    turn_label: str = ""
    factual_claims: int = 0
    cited_claims: int = 0
    had_retrieval: bool = False
    claims_label: str = ""
    retrieval_label: str = ""
    risk_label: str = ""
    excerpt: str = ""
    icon: str = "circle-check"
    color: str = "grass"


@dataclass
class QuoteCheckVM:
    turn_index: int = 0
    turn_label: str = ""
    excerpt: str = ""
    source_title: str = ""
    verdict_label: str = ""
    icon: str = "quote"
    color: str = "gray"


@dataclass
class CoverageGapVM:
    turn_index: int = 0
    turn_label: str = ""
    user_question: str = ""
    reason_label: str = ""
    query: str = ""
    icon: str = "search-x"
    color: str = "amber"


@dataclass
class SandboxSignalVM:
    turn_index: int = 0
    turn_label: str = ""
    category_label: str = ""
    tool: str = ""
    excerpt: str = ""
    icon: str = "terminal"
    color: str = "gray"


@dataclass
class SandboxFrictionVM:
    turn_index: int = 0
    turn_label: str = ""
    kind_label: str = ""
    excerpt: str = ""
    recovered: bool = False
    recovered_label: str = ""
    icon: str = "shield-alert"
    color: str = "red"


@dataclass
class SkillUseVM:
    name: str = ""
    category_label: str = ""
    turn_label: str = ""
    note: str = ""
    icon: str = "puzzle"
    color: str = "blue"


@dataclass
class FolderVM:
    path: str = ""
    area: str = ""
    count: int = 0
    count_label: str = ""
    doc_titles: list[str] = field(default_factory=list)


@dataclass
class DocRetrievalVM:
    title: str = ""
    reference_id: str = ""
    retrieval_count: int = 0
    count_label: str = ""
    turns_label: str = ""
    cited: bool = False
    cited_label: str = ""
    icon: str = "circle-check"
    color: str = "grass"


@dataclass
class SearchPrecisionVM:
    turn_label: str = ""
    query: str = ""
    retrieved: int = 0
    cited_from_search: int = 0
    precision_label: str = ""
    productive: bool = False
    icon: str = "circle-check"
    color: str = "grass"


@dataclass
class RecallTurnVM:
    turn_label: str = ""
    excerpt: str = ""


@dataclass
class GeneratedArtifactVM:
    name: str = ""
    turn_label: str = ""
    type_label: str = ""
    type_icon: str = "file-output"
    type_color: str = "gray"
    how_label: str = ""
    how_icon: str = "file-output"
    how_color: str = "gray"
    evidence: str = ""


@dataclass
class GroundingDocVM:
    title: str = ""
    reference_id: str = ""
    cited: bool = False
    chain_label: str = ""  # "Searched → downloaded → preprocessed → read"
    cited_label: str = ""
    icon: str = "file-search"
    color: str = "blue"


@dataclass
class SkillGapVM:
    turn_label: str = ""
    wanted_label: str = ""
    fallback_label: str = ""
    excerpt: str = ""
    icon: str = "puzzle"
    color: str = "amber"


@dataclass
class TimelineEventVM:
    kind: str = "answer"  # user / thought / tool / answer
    icon: str = "dot"
    color: str = "gray"
    label: str = ""
    text: str = ""
    failed: bool = False


@dataclass
class TimelineTurnVM:
    index: int = 0
    title: str = ""
    events: list[TimelineEventVM] = field(default_factory=list)


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
class ComponentNodeVM:
    """One node in the hierarchical component explorer. The tree is stored flat
    (parent before children) and rendered with indentation; `is_branch` marks
    groups/providers that can collapse and `indent` is the precomputed padding."""

    id: str = ""
    parent_id: str = ""
    depth: int = 0
    indent: str = "8px"
    node_type: str = "leaf"  # group | provider | leaf
    is_branch: bool = False  # group or provider (collapsible, has children)
    category: str = ""
    label: str = ""
    value: str = ""
    summary: str = ""
    doc: str = ""
    documented: bool = True
    icon: str = "settings"
    kind_badge: str = ""
    child_count: int = 0
    selectable: bool = True
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
    # credit estimate (#9 + modern E1–E4)
    credit_lines: list[CreditLineVM] = field(default_factory=list)
    credit_by_kind: list[CreditKindVM] = field(default_factory=list)
    credit_total: str = "0"
    credit_notes: list[str] = field(default_factory=list)
    has_credits: bool = False
    credit_reasoning_model: bool = False
    credit_total_tokens: int = 0
    credit_assumptions: list[str] = field(default_factory=list)
    credit_estimator_url: str = ""
    # code interpreter / sandbox (D1–D3)
    sandbox_used: bool = False
    sandbox_turns: int = 0
    sandbox_tools: list[str] = field(default_factory=list)
    sandbox_tools_label: str = ""
    sandbox_signals: list[SandboxSignalVM] = field(default_factory=list)
    sandbox_friction: list[SandboxFrictionVM] = field(default_factory=list)
    sandbox_friction_count: int = 0
    sandbox_skills: list[SkillUseVM] = field(default_factory=list)
    sandbox_doc_skills: int = 0
    # retrieval depth (B1–B4)
    rd_folders: list[FolderVM] = field(default_factory=list)
    rd_docs: list[DocRetrievalVM] = field(default_factory=list)
    rd_unique_docs: int = 0
    rd_total_retrieved: int = 0
    rd_overlap_docs: int = 0
    rd_cited_docs: int = 0
    rd_over_retrieval_label: str = "0%"
    rd_over_retrieval_pct: int = 0
    rd_mode: str = "inline"
    rd_full_reads: int = 0
    has_retrieval_depth: bool = False
    # search strategy (A1–A2)
    search_precision: list[SearchPrecisionVM] = field(default_factory=list)
    recall_turns: list[RecallTurnVM] = field(default_factory=list)
    ss_productive: int = 0
    ss_unproductive: int = 0
    has_search_strategy: bool = False
    # generated artifacts (G1)
    artifacts: list[GeneratedArtifactVM] = field(default_factory=list)
    artifact_count: int = 0
    artifact_types_label: str = ""
    has_artifacts: bool = False
    # code-interpreter purpose split (G2)
    sandbox_authoring_turns: list[int] = field(default_factory=list)
    sandbox_analysis_turns: list[int] = field(default_factory=list)
    sandbox_authoring_label: str = ""
    sandbox_analysis_label: str = ""
    # skill gaps (G3)
    skill_gaps: list[SkillGapVM] = field(default_factory=list)
    has_skill_gaps: bool = False
    # grounding pipeline (G4)
    grounding_docs: list[GroundingDocVM] = field(default_factory=list)
    gp_snippet_mode_label: str = ""
    gp_snippet_mode_icon: str = "circle-help"
    gp_snippet_mode_color: str = "gray"
    gp_span_label: str = ""
    gp_span_icon: str = "circle-help"
    gp_span_color: str = "gray"
    gp_stub_results: int = 0
    gp_content_results: int = 0
    gp_notes: list[str] = field(default_factory=list)
    has_grounding_pipeline: bool = False
    # component explorer (#8)
    components: list[ComponentVM] = field(default_factory=list)
    component_nodes: list[ComponentNodeVM] = field(default_factory=list)
    # failed-tool & recovery (#10)
    tool_failure_rows: list[ToolFailureVM] = field(default_factory=list)
    tf_total: int = 0
    tf_embedded: int = 0
    tf_recovered: int = 0
    tf_gaveup: int = 0
    # tool efficiency (#6)
    duplicate_groups: list[DuplicateGroupVM] = field(default_factory=list)
    eff_total_calls: int = 0
    eff_unique_calls: int = 0
    eff_redundant: int = 0
    eff_calls_per_answer: str = "0"
    # repetition / loops (#5)
    repetition: list[RepetitionVM] = field(default_factory=list)
    # per-answer groundedness (#2)
    answer_grounding: list[AnswerGroundingVM] = field(default_factory=list)
    ag_high: int = 0
    ag_medium: int = 0
    ag_low: int = 0
    # quote traceability (#11)
    quote_rows: list[QuoteCheckVM] = field(default_factory=list)
    qf_verified: int = 0
    qf_attributed: int = 0
    qf_dangling: int = 0
    qf_unattributed: int = 0
    # coverage gaps (#12)
    coverage_gaps: list[CoverageGapVM] = field(default_factory=list)
    # turn economy (#16)
    te_calls_per_answer: str = "0"
    te_searches_to_first: int = 0
    te_avg_bot_msgs: str = "0"
    te_user_turns: int = 0
    # timeline (#8 view)
    timeline: list[TimelineTurnVM] = field(default_factory=list)
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
                cross_turn=r.cross_turn,
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


def _build_sandbox(report: AnalysisReport) -> tuple[list[SandboxSignalVM], list[SandboxFrictionVM], list[SkillUseVM]]:
    ci = report.code_interpreter
    if ci is None:
        return [], [], []
    signals = [
        SandboxSignalVM(
            turn_index=s.turn_index,
            turn_label=f"Turn {s.turn_index}",
            category_label=_SANDBOX_CATEGORY_STYLE.get(s.category, (s.category, "terminal", "gray"))[0],
            tool=s.tool,
            excerpt=s.excerpt,
            icon=_SANDBOX_CATEGORY_STYLE.get(s.category, (s.category, "terminal", "gray"))[1],
            color=_SANDBOX_CATEGORY_STYLE.get(s.category, (s.category, "terminal", "gray"))[2],
        )
        for s in ci.signals
    ]
    friction = [
        SandboxFrictionVM(
            turn_index=f.turn_index,
            turn_label=f"Turn {f.turn_index}",
            kind_label=_FRICTION_STYLE.get(f.kind, (f.kind, "shield-alert", "red"))[0],
            excerpt=f.excerpt,
            recovered=f.recovered,
            recovered_label="Recovered" if f.recovered else "Unresolved",
            icon=_FRICTION_STYLE.get(f.kind, (f.kind, "shield-alert", "red"))[1],
            color="grass" if f.recovered else _FRICTION_STYLE.get(f.kind, (f.kind, "shield-alert", "red"))[2],
        )
        for f in ci.friction
    ]
    skills = [
        SkillUseVM(
            name=s.name,
            category_label="Document processing" if s.category == "document-processing" else "Skill",
            turn_label=f"Turn {s.turn_index}" if s.turn_index is not None else "",
            note=s.note,
            icon="file-text" if s.category == "document-processing" else "puzzle",
            color="cyan" if s.category == "document-processing" else "blue",
        )
        for s in ci.skills
    ]
    return signals, friction, skills


def _build_retrieval_depth(report: AnalysisReport) -> tuple[list[FolderVM], list[DocRetrievalVM]]:
    rd = report.retrieval_depth
    if rd is None:
        return [], []
    folders = [
        FolderVM(
            path=f.path,
            area=f.area,
            count=f.count,
            count_label=f"{f.count} doc" + ("s" if f.count != 1 else ""),
            doc_titles=list(f.doc_titles),
        )
        for f in rd.folders
    ]
    docs = [
        DocRetrievalVM(
            title=d.title,
            reference_id=d.reference_id or "",
            retrieval_count=d.retrieval_count,
            count_label=f"{d.retrieval_count}× retrieved",
            turns_label="Turn " + ", ".join(str(t) for t in d.turns),
            cited=d.cited,
            cited_label="Cited" if d.cited else "Not cited",
            icon="circle-check" if d.cited else "circle-minus",
            color="grass" if d.cited else "amber",
        )
        for d in rd.doc_retrievals
    ]
    return folders, docs


def _build_search_strategy(report: AnalysisReport) -> tuple[list[SearchPrecisionVM], list[RecallTurnVM]]:
    ss = report.search_strategy
    if ss is None:
        return [], []
    searches = [
        SearchPrecisionVM(
            turn_label=f"Turn {s.turn_index}",
            query=s.query,
            retrieved=s.retrieved,
            cited_from_search=s.cited_from_search,
            precision_label=f"{s.cited_from_search} of {s.retrieved} cited",
            productive=s.productive,
            icon="circle-check" if s.productive else "circle-x",
            color="grass" if s.productive else "red",
        )
        for s in ss.searches
    ]
    recall = [RecallTurnVM(turn_label=f"Turn {t.turn_index}", excerpt=t.excerpt) for t in ss.recall_turns]
    return searches, recall


def _build_artifacts(report: AnalysisReport) -> list[GeneratedArtifactVM]:
    ga = report.generated_artifacts
    if ga is None:
        return []
    out: list[GeneratedArtifactVM] = []
    for it in ga.items:
        tstyle = _ARTIFACT_TYPE_STYLE.get(it.file_type.lower(), (it.file_type.upper() or "File", "file-output", "gray"))
        hstyle = _ARTIFACT_HOW_STYLE.get(it.how_made, _ARTIFACT_HOW_STYLE["unknown"])
        out.append(
            GeneratedArtifactVM(
                name=it.name,
                turn_label=f"Turn {it.turn_index}" if it.turn_index is not None else "",
                type_label=tstyle[0],
                type_icon=tstyle[1],
                type_color=tstyle[2],
                how_label=hstyle[0],
                how_icon=hstyle[1],
                how_color=hstyle[2],
                evidence=it.evidence,
            )
        )
    return out


def _build_grounding(report: AnalysisReport) -> list[GroundingDocVM]:
    gp = report.grounding_pipeline
    if gp is None:
        return []
    out: list[GroundingDocVM] = []
    for d in gp.docs:
        steps = ["Searched"]
        if d.downloaded:
            steps.append("downloaded")
        if d.preprocessed:
            steps.append("preprocessed")
        if d.read_full:
            steps.append("read")
        out.append(
            GroundingDocVM(
                title=d.title or d.reference_id or "(untitled)",
                reference_id=d.reference_id or "",
                cited=d.cited,
                chain_label=" → ".join(steps),
                cited_label="Cited" if d.cited else "Not cited",
                icon="file-check" if d.cited else "file-search",
                color="grass" if d.cited else "gray",
            )
        )
    return out


def _build_skill_gaps(report: AnalysisReport) -> list[SkillGapVM]:
    ci = report.code_interpreter
    if ci is None:
        return []
    out: list[SkillGapVM] = []
    for g in ci.skill_gaps:
        out.append(
            SkillGapVM(
                turn_label=f"Turn {g.turn_index}",
                wanted_label=f"Wanted: {g.wanted}" if g.wanted else "No matching skill",
                fallback_label=f"Fell back to {g.fallback}" if g.fallback else "Fell back to raw code",
                excerpt=g.excerpt,
            )
        )
    return out


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


def _agent_settings_rows(p) -> list[tuple[str, str, str, str | None]]:
    """(label, value, kb_key, kb_value) for the agent group — shared shape."""
    rows: list[tuple[str, str, str, str | None]] = []
    if p.model_label or p.model_series:
        rows.append(("Model", p.model_label or p.model_series or "", "model", None))
    if p.is_modern:
        rows.append(("Orchestration", "Generative orchestration", "orchestration", None))
    if p.instructions:
        rows.append(("Instructions", f"{len(p.instruction_segments) or 1} segment(s)", "instructions", None))
    if p.authentication_mode:
        rows.append(("Authentication mode", p.authentication_mode, "authenticationMode", p.authentication_mode))
    if p.authentication_trigger:
        rows.append(("Authentication trigger", p.authentication_trigger, "authenticationTrigger", p.authentication_trigger))
    if p.access_control_policy:
        rows.append(("Access control", p.access_control_policy, "accessControlPolicy", p.access_control_policy))
    rows.append(("Memory", "Enabled" if p.enable_memory else "Disabled", "enableMemory", None))
    if p.conversation_starters:
        rows.append(("Conversation starters", f"{len(p.conversation_starters)} starter(s)", "conversationStarters", None))
    if p.recognizer_kind:
        rows.append(("Recognizer", p.recognizer_kind, "recognizer", None))
    if p.template:
        rows.append(("Template", p.template, "template", None))
    if p.runtime_provider:
        rows.append(("Runtime provider", p.runtime_provider, "runtimeProvider", None))
    return rows


def _branch(nodes: list, nid: str, key: str, child_count: int) -> None:
    label, icon, summary = _GROUP_META[key]
    nodes.append(
        ComponentNodeVM(
            id=nid, depth=0, indent="8px", node_type="group", is_branch=True, category=label,
            label=label, value=f"{child_count} item(s)", summary=summary, icon=icon,
            child_count=child_count, selectable=False, search_text=f"{label} {summary}".lower(),
        )
    )


def _build_component_nodes(report: AnalysisReport, convo: Conversation | None) -> list[ComponentNodeVM]:
    nodes: list[ComponentNodeVM] = []
    p = report.agent

    # ── Agent ──────────────────────────────────────────────────────────────
    if p is not None:
        settings = _agent_settings_rows(p)
        if settings:
            _branch(nodes, "g-agent", "agent", len(settings))
            for j, (label, value, key, kvalue) in enumerate(settings):
                ex = explain(key, kvalue)
                nodes.append(
                    ComponentNodeVM(
                        id=f"agent-{j}", parent_id="g-agent", depth=1, indent="26px", node_type="leaf",
                        category="Agent", label=label, value=value, summary=ex.summary, doc=ex.doc or "",
                        documented=ex.documented, icon="settings",
                        search_text=f"{label} {value} agent {ex.summary}".lower(),
                    )
                )

    # ── Knowledge sources ──────────────────────────────────────────────────
    if p is not None and p.knowledge_sources:
        _branch(nodes, "g-knowledge", "knowledge", len(p.knowledge_sources))
        for j, ks in enumerate(p.knowledge_sources):
            specific = f"knowledge.{ks.source_kind}" if ks.source_kind else "knowledge"
            ex = explain(specific)
            if not ex.documented:
                ex = explain("knowledge")
            nodes.append(
                ComponentNodeVM(
                    id=f"kb-{j}", parent_id="g-knowledge", depth=1, indent="26px", node_type="leaf",
                    category="Knowledge", label=ks.display_name or "(knowledge source)", value=ks.source_kind or "",
                    summary=ex.summary, doc=ex.doc or "", documented=ex.documented, icon="book-open",
                    search_text=f"{ks.display_name} {ks.source_kind} {ks.source_site} {ex.summary}".lower(),
                )
            )

    # ── Tools (Provider → Operation) ───────────────────────────────────────
    providers = build_tool_hierarchy(p, convo)
    if providers:
        _branch(nodes, "g-tools", "tools", len(providers))
        for pi, pr in enumerate(providers):
            badge, picon, kbkey = PROVIDER_META.get(pr.kind, ("Tool", "wrench", "tool"))
            pex = explain(kbkey)
            pid = f"prov-{pi}"
            origin = "Declared in agent" if pr.configured else "Observed at runtime"
            src = f" · {pr.source}" if pr.source else ""
            nodes.append(
                ComponentNodeVM(
                    id=pid, parent_id="g-tools", depth=1, indent="26px", node_type="provider", is_branch=True,
                    category="Tools", label=pr.display_name, value=f"{len(pr.operations)} operation(s) · {origin}{src}",
                    summary=pex.summary, doc=pex.doc or "", documented=pex.documented, icon=picon, kind_badge=badge,
                    child_count=len(pr.operations), selectable=True,
                    search_text=f"{pr.display_name} {badge} {pr.kind} {pex.summary}".lower(),
                )
            )
            for oi, op in enumerate(pr.operations):
                olabel = op.display_name or op.name
                oval = "Declared" if op.configured else "Observed at runtime"
                if op.description:
                    osum, odoc, odoc_ok = op.description, "", True
                else:
                    osum, odoc, odoc_ok = pex.summary, pex.doc or "", pex.documented
                nodes.append(
                    ComponentNodeVM(
                        id=f"op-{pi}-{oi}", parent_id=pid, depth=2, indent="44px", node_type="leaf",
                        category="Tools", label=olabel, value=oval, summary=osum, doc=odoc, documented=odoc_ok,
                        icon="dot", kind_badge=badge,
                        search_text=f"{olabel} {op.name} {badge} {pr.display_name} {osum}".lower(),
                    )
                )

    # ── Environment variables ──────────────────────────────────────────────
    if p is not None and p.environment_variables:
        _branch(nodes, "g-env", "env", len(p.environment_variables))
        for j, ev in enumerate(p.environment_variables):
            ex = explain("environmentVariable")
            label = ev.display_name or ev.schema_name or "(env var)"
            value = ev.type or (ev.default_value or "")
            nodes.append(
                ComponentNodeVM(
                    id=f"env-{j}", parent_id="g-env", depth=1, indent="26px", node_type="leaf",
                    category="Environment variables", label=label, value=value, summary=ex.summary,
                    doc=ex.doc or "", documented=ex.documented, icon="braces",
                    search_text=f"{label} {value} environment variable {ex.summary}".lower(),
                )
            )

    return nodes


# ---------------------------------------------------------------------------
# Top-level mapping
# ---------------------------------------------------------------------------


def _build_tool_failures(report: AnalysisReport) -> list[ToolFailureVM]:
    tf = report.tool_failures
    if tf is None:
        return []
    rows: list[ToolFailureVM] = []
    for f in tf.failures:
        label, icon, color = _RECOVERY_STYLE.get(f.recovery, ("", "circle-x", "red"))
        rows.append(
            ToolFailureVM(
                turn_index=f.turn_index,
                turn_label=f"Turn {f.turn_index}",
                name=f.name,
                params_summary=f.params_summary,
                error_text=f.error_text,
                embedded=f.embedded,
                recovery_label=label,
                next_action=f.next_action or "",
                next_label=f"→ {f.next_action}" if f.next_action else "",
                icon=icon,
                color=color,
            )
        )
    return rows


def _build_repetition(report: AnalysisReport) -> list[RepetitionVM]:
    rep = report.repetition
    if rep is None:
        return []
    out: list[RepetitionVM] = []
    for s in rep.signals:
        label, icon, color = _REPETITION_STYLE.get(s.kind, (s.kind, "repeat", "amber"))
        out.append(
            RepetitionVM(
                kind_label=label,
                turns=", ".join(str(t) for t in s.turns),
                turns_label="Turns " + ", ".join(str(t) for t in s.turns),
                similarity=f"{int(round(s.similarity * 100))}%",
                excerpt=s.excerpt,
                icon=icon,
                color=color,
            )
        )
    return out


def _build_answer_grounding(report: AnalysisReport) -> list[AnswerGroundingVM]:
    ag = report.answer_groundedness
    if ag is None:
        return []
    rank = {"high": 0, "medium": 1, "low": 2}
    out: list[AnswerGroundingVM] = []
    for a in sorted(ag.answers, key=lambda a: rank.get(a.risk, 3)):
        label, icon, color = _RISK_STYLE.get(a.risk, ("", "circle", "gray"))
        out.append(
            AnswerGroundingVM(
                turn_index=a.turn_index,
                turn_label=f"Turn {a.turn_index}",
                factual_claims=a.factual_claims,
                cited_claims=a.cited_claims,
                had_retrieval=a.had_retrieval,
                claims_label=f"{a.cited_claims}/{a.factual_claims} claims cited",
                retrieval_label="search returned docs" if a.had_retrieval else "no retrieval",
                risk_label=label,
                excerpt=a.excerpt,
                icon=icon,
                color=color,
            )
        )
    return out


def _build_quotes(report: AnalysisReport) -> list[QuoteCheckVM]:
    qf = report.quote_faithfulness
    if qf is None:
        return []
    rank = {"unattributed-quote": 0, "dangling-attribution": 1, "attributed-source-in-sandbox": 2, "verified-in-tool-output": 3}
    out: list[QuoteCheckVM] = []
    for q in sorted(qf.quotes, key=lambda q: rank.get(q.verdict, 4)):
        label, icon, color = _VERDICT_STYLE.get(q.verdict, (q.verdict, "quote", "gray"))
        out.append(
            QuoteCheckVM(
                turn_index=q.turn_index,
                turn_label=f"Turn {q.turn_index}",
                excerpt=q.excerpt,
                source_title=q.source_title or "",
                verdict_label=label,
                icon=icon,
                color=color,
            )
        )
    return out


def _build_coverage_gaps(report: AnalysisReport) -> list[CoverageGapVM]:
    cg = report.coverage_gaps
    if cg is None:
        return []
    out: list[CoverageGapVM] = []
    for g in cg.gaps:
        label, icon, color = _COVERAGE_STYLE.get(g.reason, (g.reason, "search-x", "amber"))
        out.append(
            CoverageGapVM(
                turn_index=g.turn_index,
                turn_label=f"Turn {g.turn_index}",
                user_question=g.user_question,
                reason_label=label,
                query=g.query,
                icon=icon,
                color=color,
            )
        )
    return out


def _build_timeline(convo: Conversation | None) -> list[TimelineTurnVM]:
    if convo is None:
        return []
    turns: list[TimelineTurnVM] = []
    for turn in convo.turns:
        events: list[TimelineEventVM] = []
        if turn.user_message is not None and turn.user_message.text.strip():
            events.append(
                TimelineEventVM(kind="user", icon="user", color="grass", label="User", text=_clip(turn.user_message.text, 240))
            )
        for m in turn.bot_messages:
            for th in m.thoughts:
                if th.text.strip():
                    events.append(
                        TimelineEventVM(kind="thought", icon="brain", color="purple", label="Thought", text=_clip(th.text, 200))
                    )
            for tc in m.tool_calls:
                kind = classify_tool(tc)
                failed = tool_failed(tc)
                detail = tc.query or _params_preview(tc)
                events.append(
                    TimelineEventVM(
                        kind="tool",
                        icon="circle-x" if failed else _TIMELINE_TOOL_ICON.get(kind, "wrench"),
                        color="red" if failed else "blue",
                        label=tc.display_name or tc.name or "tool",
                        text=detail,
                        failed=failed,
                    )
                )
            if m.text.strip():
                events.append(
                    TimelineEventVM(kind="answer", icon="bot", color="blue", label="Agent", text=_clip(m.text, 280))
                )
        title = "Greeting" if turn.user_message is None else f"Turn {turn.index}"
        turns.append(TimelineTurnVM(index=turn.index, title=title, events=events))
    return turns


def _params_preview(tc: ToolCall, limit: int = 100) -> str:
    if not isinstance(tc.params, dict) or not tc.params:
        return ""
    parts = []
    for k, v in tc.params.items():
        vs = " ".join(str(v).split())
        vs = vs if len(vs) <= 40 else vs[:39] + "…"
        parts.append(f"{k}={vs}")
    s = " ".join(parts)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
    if report.credit_estimate is not None:
        ce = report.credit_estimate
        vm.credit_reasoning_model = ce.reasoning_model
        vm.credit_total_tokens = ce.total_tokens
        vm.credit_assumptions = list(ce.assumptions)
        vm.credit_estimator_url = CREDIT_ESTIMATOR_URL

    if report.code_interpreter is not None:
        ci = report.code_interpreter
        vm.sandbox_signals, vm.sandbox_friction, vm.sandbox_skills = _build_sandbox(report)
        vm.sandbox_used = ci.used
        vm.sandbox_turns = ci.turns_with_code
        vm.sandbox_tools = list(ci.distinct_tools)
        vm.sandbox_tools_label = ", ".join(ci.distinct_tools) if ci.distinct_tools else "—"
        vm.sandbox_friction_count = ci.friction_count
        vm.sandbox_doc_skills = ci.document_processing_skills
        vm.sandbox_authoring_turns = list(ci.authoring_turns)
        vm.sandbox_analysis_turns = list(ci.analysis_turns)
        vm.sandbox_authoring_label = (
            "Turn " + ", ".join(str(t) for t in ci.authoring_turns) if ci.authoring_turns else "—"
        )
        vm.sandbox_analysis_label = (
            "Turn " + ", ".join(str(t) for t in ci.analysis_turns) if ci.analysis_turns else "—"
        )
        vm.skill_gaps = _build_skill_gaps(report)
        vm.has_skill_gaps = bool(vm.skill_gaps)

    if report.retrieval_depth is not None:
        rd = report.retrieval_depth
        vm.rd_folders, vm.rd_docs = _build_retrieval_depth(report)
        vm.rd_unique_docs = rd.unique_docs
        vm.rd_total_retrieved = rd.total_retrieved
        vm.rd_overlap_docs = rd.overlap_docs
        vm.rd_cited_docs = rd.cited_docs
        vm.rd_over_retrieval_label = f"{int(rd.over_retrieval_ratio * 100)}%"
        vm.rd_over_retrieval_pct = int(rd.over_retrieval_ratio * 100)
        vm.rd_mode = rd.retrieval_mode
        vm.rd_full_reads = rd.full_doc_reads
        vm.has_retrieval_depth = bool(rd.folders or rd.doc_retrievals)

    if report.search_strategy is not None:
        ss = report.search_strategy
        vm.search_precision, vm.recall_turns = _build_search_strategy(report)
        vm.ss_productive = ss.productive_searches
        vm.ss_unproductive = ss.unproductive_searches
        vm.has_search_strategy = bool(ss.searches or ss.recall_turns)

    if report.generated_artifacts is not None:
        ga = report.generated_artifacts
        vm.artifacts = _build_artifacts(report)
        vm.artifact_count = ga.count
        vm.artifact_types_label = ", ".join(f"{v}× {k}" for k, v in ga.by_type.items()) if ga.by_type else ""
        vm.has_artifacts = bool(ga.items)

    if report.grounding_pipeline is not None:
        gp = report.grounding_pipeline
        vm.grounding_docs = _build_grounding(report)
        sm = _SNIPPET_MODE_STYLE.get(gp.snippet_mode, _SNIPPET_MODE_STYLE["unknown"])
        vm.gp_snippet_mode_label, vm.gp_snippet_mode_icon, vm.gp_snippet_mode_color = sm
        sp = _SPAN_VISIBILITY_STYLE.get(gp.span_visibility, _SPAN_VISIBILITY_STYLE["unknown"])
        vm.gp_span_label, vm.gp_span_icon, vm.gp_span_color = sp
        vm.gp_stub_results = gp.stub_results
        vm.gp_content_results = gp.content_results
        vm.gp_notes = list(gp.notes)
        vm.has_grounding_pipeline = bool(gp.docs)

    vm.components = _build_components(report, convo)
    vm.component_nodes = _build_component_nodes(report, convo)

    if report.tool_failures is not None:
        tf = report.tool_failures
        vm.tool_failure_rows = _build_tool_failures(report)
        vm.tf_total, vm.tf_embedded = tf.total_failures, tf.embedded_failures
        vm.tf_recovered, vm.tf_gaveup = tf.recovered, tf.gave_up

    if report.tool_efficiency is not None:
        te = report.tool_efficiency
        vm.duplicate_groups = [
            DuplicateGroupVM(
                name=d.name,
                params_summary=d.params_summary,
                count=d.count,
                count_label=f"{d.count}× identical",
                turns=", ".join(str(t) for t in d.turns),
                turns_label="Turns " + ", ".join(str(t) for t in d.turns),
            )
            for d in te.duplicate_groups
        ]
        vm.eff_total_calls, vm.eff_unique_calls = te.total_calls, te.unique_calls
        vm.eff_redundant = te.redundant_calls
        vm.eff_calls_per_answer = f"{te.calls_per_answer:g}"

    vm.repetition = _build_repetition(report)

    if report.answer_groundedness is not None:
        ag = report.answer_groundedness
        vm.answer_grounding = _build_answer_grounding(report)
        vm.ag_high, vm.ag_medium, vm.ag_low = ag.high_risk, ag.medium_risk, ag.low_risk

    if report.quote_faithfulness is not None:
        qf = report.quote_faithfulness
        vm.quote_rows = _build_quotes(report)
        vm.qf_verified, vm.qf_attributed = qf.verified, qf.attributed
        vm.qf_dangling, vm.qf_unattributed = qf.dangling, qf.unattributed

    vm.coverage_gaps = _build_coverage_gaps(report)

    if report.turn_economy is not None:
        eco = report.turn_economy
        vm.te_calls_per_answer = f"{eco.calls_per_answer:g}"
        vm.te_searches_to_first = eco.searches_to_first_answer
        vm.te_avg_bot_msgs = f"{eco.avg_bot_msgs_per_turn:g}"
        vm.te_user_turns = eco.user_turns

    vm.timeline = _build_timeline(convo)

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
