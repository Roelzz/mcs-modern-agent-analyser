"""Reflex state: upload, parse, analyse, and expose structured view-models."""

import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta

import httpx
import reflex as rx
from loguru import logger

from agent_parser import parse_agent_yaml_text
from analysis import analyze
from dataverse_client import (
    DEFAULT_CLIENT_ID,
    DataverseClient,
    acquire_device_flow_token,
    initiate_device_flow,
    validate_auth_config,
)
from models import AgentProfile, Conversation
from renderer import build_standalone_html, render_markdown
from transcript_parser import TRANSCRIPT_ENVELOPE_KEYS, activity_role, parse_transcript_text
from web.view_models import (
    AnswerGroundingVM,
    ChatBlockVM,
    CheckVM,
    CitationRowVM,
    ComponentNodeVM,
    ComponentVM,
    CoverageGapVM,
    CreditKindVM,
    CreditLineVM,
    DocRetrievalVM,
    DocVM,
    DuplicateGroupVM,
    EnvVarVM,
    FindingVM,
    FolderVM,
    GeneratedArtifactVM,
    GroundingDocVM,
    KnowledgeTurnVM,
    KSourceVM,
    QuoteCheckVM,
    RecallTurnVM,
    RepetitionVM,
    SandboxFrictionVM,
    SandboxSignalVM,
    SearchPrecisionVM,
    SkillGapVM,
    SkillUseVM,
    SourceEffVM,
    TimelineTurnVM,
    ToolCallVM,
    ToolFailureVM,
    ToolRowVM,
    ToolTurnVM,
    TurnNavVM,
    TurnVM,
    map_report,
)

# Tab key -> label
TAB_DEFS: list[tuple[str, str]] = [
    ("overview", "Overview"),
    ("agent", "Agent"),
    ("conversation", "Conversation"),
    ("tools", "Tools & Actions"),
    ("knowledge", "Knowledge"),
    ("reasoning", "Reasoning"),
    ("quality", "Quality"),
    ("timeline", "Timeline"),
    ("components", "Components"),
]

_FILTERS = ("all", "critical", "warning", "info")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# --- Community usage counter (shared komarev badge, same as the classic analyser) ---
# Keyed on username=Roelzz, so this is the SAME counter both READMEs and both apps use;
# every fetch/render bumps the one shared total. 30s cache throttles rapid refetches.
_KOMAREV_URL = "https://komarev.com/ghpvc/?username=Roelzz&label=Repo%20Views&color=0e75b6&style=flat"

_community_count_cache: dict[str, float | int] = {"count": 0, "fetched_at": 0.0}


def _fetch_community_count() -> int:
    """Fetch the shared view count from the komarev badge SVG (cached 30s)."""
    now = time.time()
    if now - _community_count_cache["fetched_at"] < 30:
        return int(_community_count_cache["count"])
    try:
        resp = httpx.get(_KOMAREV_URL, headers={"User-Agent": "AgentAnalyserModern/1.0"}, timeout=5)
        numbers = re.findall(r">([\d,]+)</", resp.text)
        if numbers:
            count = int(numbers[-1].replace(",", ""))
            _community_count_cache["count"] = count
            _community_count_cache["fetched_at"] = now
            return count
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to fetch community count: {exc}")
    return int(_community_count_cache["count"])


_CAT_MILESTONES: list[tuple[int, str, str]] = [
    (1000, "\U0001f406", "Legendary Leopard"),
    (500, "\U0001f42f", "Tiger Analyst"),
    (250, "\U0001f981", "Lion Mode"),
    (100, "\U0001f408\u200d\u2b1b", "Shadow Cat"),
    (50, "\U0001f408", "Prowling Cat"),
    (25, "\U0001f638", "Grinning Cat"),
    (10, "\U0001f63a", "Happy Cat"),
    (0, "\U0001f431", "Curious Kitten"),
]

_MILESTONE_THRESHOLDS: set[int] = {t for t, _, _ in _CAT_MILESTONES if t > 0}


def _cat_emoji_for(count: int) -> str:
    for threshold, emoji, _ in _CAT_MILESTONES:
        if count >= threshold:
            return emoji
    return "\U0001f431"


def _cat_title_for(count: int) -> str:
    for threshold, _, title in _CAT_MILESTONES:
        if count >= threshold:
            return title
    return "Curious Kitten"


def _transcript_activities(content: object) -> list[dict]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return []
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    if isinstance(content, dict):
        activities = content.get("activities")
        if isinstance(activities, list):
            return [item for item in activities if isinstance(item, dict)]
    return []


def _summarise_dataverse_record(record: dict) -> tuple[dict, str]:
    transcript_id = str(record.get("conversationtranscriptid") or "")
    created_on = str(record.get("createdon") or "")
    content = record.get("content") or ""
    activities = _transcript_activities(content)
    preview = ""
    for activity in activities:
        activity_type = str(activity.get("type") or "").lower()
        if activity_type and activity_type != "message":
            continue
        if activity_role(activity) != "user":
            continue
        text = str(activity.get("text") or "").strip()
        if text:
            preview = text[:120] + ("..." if len(text) > 120 else "")
            break
    summary = {
        "id": transcript_id,
        "short_id": f"{transcript_id[:8]}..." if len(transcript_id) > 8 else transcript_id,
        "created_on": created_on[:10] if len(created_on) >= 10 else created_on,
        "preview": preview or "(no user message found)",
        "activity_count": len(activities),
        "activity_label": f"{len(activities)} activities",
    }
    content_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return summary, content_text


def _summarise_dataverse_metadata(record: dict) -> dict:
    transcript_id = str(record.get("conversationtranscriptid") or "")
    created_on = str(record.get("createdon") or "")
    return {
        "id": transcript_id,
        "short_id": f"{transcript_id[:8]}..." if len(transcript_id) > 8 else transcript_id,
        "created_on": created_on[:10] if len(created_on) >= 10 else created_on,
        "preview": str(record.get("name") or "Select Analyse to load this transcript"),
        "activity_count": 0,
        "activity_label": "On demand",
    }


async def _fetch_dataverse_records(
    org_url: str,
    access_token: str,
    bot_identifier: str,
    since_date: str,
    top_n: int,
) -> tuple[list[dict], int]:
    if not bot_identifier.strip():
        raise ValueError("Enter the Copilot ID from Copilot Studio session details.")
    try:
        since = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("Since date must use YYYY-MM-DD.") from exc

    async with DataverseClient(org_url, access_token) as client:
        records = await client.fetch_transcripts(bot_identifier.strip(), since, top_n)

    summaries = [_summarise_dataverse_metadata(record) for record in records]
    return [summary for summary in summaries if summary["id"]], 0


async def _fetch_dataverse_transcript(
    org_url: str,
    access_token: str,
    conversation_id: str,
) -> tuple[dict, str]:
    async with DataverseClient(org_url, access_token) as client:
        record = await client.fetch_transcript_by_id(conversation_id)
    summary, content = _summarise_dataverse_record(record)
    if not content:
        raise RuntimeError("Transcript found, but its content is empty.")
    return summary, content


def _dataverse_empty_message(empty_count: int, since_date: str) -> str:
    if empty_count:
        return f"Dataverse returned {empty_count} transcript record(s), but none contained transcript content."
    return (
        f"No transcripts found since {since_date}. "
        "Transcripts can take about 30 minutes to appear after a conversation ends."
    )


class State(rx.State):
    # Raw uploaded payloads
    _transcript_text: str = ""
    agent_text: str = ""
    transcript_name: str = ""
    agent_name: str = ""

    # JSON pasted straight from the Dataverse conversationtranscript row
    paste_text: str = ""

    # Dataverse transcript import
    dv_org_url: str = rx.LocalStorage("", name="agent-analyser-dv-org-url")
    dv_tenant_id: str = rx.LocalStorage("", name="agent-analyser-dv-tenant-id")
    dv_client_id: str = rx.LocalStorage(
        DEFAULT_CLIENT_ID,
        name="agent-analyser-dv-client-id",
    )
    dv_bot_identifier: str = rx.LocalStorage("", name="agent-analyser-dv-bot-id")
    dv_since_date: str = ""
    dv_top_n: int = 50
    dv_session_details_paste: str = ""
    dv_autofill_error: str = ""
    dv_device_code: str = ""
    dv_device_code_url: str = ""
    dv_is_authenticating: bool = False
    dv_auth_error: str = ""
    dv_is_connected: bool = False
    dv_is_fetching: bool = False
    dv_fetch_error: str = ""
    dv_transcripts: list[dict] = []
    dv_conversation_id: str = ""
    dv_single_fetching: bool = False
    dv_single_fetch_error: str = ""
    _dv_token: str = ""
    _dv_auth_attempt: int = 0
    _dv_analysis_attempt: int = 0

    # Status
    error: str = ""
    status: str = ""
    has_report: bool = False
    active_tab: str = "overview"

    # Community usage counter (shared komarev counter, see _fetch_community_count)
    analyses_count: int = 0
    counter_animating: bool = False
    milestone_reached: bool = False

    # Export payload
    full_md: str = ""

    # --- Structured report (from ReportVM) ---
    agent_present: bool = False
    convo_present: bool = False
    agent_title: str = ""
    model_label: str = ""
    template: str = ""
    recognizer: str = ""
    auth: str = ""
    memory: bool = False
    instructions: str = ""
    created_at: str = ""
    modified_at: str = ""
    conversation_starters: list[str] = []
    knowledge_sources: list[KSourceVM] = []
    env_vars: list[EnvVarVM] = []

    # Overview metrics
    m_turns: int = 0
    m_user: int = 0
    m_bot: int = 0
    m_tools: int = 0
    m_searches: int = 0
    m_thoughts: int = 0
    m_failed: int = 0
    m_zero: int = 0

    # Findings
    findings: list[FindingVM] = []
    f_critical: int = 0
    f_warning: int = 0
    f_info: int = 0

    # Tools / actions
    tool_rows: list[ToolRowVM] = []
    # flat, ordered list of EVERY tool call (success + fail) — drives the generic
    # "All tool calls" drill-down in the Tools & actions tab.
    _tool_calls_all: list[ToolCallVM] = []
    _tool_turns: list[ToolTurnVM] = []
    active_tool_turn: str = ""
    tool_turn_query: str = ""
    skill_loads: list[str] = []
    retry_signals: list[str] = []
    tool_failures: list[str] = []

    # Knowledge
    _knowledge_turns: list[KnowledgeTurnVM] = []
    active_knowledge_turn: str = ""
    knowledge_turn_query: str = ""
    uncited_docs: list[DocVM] = []
    sources_seen: list[str] = []
    zero_result_queries: list[str] = []

    # Citations
    citation_markers: int = 0
    uncited_answer_count: int = 0

    # Citation audit (#4)
    citation_rows: list[CitationRowVM] = []
    cit_resolved: int = 0
    cit_dangling: int = 0
    cit_uncited: int = 0

    # Knowledge effectiveness (#3)
    source_effectiveness: list[SourceEffVM] = []
    eff_total_searches: int = 0
    eff_distinct_docs: int = 0
    eff_avg_docs: str = "0"
    eff_unattributed: int = 0

    # Credit estimate (#9)
    credit_lines: list[CreditLineVM] = []
    credit_by_kind: list[CreditKindVM] = []
    credit_total: str = "0"
    credit_notes: list[str] = []
    has_credits: bool = False
    credit_reasoning_model: bool = False
    credit_total_tokens: int = 0
    credit_assumptions: list[str] = []
    credit_estimator_url: str = ""

    # Code interpreter / sandbox (D1–D3)
    sandbox_used: bool = False
    sandbox_turns: int = 0
    sandbox_tools_label: str = "—"
    sandbox_signals: list[SandboxSignalVM] = []
    sandbox_friction: list[SandboxFrictionVM] = []
    sandbox_friction_count: int = 0
    sandbox_skills: list[SkillUseVM] = []
    sandbox_doc_skills: int = 0

    # Retrieval depth (B1–B4)
    rd_folders: list[FolderVM] = []
    rd_docs: list[DocRetrievalVM] = []
    rd_unique_docs: int = 0
    rd_total_retrieved: int = 0
    rd_overlap_docs: int = 0
    rd_cited_docs: int = 0
    rd_over_retrieval_label: str = "0%"
    rd_over_retrieval_pct: int = 0
    rd_mode: str = "inline"
    rd_full_reads: int = 0
    has_retrieval_depth: bool = False

    # Search strategy (A1–A2)
    search_precision: list[SearchPrecisionVM] = []
    recall_turns: list[RecallTurnVM] = []
    ss_productive: int = 0
    ss_unproductive: int = 0
    has_search_strategy: bool = False

    # Generated artifacts (G1)
    artifacts: list[GeneratedArtifactVM] = []
    artifact_count: int = 0
    artifact_types_label: str = ""
    has_artifacts: bool = False

    # Code-interpreter purpose split (G2)
    sandbox_authoring_label: str = "—"
    sandbox_analysis_label: str = "—"
    sandbox_authoring_count: int = 0
    sandbox_analysis_count: int = 0

    # Skill gaps (G3)
    skill_gaps: list[SkillGapVM] = []
    has_skill_gaps: bool = False

    # Grounding pipeline (G4)
    grounding_docs: list[GroundingDocVM] = []
    gp_snippet_mode_label: str = ""
    gp_snippet_mode_icon: str = "circle-help"
    gp_snippet_mode_color: str = "gray"
    gp_span_label: str = ""
    gp_span_icon: str = "circle-help"
    gp_span_color: str = "gray"
    gp_stub_results: int = 0
    gp_content_results: int = 0
    gp_notes: list[str] = []
    has_grounding_pipeline: bool = False

    # Component explorer (#8) — hierarchical
    components: list[ComponentVM] = []
    component_nodes: list[ComponentNodeVM] = []
    collapsed_nodes: list[str] = []

    # Failed-tool & recovery (#10)
    tool_failure_rows: list[ToolFailureVM] = []
    tf_total: int = 0
    tf_embedded: int = 0
    tf_recovered: int = 0
    tf_gaveup: int = 0

    # Tool efficiency (#6)
    duplicate_groups: list[DuplicateGroupVM] = []
    eff_total_calls: int = 0
    eff_unique_calls: int = 0
    eff_redundant: int = 0
    eff_calls_per_answer: str = "0"

    # Repetition / loops (#5)
    repetition: list[RepetitionVM] = []

    # Per-answer groundedness (#2)
    answer_grounding: list[AnswerGroundingVM] = []
    ag_high: int = 0
    ag_medium: int = 0
    ag_low: int = 0

    # Quote traceability (#11)
    quote_rows: list[QuoteCheckVM] = []
    qf_verified: int = 0
    qf_attributed: int = 0
    qf_dangling: int = 0
    qf_unattributed: int = 0

    # Coverage gaps (#12)
    coverage_gaps: list[CoverageGapVM] = []

    # Turn economy (#16)
    te_calls_per_answer: str = "0"
    te_searches_to_first: int = 0
    te_avg_bot_msgs: str = "0"
    te_user_turns: int = 0

    # Timeline (#8 view)
    timeline: list[TimelineTurnVM] = []

    # Reasoning
    premise_corrections: list[str] = []
    thoughts_per_turn: list[int] = []

    # Groundedness
    grounded: int = 0
    ungrounded: int = 0
    hallucination_risk: list[str] = []
    honest_grounding: list[str] = []
    groundedness_notes: list[str] = []

    # Instruction compliance
    checks: list[CheckVM] = []

    # Cross reference
    unused_knowledge_sources: list[str] = []
    contributing_knowledge_sources: list[str] = []
    tools_used_not_defined: list[str] = []

    # Conversation views
    chat: list[ChatBlockVM] = []
    turns: list[TurnVM] = []
    mermaid: str = ""

    # --- Interactive UI state ---
    finding_filter: str = "all"
    transcript_query: str = ""
    active_citation: str = ""
    show_thoughts: bool = True
    # Tool-call transparency drop-downs (two-level disclosure).
    # tool_open holds expanded call_ids (Level 1); tool_sections_open holds
    # expanded "call_id::section" keys (Level 2). Seeded on load so failed calls
    # open their "What happened" + "Diagnosis" panels by default.
    tool_open: list[str] = []
    tool_sections_open: list[str] = []
    component_query: str = ""
    active_component: str = ""

    # ------------------------------------------------------------------
    # Derived upload state
    # ------------------------------------------------------------------
    @rx.var
    def has_transcript(self) -> bool:
        return bool(self._transcript_text)

    @rx.var
    def has_agent(self) -> bool:
        return bool(self.agent_text)

    @rx.var
    def can_analyse(self) -> bool:
        return bool(self._transcript_text or self.agent_text)

    @rx.var
    def dv_show_device_code(self) -> bool:
        return bool(self.dv_device_code) and self.dv_is_authenticating

    @rx.var
    def dv_has_transcripts(self) -> bool:
        return bool(self.dv_transcripts)

    @rx.var
    def findings_total(self) -> int:
        return len(self.findings)

    @rx.var
    def filtered_findings(self) -> list[FindingVM]:
        if self.finding_filter == "all":
            return self.findings
        return [f for f in self.findings if f.severity == self.finding_filter]

    @rx.var
    def filtered_chat(self) -> list[ChatBlockVM]:
        q = self.transcript_query.strip().lower()
        if not q:
            return self.chat
        return [b for b in self.chat if q in b.search_text]

    @rx.var
    def chat_hits(self) -> int:
        return len(self.filtered_chat)

    def _filtered_knowledge_turn_items(self) -> list[KnowledgeTurnVM]:
        q = self.knowledge_turn_query.strip().lower()
        if not q:
            return self._knowledge_turns
        return [
            turn
            for turn in self._knowledge_turns
            if q in turn.question.lower()
            or q in turn.turn_label.lower()
            or any(q in query.query.lower() or q in query.tool_name.lower() for query in turn.queries)
        ]

    @rx.var
    def filtered_knowledge_turns(self) -> list[TurnNavVM]:
        return [
            TurnNavVM(
                id=turn.id,
                turn_label=turn.turn_label,
                question_excerpt=turn.question_excerpt,
                summary_label=turn.summary_label,
            )
            for turn in self._filtered_knowledge_turn_items()
        ]

    @rx.var
    def has_knowledge_turns(self) -> bool:
        return bool(self._knowledge_turns)

    @rx.var
    def selected_knowledge_turn(self) -> KnowledgeTurnVM:
        turns = self._filtered_knowledge_turn_items()
        if not turns:
            return KnowledgeTurnVM()
        return next(
            (turn for turn in turns if turn.id == self.active_knowledge_turn),
            turns[0],
        )

    @rx.var
    def knowledge_turn_position(self) -> str:
        turns = self._filtered_knowledge_turn_items()
        if not turns:
            return ""
        index = next(
            (i for i, turn in enumerate(turns) if turn.id == self.active_knowledge_turn),
            0,
        )
        return f"{index + 1} of {len(turns)}"

    @rx.var
    def has_previous_knowledge_turn(self) -> bool:
        turns = self._filtered_knowledge_turn_items()
        if not turns:
            return False
        current_id = self.active_knowledge_turn or turns[0].id
        return current_id != turns[0].id

    @rx.var
    def has_next_knowledge_turn(self) -> bool:
        turns = self._filtered_knowledge_turn_items()
        if not turns:
            return False
        current_id = self.active_knowledge_turn or turns[0].id
        return current_id != turns[-1].id

    def _filtered_tool_turn_items(self) -> list[ToolTurnVM]:
        q = self.tool_turn_query.strip().lower()
        if not q:
            return self._tool_turns
        return [
            turn
            for turn in self._tool_turns
            if q in turn.question.lower()
            or q in turn.turn_label.lower()
            or any(
                q in call.name.lower()
                or q in call.display_name.lower()
                or q in call.query.lower()
                or q in call.status.lower()
                for call in turn.tool_calls
            )
        ]

    @rx.var
    def filtered_tool_turns(self) -> list[TurnNavVM]:
        return [
            TurnNavVM(
                id=turn.id,
                turn_label=turn.turn_label,
                question_excerpt=turn.question_excerpt,
                summary_label=turn.summary_label,
            )
            for turn in self._filtered_tool_turn_items()
        ]

    @rx.var
    def has_tool_turns(self) -> bool:
        return bool(self._tool_turns)

    @rx.var
    def selected_tool_turn(self) -> ToolTurnVM:
        turns = self._filtered_tool_turn_items()
        if not turns:
            return ToolTurnVM()
        return next(
            (turn for turn in turns if turn.id == self.active_tool_turn),
            turns[0],
        )

    @rx.var
    def tool_turn_position(self) -> str:
        turns = self._filtered_tool_turn_items()
        if not turns:
            return ""
        index = next(
            (i for i, turn in enumerate(turns) if turn.id == self.active_tool_turn),
            0,
        )
        return f"{index + 1} of {len(turns)}"

    @rx.var
    def has_previous_tool_turn(self) -> bool:
        turns = self._filtered_tool_turn_items()
        if not turns:
            return False
        current_id = self.active_tool_turn or turns[0].id
        return current_id != turns[0].id

    @rx.var
    def has_next_tool_turn(self) -> bool:
        turns = self._filtered_tool_turn_items()
        if not turns:
            return False
        current_id = self.active_tool_turn or turns[0].id
        return current_id != turns[-1].id

    @rx.var
    def grounded_total(self) -> int:
        return self.grounded + self.ungrounded

    @rx.var
    def visible_nodes(self) -> list[ComponentNodeVM]:
        """Tree nodes to render: search-filtered (keeping ancestors) when a query
        is present, else hiding nodes beneath a collapsed branch."""
        nodes = self.component_nodes
        by_id = {n.id: n for n in nodes}
        q = self.component_query.strip().lower()
        if q:
            keep: set[str] = set()
            for n in nodes:
                if q in n.search_text:
                    keep.add(n.id)
                    pid = n.parent_id
                    while pid and pid in by_id:
                        keep.add(pid)
                        pid = by_id[pid].parent_id
            return [n for n in nodes if n.id in keep]
        collapsed = set(self.collapsed_nodes)
        out: list[ComponentNodeVM] = []
        for n in nodes:
            hidden = False
            pid = n.parent_id
            while pid and pid in by_id:
                if pid in collapsed:
                    hidden = True
                    break
                pid = by_id[pid].parent_id
            if not hidden:
                out.append(n)
        return out

    @rx.var
    def component_count(self) -> int:
        return len([n for n in self.component_nodes if n.node_type != "group"])

    @rx.var
    def selected_component(self) -> ComponentNodeVM:
        selectable = [n for n in self.component_nodes if n.selectable]
        if not selectable:
            return ComponentNodeVM()
        for n in selectable:
            if n.id == self.active_component:
                return n
        return selectable[0]

    @rx.var
    def cat_emoji(self) -> str:
        return _cat_emoji_for(self.analyses_count)

    @rx.var
    def cat_title(self) -> str:
        return _cat_title_for(self.analyses_count)

    # ------------------------------------------------------------------
    # UI setters
    # ------------------------------------------------------------------
    def set_tab(self, tab: str):
        self.active_tab = tab

    def set_finding_filter(self, sev: str):
        self.finding_filter = sev if sev in _FILTERS else "all"

    def set_transcript_query(self, q: str):
        self.transcript_query = q

    def clear_transcript_query(self):
        self.transcript_query = ""

    def set_knowledge_turn_query(self, value: str):
        self.knowledge_turn_query = value
        matches = self._filtered_knowledge_turn_items()
        if matches and all(turn.id != self.active_knowledge_turn for turn in matches):
            self.active_knowledge_turn = matches[0].id

    def clear_knowledge_turn_query(self):
        self.knowledge_turn_query = ""

    def select_knowledge_turn(self, turn_id: str):
        if any(turn.id == turn_id for turn in self._knowledge_turns):
            self.active_knowledge_turn = turn_id

    def previous_knowledge_turn(self):
        turns = self._filtered_knowledge_turn_items()
        if not turns:
            return
        current = next(
            (i for i, turn in enumerate(turns) if turn.id == self.active_knowledge_turn),
            0,
        )
        if current > 0:
            self.active_knowledge_turn = turns[current - 1].id

    def next_knowledge_turn(self):
        turns = self._filtered_knowledge_turn_items()
        if not turns:
            return
        current = next(
            (i for i, turn in enumerate(turns) if turn.id == self.active_knowledge_turn),
            0,
        )
        if current < len(turns) - 1:
            self.active_knowledge_turn = turns[current + 1].id

    def set_tool_turn_query(self, value: str):
        self.tool_turn_query = value
        matches = self._filtered_tool_turn_items()
        if matches and all(turn.id != self.active_tool_turn for turn in matches):
            self.active_tool_turn = matches[0].id

    def clear_tool_turn_query(self):
        self.tool_turn_query = ""

    def select_tool_turn(self, turn_id: str):
        if any(turn.id == turn_id for turn in self._tool_turns):
            self.active_tool_turn = turn_id

    def previous_tool_turn(self):
        turns = self._filtered_tool_turn_items()
        if not turns:
            return
        current = next(
            (i for i, turn in enumerate(turns) if turn.id == self.active_tool_turn),
            0,
        )
        if current > 0:
            self.active_tool_turn = turns[current - 1].id

    def next_tool_turn(self):
        turns = self._filtered_tool_turn_items()
        if not turns:
            return
        current = next(
            (i for i, turn in enumerate(turns) if turn.id == self.active_tool_turn),
            0,
        )
        if current < len(turns) - 1:
            self.active_tool_turn = turns[current + 1].id

    def toggle_citation(self, rid: str):
        self.active_citation = "" if self.active_citation == rid else rid

    def toggle_tool(self, cid: str):
        """Expand/collapse a tool-call drop-down (Level 1)."""
        if cid in self.tool_open:
            self.tool_open = [x for x in self.tool_open if x != cid]
        else:
            self.tool_open = self.tool_open + [cid]

    def toggle_tool_section(self, key: str):
        """Expand/collapse a per-section drop-down inside a tool call (Level 2).
        key is 'call_id::section'."""
        if key in self.tool_sections_open:
            self.tool_sections_open = [x for x in self.tool_sections_open if x != key]
        else:
            self.tool_sections_open = self.tool_sections_open + [key]

    def expand_all_tools(self):
        """Open every tool call in the flat 'All tool calls' list and every one of its
        detail sections at once (success + fail). Preserves already-open chat/fail rows."""
        open_calls = list(self.tool_open)
        open_sections = list(self.tool_sections_open)
        for _tc in self._tool_calls_all:
            _cid = _tc.call_id
            if _cid not in open_calls:
                open_calls.append(_cid)
            for _sec in ("what", "ctx", "params", "resp", "err", "diag"):
                _key = f"{_cid}::{_sec}"
                if _key not in open_sections:
                    open_sections.append(_key)
        self.tool_open = open_calls
        self.tool_sections_open = open_sections

    def collapse_all_tools(self):
        """Collapse every tool call in the flat 'All tool calls' list and its sections.
        Leaves unrelated keys (chat cards, fail:: rows) untouched."""
        flat_ids = [_tc.call_id for _tc in self._tool_calls_all]
        self.tool_open = [c for c in self.tool_open if c not in flat_ids]
        self.tool_sections_open = [k for k in self.tool_sections_open if k.split("::")[0] not in flat_ids]

    def toggle_thoughts(self):
        self.show_thoughts = not self.show_thoughts

    def set_component_query(self, q: str):
        self.component_query = q

    def clear_component_query(self):
        self.component_query = ""

    def on_node_click(self, nid: str):
        """Group/provider rows toggle collapse; providers and leaves also select
        for the detail pane."""
        node = next((n for n in self.component_nodes if n.id == nid), None)
        if node is None:
            return
        if node.is_branch:
            if nid in self.collapsed_nodes:
                self.collapsed_nodes = [x for x in self.collapsed_nodes if x != nid]
            else:
                self.collapsed_nodes = self.collapsed_nodes + [nid]
        if node.selectable:
            self.active_component = nid

    def select_component(self, cid: str):
        self.active_component = cid

    def set_dv_org_url(self, value: str):
        self.dv_org_url = value

    def set_dv_tenant_id(self, value: str):
        self.dv_tenant_id = value

    def set_dv_client_id(self, value: str):
        self.dv_client_id = value

    def set_dv_bot_identifier(self, value: str):
        self.dv_bot_identifier = value

    def set_dv_since_date(self, value: str):
        self.dv_since_date = value

    def set_dv_top_n(self, value: str):
        try:
            self.dv_top_n = max(1, min(int(value), 250))
        except (TypeError, ValueError):
            return

    def set_dv_session_details_paste(self, value: str):
        self.dv_session_details_paste = value

    def set_dv_conversation_id(self, value: str):
        self.dv_conversation_id = value

    def dv_forget_connection_info(self):
        self.dv_org_url = ""
        self.dv_tenant_id = ""
        self.dv_client_id = DEFAULT_CLIENT_ID
        self.dv_bot_identifier = ""
        self.dv_session_details_paste = ""
        self.dv_autofill_error = ""
        self.dv_auth_error = ""

    # ------------------------------------------------------------------
    # Usage counter (shared komarev counter)
    # ------------------------------------------------------------------
    async def refresh_counter(self):
        """Fetch the shared komarev count and animate when it grows."""
        prev = self.analyses_count
        self.analyses_count = await asyncio.to_thread(_fetch_community_count)
        if self.analyses_count > prev and prev > 0:
            self.counter_animating = True
            self.milestone_reached = any(prev < t <= self.analyses_count for t in _MILESTONE_THRESHOLDS)

    async def initialize_page(self):
        if not self.dv_since_date:
            self.dv_since_date = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
        await self.refresh_counter()

    def reset_counter_animation(self):
        self.counter_animating = False
        self.milestone_reached = False

    # ------------------------------------------------------------------
    # Upload routing
    # ------------------------------------------------------------------
    def _route(self, name: str, text: str) -> str:
        low = name.lower()
        if low.endswith(".json"):
            return "transcript"
        if low.endswith((".yaml", ".yml")):
            return "agent"
        stripped = text.lstrip()
        if stripped.startswith("["):
            return "transcript"
        if "BotDefinition" in text or "agentSettings" in text:
            return "agent"
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return "agent" if (":" in text and "{" not in text[:80]) else ""
        if isinstance(obj, list):
            return "transcript"
        # Dataverse conversationtranscript exports arrive as an envelope object.
        if isinstance(obj, dict) and any(k in obj for k in TRANSCRIPT_ENVELOPE_KEYS):
            return "transcript"
        return ""

    # ------------------------------------------------------------------
    # Pasted JSON
    # ------------------------------------------------------------------
    @rx.var
    def paste_preview(self) -> str:
        """Live validity hint under the paste box."""
        text = self.paste_text.strip()
        if not text:
            return ""
        try:
            obj = json.loads(text)
        except ValueError as exc:
            return f"Not valid JSON — {exc.msg} (line {exc.lineno})"

        if isinstance(obj, list):
            return f"Valid JSON · array of {len(obj)} item(s)"
        if isinstance(obj, dict):
            for key in TRANSCRIPT_ENVELOPE_KEYS:
                value = obj.get(key)
                if isinstance(value, list):
                    return f"Valid JSON · {key} envelope with {len(value)} item(s)"
            return "Valid JSON, but no transcript array found (expected activities/messages/records)"
        return "Valid JSON, but not a transcript"

    @rx.var
    def paste_is_valid(self) -> bool:
        text = self.paste_text.strip()
        if not text:
            return False
        try:
            obj = json.loads(text)
        except ValueError:
            return False
        if isinstance(obj, list):
            return True
        return isinstance(obj, dict) and any(isinstance(obj.get(k), list) for k in TRANSCRIPT_ENVELOPE_KEYS)

    def analyse_pasted(self):
        """Load a transcript pasted straight from the Dataverse row."""
        self.error = ""
        text = self.paste_text.strip()
        if not text:
            self.error = "Paste a transcript JSON first."
            return

        # Route on content only — a name ending in .json would short-circuit the
        # envelope sniffing below and accept any JSON object.
        kind = self._route("pasted", text)
        if kind != "transcript":
            self.error = (
                "That does not look like a transcript. Expected a JSON array of messages or an activities envelope."
            )
            return

        self._transcript_text, self.transcript_name = text, "pasted-transcript.json"
        logger.info(f"Pasted transcript ({len(text)} chars) -> transcript")
        self._set_status()
        # Same inline pattern as handle_upload: analyse now, don't chain an event.
        self.run_analysis()

    def clear_paste(self):
        self.paste_text = ""
        self.error = ""

    def back_to_input(self):
        """Reopen the upload/paste panel without discarding what is loaded.

        Analysis runs as soon as one source lands, which hides the input panel.
        Without this the second source (agent YAML or transcript) is unreachable.
        """
        self.has_report = False
        self.error = ""
        self._set_status()

    def set_paste_text(self, value: str):
        self.paste_text = value

    # ------------------------------------------------------------------
    # Dataverse import
    # ------------------------------------------------------------------
    def dv_autofill_from_session_details(self):
        text = self.dv_session_details_paste.strip()
        if not text:
            self.dv_autofill_error = "Paste the Copilot Studio session details first."
            return

        filled: list[str] = []
        missing: list[str] = []
        patterns = (
            ("Tenant ID", r"Tenant\s+ID\s*:\s*([0-9a-f-]{36})", "dv_tenant_id"),
            ("Instance URL", r"Instance\s+url\s*:\s*(https?://\S+)", "dv_org_url"),
            ("Copilot ID", r"Copilot\s+Id\s*:\s*([0-9a-f-]{36})", "dv_bot_identifier"),
        )
        for label, pattern, field in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                setattr(self, field, match.group(1).rstrip("/"))
                filled.append(label)
            else:
                missing.append(label)

        if not filled:
            self.dv_autofill_error = (
                "No session details found. Expected Tenant ID, Instance url, and Copilot Id labels."
            )
            return
        self.dv_session_details_paste = ""
        self.dv_autofill_error = f"Filled {', '.join(filled)}. Missing: {', '.join(missing)}." if missing else ""

    @rx.event(background=True)
    async def start_device_flow(self):
        async with self:
            self.dv_auth_error = ""
            self.dv_fetch_error = ""
            self._dv_auth_attempt += 1
            auth_attempt = self._dv_auth_attempt
            org_value = self.dv_org_url
            tenant_value = self.dv_tenant_id
            client_value = self.dv_client_id
        try:
            tenant_id, client_id, org_url = validate_auth_config(
                tenant_value,
                client_value,
                org_value,
            )
        except ValueError as exc:
            async with self:
                if auth_attempt == self._dv_auth_attempt:
                    self.dv_auth_error = str(exc)
            return

        async with self:
            if auth_attempt != self._dv_auth_attempt:
                return
            self.dv_tenant_id = tenant_id
            self.dv_client_id = client_id
            self.dv_org_url = org_url
            self.dv_is_authenticating = True
            self.dv_device_code = ""
            self.dv_device_code_url = ""

        try:
            flow = await initiate_device_flow(tenant_id, client_id, org_url)
            async with self:
                if auth_attempt != self._dv_auth_attempt:
                    return
                self.dv_device_code = str(flow["user_code"])
                self.dv_device_code_url = str(flow.get("verification_uri") or "https://microsoft.com/devicelogin")

            async def auth_cancelled() -> bool:
                async with self:
                    return auth_attempt != self._dv_auth_attempt

            access_token = await acquire_device_flow_token(
                tenant_id,
                client_id,
                str(flow["device_code"]),
                expires_in=min(int(flow.get("expires_in") or 300), 300),
                interval=int(flow.get("interval") or 5),
                is_cancelled=auth_cancelled,
            )
            if access_token is None:
                return
            async with self:
                if auth_attempt != self._dv_auth_attempt:
                    return
                self._dv_token = access_token
                self.dv_is_connected = True
                self.dv_is_authenticating = False
                self.dv_device_code = ""
                self.dv_device_code_url = ""
                bot_identifier = self.dv_bot_identifier
                since_date = self.dv_since_date
                top_n = self.dv_top_n
            logger.info("Dataverse device-code authentication succeeded")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Dataverse authentication failed: {exc}")
            async with self:
                if auth_attempt == self._dv_auth_attempt:
                    self.dv_auth_error = str(exc)
                    self._dv_token = ""
                    self.dv_is_connected = False
                    self.dv_is_authenticating = False
                    self.dv_device_code = ""
                    self.dv_device_code_url = ""
            return

        if not bot_identifier.strip():
            return

        async with self:
            if auth_attempt != self._dv_auth_attempt:
                return
            self.dv_is_fetching = True
        try:
            summaries, empty_count = await _fetch_dataverse_records(
                org_url,
                access_token,
                bot_identifier,
                since_date,
                top_n,
            )
            async with self:
                if auth_attempt != self._dv_auth_attempt:
                    return
                self.dv_transcripts = summaries
                if not summaries:
                    self.dv_fetch_error = _dataverse_empty_message(empty_count, since_date)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Initial Dataverse transcript fetch failed: {exc}")
            async with self:
                if auth_attempt == self._dv_auth_attempt:
                    self.dv_fetch_error = str(exc)
        finally:
            async with self:
                if auth_attempt == self._dv_auth_attempt:
                    self.dv_is_fetching = False

    @rx.event(background=True)
    async def dv_fetch_transcripts(self):
        async with self:
            if not self.dv_is_connected or not self._dv_token:
                self.dv_fetch_error = "Connect to Dataverse first."
                return
            request_attempt = self._dv_auth_attempt
            org_url = self.dv_org_url
            token = self._dv_token
            bot_identifier = self.dv_bot_identifier
            since_date = self.dv_since_date
            top_n = self.dv_top_n
            self.dv_fetch_error = ""
            self.dv_is_fetching = True
        try:
            summaries, empty_count = await _fetch_dataverse_records(
                org_url,
                token,
                bot_identifier,
                since_date,
                top_n,
            )
            async with self:
                if request_attempt != self._dv_auth_attempt or not self.dv_is_connected:
                    return
                self.dv_transcripts = summaries
                if not summaries:
                    self.dv_fetch_error = _dataverse_empty_message(empty_count, since_date)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Dataverse transcript fetch failed: {exc}")
            async with self:
                if request_attempt == self._dv_auth_attempt and self.dv_is_connected:
                    self.dv_fetch_error = str(exc)
        finally:
            async with self:
                if request_attempt == self._dv_auth_attempt:
                    self.dv_is_fetching = False

    @rx.event(background=True)
    async def dv_analyse_transcript(self, transcript_id: str):
        async with self:
            if not self.dv_is_connected or not self._dv_token:
                self.dv_fetch_error = "Connect to Dataverse first."
                return
            request_attempt = self._dv_auth_attempt
            self._dv_analysis_attempt += 1
            analysis_attempt = self._dv_analysis_attempt
            org_url = self.dv_org_url
            token = self._dv_token
            self.dv_is_fetching = True
            self.dv_single_fetching = False
            self.dv_fetch_error = ""
        try:
            _summary, content = await _fetch_dataverse_transcript(org_url, token, transcript_id)
            async with self:
                if (
                    request_attempt != self._dv_auth_attempt
                    or analysis_attempt != self._dv_analysis_attempt
                    or not self.dv_is_connected
                ):
                    return
                self._transcript_text = content
                self.transcript_name = f"dataverse-{transcript_id[:8]}.json"
                self._set_status()
                self.run_analysis()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Dataverse transcript analysis failed: {exc}")
            async with self:
                if (
                    request_attempt == self._dv_auth_attempt
                    and analysis_attempt == self._dv_analysis_attempt
                    and self.dv_is_connected
                ):
                    self.dv_fetch_error = str(exc)
        finally:
            async with self:
                if request_attempt == self._dv_auth_attempt and analysis_attempt == self._dv_analysis_attempt:
                    self.dv_is_fetching = False
                    self.dv_single_fetching = False

    @rx.event(background=True)
    async def dv_fetch_and_analyse_by_id(self):
        async with self:
            conversation_id = self.dv_conversation_id.strip()
            if not self.dv_is_connected or not self._dv_token:
                self.dv_single_fetch_error = "Connect to Dataverse first."
                return
            if not _UUID_RE.fullmatch(conversation_id):
                self.dv_single_fetch_error = "Conversation ID must be a UUID."
                return
            self.dv_single_fetch_error = ""
            self.dv_single_fetching = True
            self.dv_is_fetching = False
            request_attempt = self._dv_auth_attempt
            self._dv_analysis_attempt += 1
            analysis_attempt = self._dv_analysis_attempt
            org_url = self.dv_org_url
            token = self._dv_token
        try:
            summary, content = await _fetch_dataverse_transcript(org_url, token, conversation_id)
            async with self:
                if (
                    request_attempt != self._dv_auth_attempt
                    or analysis_attempt != self._dv_analysis_attempt
                    or not self.dv_is_connected
                ):
                    return
                self.dv_transcripts = [summary]
                self._transcript_text = content
                self.transcript_name = f"dataverse-{conversation_id[:8]}.json"
                self._set_status()
                self.run_analysis()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Dataverse conversation lookup failed: {exc}")
            async with self:
                if (
                    request_attempt == self._dv_auth_attempt
                    and analysis_attempt == self._dv_analysis_attempt
                    and self.dv_is_connected
                ):
                    self.dv_single_fetch_error = str(exc)
        finally:
            async with self:
                if request_attempt == self._dv_auth_attempt and analysis_attempt == self._dv_analysis_attempt:
                    self.dv_single_fetching = False
                    self.dv_is_fetching = False

    def dv_disconnect(self):
        self._dv_auth_attempt += 1
        self._dv_analysis_attempt += 1
        self._dv_token = ""
        self.dv_is_connected = False
        self.dv_is_authenticating = False
        self.dv_is_fetching = False
        self.dv_single_fetching = False
        self.dv_device_code = ""
        self.dv_device_code_url = ""
        self.dv_auth_error = ""
        self.dv_fetch_error = ""
        self.dv_transcripts = []
        self.dv_single_fetch_error = ""
        if self.transcript_name.startswith("dataverse-"):
            self._transcript_text = ""
            self.transcript_name = ""
            self.full_md = ""
            self.has_report = False
            self.status = ""

    def dv_cancel_auth(self):
        self._dv_auth_attempt += 1
        self._dv_analysis_attempt += 1
        self.dv_is_authenticating = False
        self.dv_device_code = ""
        self.dv_device_code_url = ""
        self.dv_auth_error = ""

    async def handle_upload(self, files: list[rx.UploadFile]):
        self.error = ""
        for file in files:
            try:
                data = await file.read()
            except Exception as exc:  # noqa: BLE001
                self.error = f"Could not read upload: {exc}"
                logger.error(self.error)
                continue
            name = getattr(file, "name", None) or getattr(file, "filename", "") or "upload"
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                self.error = f"{name}: not a UTF-8 text file."
                continue
            kind = self._route(name, text)
            if kind == "transcript":
                self._transcript_text, self.transcript_name = text, name
            elif kind == "agent":
                self.agent_text, self.agent_name = text, name
            else:
                self.error = f"{name}: could not tell if this is a transcript or an agent YAML."
                continue
            logger.info(f"Uploaded {name} -> {kind}")
        self._set_status()
        # Run analysis in the same handler so it can't race the upload POST.
        # (The upload arrives over a separate HTTP channel; a chained
        # run_analysis event would read empty state on the first click.)
        if not self.error and (self._transcript_text or self.agent_text):
            self.run_analysis()

    def _set_status(self):
        bits = []
        if self._transcript_text:
            bits.append(f"transcript ({self.transcript_name})")
        if self.agent_text:
            bits.append(f"agent ({self.agent_name})")
        self.status = "Loaded: " + ", ".join(bits) if bits else ""

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def run_analysis(self):
        self.error = ""
        if not (self._transcript_text or self.agent_text):
            self.error = "Upload a transcript JSON and/or an agent YAML first."
            return

        profile: AgentProfile | None = None
        convo: Conversation | None = None
        try:
            if self.agent_text:
                profile = parse_agent_yaml_text(self.agent_text)
        except Exception as exc:  # noqa: BLE001
            self.error = f"Agent YAML parse failed: {exc}"
            logger.error(self.error)
            return
        try:
            if self._transcript_text:
                convo = parse_transcript_text(self._transcript_text)
        except Exception as exc:  # noqa: BLE001
            self.error = f"Transcript parse failed: {exc}"
            logger.error(self.error)
            return

        report = analyze(profile, convo)
        self.full_md = render_markdown(report, convo)
        self._apply_vm(map_report(report, convo))

        self.active_tab = "overview"
        self.finding_filter = "all"
        self.transcript_query = ""
        self.active_citation = ""
        self.component_query = ""
        self.active_component = ""
        self.has_report = True
        self.status = ""
        logger.info(f"Report ready for {self.agent_title}")

    def _apply_vm(self, vm):
        self.agent_present = vm.has_agent
        self.convo_present = vm.has_convo
        self.agent_title = vm.agent_name
        self.model_label = vm.model_label
        self.template = vm.template
        self.recognizer = vm.recognizer
        self.auth = vm.auth
        self.memory = vm.memory
        self.instructions = vm.instructions
        self.created_at = vm.created_at
        self.modified_at = vm.modified_at
        self.conversation_starters = vm.conversation_starters
        self.knowledge_sources = vm.knowledge_sources
        self.env_vars = vm.env_vars

        self.m_turns, self.m_user, self.m_bot = vm.m_turns, vm.m_user, vm.m_bot
        self.m_tools, self.m_searches, self.m_thoughts = vm.m_tools, vm.m_searches, vm.m_thoughts
        self.m_failed, self.m_zero = vm.m_failed, vm.m_zero

        self.findings = vm.findings
        self.f_critical, self.f_warning, self.f_info = vm.f_critical, vm.f_warning, vm.f_info

        self.tool_rows = vm.tool_rows
        self._tool_calls_all = vm.tool_calls_all
        self._tool_turns = vm.tool_turns
        self.active_tool_turn = vm.tool_turns[0].id if vm.tool_turns else ""
        self.tool_turn_query = ""
        self.skill_loads = vm.skill_loads
        self.retry_signals = vm.retry_signals
        self.tool_failures = vm.tool_failures

        self._knowledge_turns = vm.knowledge_turns
        self.active_knowledge_turn = vm.knowledge_turns[0].id if vm.knowledge_turns else ""
        self.knowledge_turn_query = ""
        self.uncited_docs = vm.uncited_docs
        self.sources_seen = vm.sources_seen
        self.zero_result_queries = vm.zero_result_queries

        self.citation_markers = vm.citation_markers
        self.uncited_answer_count = vm.uncited_answer_count

        self.citation_rows = vm.citation_rows
        self.cit_resolved, self.cit_dangling, self.cit_uncited = vm.cit_resolved, vm.cit_dangling, vm.cit_uncited

        self.source_effectiveness = vm.source_effectiveness
        self.eff_total_searches, self.eff_distinct_docs = vm.eff_total_searches, vm.eff_distinct_docs
        self.eff_avg_docs, self.eff_unattributed = vm.eff_avg_docs, vm.eff_unattributed

        self.credit_lines = vm.credit_lines
        self.credit_by_kind = vm.credit_by_kind
        self.credit_total = vm.credit_total
        self.credit_notes = vm.credit_notes
        self.has_credits = vm.has_credits
        self.credit_reasoning_model = vm.credit_reasoning_model
        self.credit_total_tokens = vm.credit_total_tokens
        self.credit_assumptions = vm.credit_assumptions
        self.credit_estimator_url = vm.credit_estimator_url

        self.sandbox_used = vm.sandbox_used
        self.sandbox_turns = vm.sandbox_turns
        self.sandbox_tools_label = vm.sandbox_tools_label
        self.sandbox_signals = vm.sandbox_signals
        self.sandbox_friction = vm.sandbox_friction
        self.sandbox_friction_count = vm.sandbox_friction_count
        self.sandbox_skills = vm.sandbox_skills
        self.sandbox_doc_skills = vm.sandbox_doc_skills

        self.rd_folders = vm.rd_folders
        self.rd_docs = vm.rd_docs
        self.rd_unique_docs = vm.rd_unique_docs
        self.rd_total_retrieved = vm.rd_total_retrieved
        self.rd_overlap_docs = vm.rd_overlap_docs
        self.rd_cited_docs = vm.rd_cited_docs
        self.rd_over_retrieval_label = vm.rd_over_retrieval_label
        self.rd_over_retrieval_pct = vm.rd_over_retrieval_pct
        self.rd_mode = vm.rd_mode
        self.rd_full_reads = vm.rd_full_reads
        self.has_retrieval_depth = vm.has_retrieval_depth

        self.search_precision = vm.search_precision
        self.recall_turns = vm.recall_turns
        self.ss_productive = vm.ss_productive
        self.ss_unproductive = vm.ss_unproductive
        self.has_search_strategy = vm.has_search_strategy

        self.artifacts = vm.artifacts
        self.artifact_count = vm.artifact_count
        self.artifact_types_label = vm.artifact_types_label
        self.has_artifacts = vm.has_artifacts

        self.sandbox_authoring_label = vm.sandbox_authoring_label
        self.sandbox_analysis_label = vm.sandbox_analysis_label
        self.sandbox_authoring_count = len(vm.sandbox_authoring_turns)
        self.sandbox_analysis_count = len(vm.sandbox_analysis_turns)

        self.skill_gaps = vm.skill_gaps
        self.has_skill_gaps = vm.has_skill_gaps

        self.grounding_docs = vm.grounding_docs
        self.gp_snippet_mode_label = vm.gp_snippet_mode_label
        self.gp_snippet_mode_icon = vm.gp_snippet_mode_icon
        self.gp_snippet_mode_color = vm.gp_snippet_mode_color
        self.gp_span_label = vm.gp_span_label
        self.gp_span_icon = vm.gp_span_icon
        self.gp_span_color = vm.gp_span_color
        self.gp_stub_results = vm.gp_stub_results
        self.gp_content_results = vm.gp_content_results
        self.gp_notes = vm.gp_notes
        self.has_grounding_pipeline = vm.has_grounding_pipeline

        self.components = vm.components
        self.component_nodes = vm.component_nodes
        self.collapsed_nodes = []

        self.tool_failure_rows = vm.tool_failure_rows
        self.tf_total, self.tf_embedded = vm.tf_total, vm.tf_embedded
        self.tf_recovered, self.tf_gaveup = vm.tf_recovered, vm.tf_gaveup

        self.duplicate_groups = vm.duplicate_groups
        self.eff_total_calls, self.eff_unique_calls = vm.eff_total_calls, vm.eff_unique_calls
        self.eff_redundant = vm.eff_redundant
        self.eff_calls_per_answer = vm.eff_calls_per_answer

        self.repetition = vm.repetition

        self.answer_grounding = vm.answer_grounding
        self.ag_high, self.ag_medium, self.ag_low = vm.ag_high, vm.ag_medium, vm.ag_low

        self.quote_rows = vm.quote_rows
        self.qf_verified, self.qf_attributed = vm.qf_verified, vm.qf_attributed
        self.qf_dangling, self.qf_unattributed = vm.qf_dangling, vm.qf_unattributed

        self.coverage_gaps = vm.coverage_gaps

        self.te_calls_per_answer = vm.te_calls_per_answer
        self.te_searches_to_first = vm.te_searches_to_first
        self.te_avg_bot_msgs = vm.te_avg_bot_msgs
        self.te_user_turns = vm.te_user_turns

        self.timeline = vm.timeline

        self.premise_corrections = vm.premise_corrections
        self.thoughts_per_turn = vm.thoughts_per_turn

        self.grounded, self.ungrounded = vm.grounded, vm.ungrounded
        self.hallucination_risk = vm.hallucination_risk
        self.honest_grounding = vm.honest_grounding
        self.groundedness_notes = vm.groundedness_notes

        self.checks = vm.checks

        self.unused_knowledge_sources = vm.unused_knowledge_sources
        self.contributing_knowledge_sources = vm.contributing_knowledge_sources
        self.tools_used_not_defined = vm.tools_used_not_defined

        self.chat = vm.chat
        # Seed disclosure defaults: a failed call auto-opens itself and ALL of its
        # detail panels (What happened / Request params / Response body / Error /
        # Diagnosis) so the connector's real inputs + error body are visible on load
        # without a click. Successful calls stay fully collapsed. Driven off the flat
        # tool_calls_all list (same VM objects/ids as the chat cards, so both surfaces
        # open together).
        open_calls: list[str] = []
        open_sections: list[str] = []
        for _tc in vm.tool_calls_all:
            _cid = _tc.call_id
            _failed = getattr(_tc, "failed", False) or getattr(_tc, "has_error", False)
            _payloadless = not (_tc.params or _tc.raw_result or _tc.docs or _tc.content_html or _tc.content_text)
            if _failed:
                open_calls.append(_cid)
                for _sec in ("what", "ctx", "params", "resp", "err", "diag"):
                    open_sections.append(f"{_cid}::{_sec}")
            elif _payloadless:
                # A payload-less call (e.g. a bare skill load) has nothing in
                # params/resp/err — its only debugging value is the derived Context,
                # so open What happened + Context by default.
                open_calls.append(_cid)
                open_sections.append(f"{_cid}::what")
                open_sections.append(f"{_cid}::ctx")
        # Failure-block rows are keyed "fail::<call_id>" (distinct from chat cards);
        # seed them open so the cause/fix + embedded detail is visible without a click.
        for _fr in vm.tool_failure_rows:
            if getattr(_fr, "call_id", ""):
                open_calls.append(f"fail::{_fr.call_id}")
        self.tool_open = open_calls
        self.tool_sections_open = open_sections
        self.turns = vm.turns
        self.mermaid = vm.mermaid

    # ------------------------------------------------------------------
    # Samples
    # ------------------------------------------------------------------
    def load_sample(self, kind: str = "knowledge"):
        """Load a bundled sample. `knowledge` = agent YAML + transcript;
        `agentic` = transcript only (autonomous Teams agent);
        `connector` = transcript only (failed SharePoint connector/MCP calls);
        `sandbox` = HR agent that uses the code interpreter + reasoning model;
        `deck` = HR agent that generates a PowerPoint (artifacts + skill gap + grounding pipeline)."""
        self.error = ""
        self.agent_text = self.agent_name = ""
        try:
            if kind == "agentic":
                with open("samples/sample_transcript_agentic.json", encoding="utf-8") as fh:
                    self._transcript_text = fh.read()
                    self.transcript_name = "sample_transcript_agentic.json"
            elif kind == "connector":
                with open("samples/sample_transcript_connector_fail.json", encoding="utf-8") as fh:
                    self._transcript_text = fh.read()
                    self.transcript_name = "sample_transcript_connector_fail.json"
            elif kind == "sandbox":
                with open("samples/sample_agent_sandbox.yaml", encoding="utf-8") as fh:
                    self.agent_text = fh.read()
                    self.agent_name = "sample_agent_sandbox.yaml"
                with open("samples/sample_transcript_sandbox.json", encoding="utf-8") as fh:
                    self._transcript_text = fh.read()
                    self.transcript_name = "sample_transcript_sandbox.json"
            elif kind == "deck":
                with open("samples/sample_agent_sandbox.yaml", encoding="utf-8") as fh:
                    self.agent_text = fh.read()
                    self.agent_name = "sample_agent_sandbox.yaml"
                with open("samples/sample_transcript_deck.json", encoding="utf-8") as fh:
                    self._transcript_text = fh.read()
                    self.transcript_name = "sample_transcript_deck.json"
            else:
                with open("samples/sample_agent.yaml", encoding="utf-8") as fh:
                    self.agent_text = fh.read()
                    self.agent_name = "sample_agent.yaml"
                with open("samples/sample_transcript.json", encoding="utf-8") as fh:
                    self._transcript_text = fh.read()
                    self.transcript_name = "sample_transcript.json"
        except OSError as exc:
            self.error = f"Could not load sample: {exc}"
            return
        self._set_status()
        self.run_analysis()

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    def _slug(self) -> str:
        return (self.agent_title or "agent").lower().replace(" ", "_")

    def download_md(self):
        if not self.full_md:
            return None
        return rx.download(data=self.full_md, filename=self._slug() + "_analysis.md")

    def download_html(self):
        if not self.full_md:
            return None
        title = f"Agent analysis — {self.agent_title or 'Modern agent'}"
        html_doc = build_standalone_html(self.full_md, title)
        return rx.download(data=html_doc, filename=self._slug() + "_analysis.html")

    def download_raw_transcript(self):
        if not self._transcript_text:
            return None
        filename = self.transcript_name or "conversation-transcript.json"
        return rx.download(data=self._transcript_text, filename=filename)

    def print_report(self):
        return rx.call_script("window.print()")

    def clear_all(self):
        self._transcript_text = ""
        self.agent_text = ""
        self.transcript_name = self.agent_name = ""
        self.paste_text = ""
        self.full_md = ""
        self.has_report = False
        self.error = self.status = ""
        self.active_tab = "overview"
        self.finding_filter = "all"
        self.transcript_query = ""
        self.knowledge_turn_query = ""
        self.active_knowledge_turn = ""
        self.tool_turn_query = ""
        self.active_tool_turn = ""
        self.active_citation = ""
        self.tool_open = []
        self.tool_sections_open = []
        self.component_query = ""
        self.active_component = ""
        self.collapsed_nodes = []
        self._tool_calls_all = []
        self._tool_turns = []
        self._knowledge_turns = []
        self._dv_auth_attempt += 1
        self._dv_analysis_attempt += 1
        self._dv_token = ""
        self.dv_is_connected = False
        self.dv_is_authenticating = False
        self.dv_is_fetching = False
        self.dv_single_fetching = False
        self.dv_auth_error = ""
        self.dv_fetch_error = ""
        self.dv_single_fetch_error = ""
        self.dv_device_code = ""
        self.dv_device_code_url = ""
        self.dv_transcripts = []
        return rx.clear_selected_files("upload")


# Re-export for tooling/tests
__all__ = ["State", "TAB_DEFS"]
