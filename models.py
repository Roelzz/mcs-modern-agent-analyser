"""Pydantic models for the modern agent analyser.

Three groups:
  1. Agent profile  — parsed from the modern `BotDefinition` build YAML.
  2. Conversation   — parsed from the modern flat transcript JSON.
  3. Analysis       — heuristic results produced by `analysis.py`.

Modern transcripts carry no timing data, so everything here is count/structure
based. `occurred_at` hooks are kept optional in case a future export adds them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 1. Agent profile (from build YAML)
# ---------------------------------------------------------------------------


class KnowledgeSource(BaseModel):
    display_name: str
    schema_name: str | None = None
    description: str | None = None
    source_kind: str | None = None  # e.g. SharePointKnowledgeSource
    source_site: str | None = None  # siteUrl (URL-decoded)
    state: str | None = None
    status: str | None = None
    modified_at: str | None = None


class EnvVar(BaseModel):
    display_name: str
    schema_name: str | None = None
    type: str | None = None
    default_value: str | None = None


class ToolComponent(BaseModel):
    """Any action/tool/connector/connected-agent defined on the agent. Modern
    knowledge agents usually have none, but we capture them for cross-reference."""

    kind: str
    display_name: str
    description: str | None = None


class AgentProfile(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    display_name: str = "Unknown agent"
    schema_name: str | None = None
    cds_bot_id: str | None = None
    template: str | None = None  # e.g. cliagent-1.0.0
    recognizer_kind: str | None = None  # e.g. CLICopilotRecognizer
    authentication_mode: str | None = None
    authentication_trigger: str | None = None
    access_control_policy: str | None = None  # e.g. GroupMembership
    runtime_provider: str | None = None  # e.g. PowerVirtualAgents

    model_series: str | None = None  # raw, e.g. Sonnet46
    model_label: str | None = None  # friendly, e.g. "Claude Sonnet 4.6"

    instructions: str = ""  # joined segments
    instruction_segments: list[str] = Field(default_factory=list)
    enable_memory: bool = False
    conversation_starters: list[str] = Field(default_factory=list)

    knowledge_sources: list[KnowledgeSource] = Field(default_factory=list)
    environment_variables: list[EnvVar] = Field(default_factory=list)
    tool_components: list[ToolComponent] = Field(default_factory=list)

    created_at: str | None = None
    modified_at: str | None = None

    @property
    def is_modern(self) -> bool:
        """True when this looks like a modern cliagent / CLICopilotRecognizer."""
        t = (self.template or "").lower()
        r = (self.recognizer_kind or "").lower()
        return "cliagent" in t or "clicopilot" in r


# ---------------------------------------------------------------------------
# 2. Conversation (from transcript JSON)
# ---------------------------------------------------------------------------


class RetrievedDoc(BaseModel):
    title: str | None = None
    url: str | None = None
    reference_id: str | None = None  # e.g. turn1doc1


class ToolCall(BaseModel):
    id: str | None = None
    name: str | None = None  # KnowledgeSearch, skill, ...
    status: str | None = None  # completed / failed / ...
    display_name: str | None = None  # "Searched knowledge", "Loaded Skill: analyzing-docx"
    params: dict = Field(default_factory=dict)
    result: str | None = None  # raw result text

    # Parsed from `result` (best-effort, for KnowledgeSearch-style tools)
    retrieved_docs: list[RetrievedDoc] = Field(default_factory=list)
    result_count: int | None = None  # from "[N results]"
    zero_result: bool = False

    @property
    def query(self) -> str | None:
        q = self.params.get("query") if isinstance(self.params, dict) else None
        return q if isinstance(q, str) else None

    @property
    def is_knowledge_search(self) -> bool:
        return (self.name or "").lower() == "knowledgesearch"

    @property
    def failed(self) -> bool:
        return (self.status or "").lower() in {"failed", "error"}


class Thought(BaseModel):
    id: str | None = None
    status: str | None = None
    title: str | None = None
    description: str | None = None

    @property
    def text(self) -> str:
        return self.description or self.title or ""


class Message(BaseModel):
    role: str  # bot / user
    id: str | None = None
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    thoughts: list[Thought] = Field(default_factory=list)
    occurred_at: str | None = None  # optional, usually absent in modern transcripts

    @property
    def is_bot(self) -> bool:
        return self.role == "bot"

    @property
    def is_user(self) -> bool:
        return self.role == "user"


class Turn(BaseModel):
    """A user message and all bot messages that follow it (until the next user
    message). A leading bot greeting before any user input is a turn with
    `user_message=None`."""

    index: int
    user_message: Message | None = None
    bot_messages: list[Message] = Field(default_factory=list)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [tc for m in self.bot_messages for tc in m.tool_calls]

    @property
    def thoughts(self) -> list[Thought]:
        return [t for m in self.bot_messages for t in m.thoughts]

    @property
    def final_bot_text(self) -> str:
        for m in reversed(self.bot_messages):
            if m.text.strip():
                return m.text
        return ""


class Conversation(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    turns: list[Turn] = Field(default_factory=list)

    @property
    def bot_messages(self) -> list[Message]:
        return [m for m in self.messages if m.is_bot]

    @property
    def user_messages(self) -> list[Message]:
        return [m for m in self.messages if m.is_user]

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [tc for m in self.messages for tc in m.tool_calls]

    @property
    def thoughts(self) -> list[Thought]:
        return [t for m in self.messages for t in m.thoughts]


# ---------------------------------------------------------------------------
# 3. Analysis results
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    severity: str  # info / warning / critical
    category: str  # Tools / Knowledge / Citations / Reasoning / Quality / Instructions / Agent
    title: str
    detail: str = ""


class ConversationOverview(BaseModel):
    turn_count: int = 0
    user_message_count: int = 0
    bot_message_count: int = 0
    tool_call_count: int = 0
    knowledge_search_count: int = 0
    thought_count: int = 0
    failed_tool_count: int = 0
    zero_result_search_count: int = 0


class ToolUsage(BaseModel):
    name: str
    count: int = 0
    completed: int = 0
    failed: int = 0


class ToolAnalysis(BaseModel):
    usage: list[ToolUsage] = Field(default_factory=list)
    skill_loads: list[str] = Field(default_factory=list)
    retry_signals: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)


class KnowledgeQuery(BaseModel):
    query: str
    result_count: int = 0
    docs: list[RetrievedDoc] = Field(default_factory=list)
    zero_result: bool = False


class KnowledgeAnalysis(BaseModel):
    queries: list[KnowledgeQuery] = Field(default_factory=list)
    distinct_docs: list[RetrievedDoc] = Field(default_factory=list)
    sources_seen: list[str] = Field(default_factory=list)
    cited_reference_ids: list[str] = Field(default_factory=list)
    uncited_docs: list[RetrievedDoc] = Field(default_factory=list)
    zero_result_queries: list[str] = Field(default_factory=list)


class CitationAnalysis(BaseModel):
    total_markers: int = 0  # count of [n] across bot text
    reference_ids_in_results: list[str] = Field(default_factory=list)
    cited_reference_ids: list[str] = Field(default_factory=list)
    uncited_answer_count: int = 0  # bot answers with claims but no citation and no search


class ReasoningTrace(BaseModel):
    total_thoughts: int = 0
    thoughts_per_turn: list[int] = Field(default_factory=list)
    retry_signals: list[str] = Field(default_factory=list)
    premise_corrections: list[str] = Field(default_factory=list)


class GroundednessAssessment(BaseModel):
    grounded_answers: int = 0
    ungrounded_answers: int = 0
    hallucination_risk: list[str] = Field(default_factory=list)
    honest_grounding: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class InstructionCheck(BaseModel):
    instruction: str
    check: str
    status: str  # pass / fail / unknown
    evidence: str = ""


class InstructionCompliance(BaseModel):
    checks: list[InstructionCheck] = Field(default_factory=list)


class CrossReference(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    defined_knowledge_sources: list[str] = Field(default_factory=list)
    contributing_knowledge_sources: list[str] = Field(default_factory=list)
    unused_knowledge_sources: list[str] = Field(default_factory=list)
    tools_used_not_defined: list[str] = Field(default_factory=list)
    model_in_use: str | None = None


class SourceEffectiveness(BaseModel):
    """Per-knowledge-source runtime effectiveness, reconstructed from doc URLs."""

    display_name: str
    source_kind: str | None = None
    source_site: str | None = None
    configured: bool = True  # present in the agent YAML (False = observed only at runtime)
    docs_retrieved: int = 0  # distinct retrieved docs whose URL traces to this source
    docs_cited: int = 0  # of those, how many appear in a bot answer
    contribution_rate: float = 0.0  # docs_cited / docs_retrieved
    zero_contribution: bool = False  # retrieved >0 but cited 0
    never_retrieved: bool = False  # configured but no doc ever came from it


class KnowledgeEffectiveness(BaseModel):
    sources: list[SourceEffectiveness] = Field(default_factory=list)
    total_searches: int = 0
    zero_result_searches: int = 0
    distinct_docs: int = 0
    avg_docs_per_search: float = 0.0
    unattributed_docs: int = 0  # retrieved docs whose URL matched no configured source


class CitationAuditRow(BaseModel):
    """One audited citation: a [n] marker / refid and whether it resolves."""

    marker: str  # the displayed token, e.g. "[1]" or "turn1doc1"
    reference_id: str | None = None
    status: str  # resolved / dangling / uncited_retrieval
    doc_title: str | None = None
    doc_url: str | None = None
    source: str | None = None  # site root the doc came from
    turn_index: int | None = None  # turn where the citation appeared (resolved/dangling)
    provenance: str | None = None  # search query that produced the doc


class CitationAudit(BaseModel):
    rows: list[CitationAuditRow] = Field(default_factory=list)
    resolved: int = 0
    dangling: int = 0  # cited refid not found in any search result
    uncited_retrievals: int = 0  # doc retrieved but never cited


class CreditLineItem(BaseModel):
    label: str
    kind: str  # generative_answer / agent_action / classic_answer
    credits: float
    detail: str = ""


class CreditEstimate(BaseModel):
    line_items: list[CreditLineItem] = Field(default_factory=list)
    total_credits: float = 0.0
    by_kind: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    agent: AgentProfile | None = None
    overview: ConversationOverview | None = None
    tools: ToolAnalysis | None = None
    knowledge: KnowledgeAnalysis | None = None
    knowledge_effectiveness: KnowledgeEffectiveness | None = None
    citations: CitationAnalysis | None = None
    citation_audit: CitationAudit | None = None
    reasoning: ReasoningTrace | None = None
    groundedness: GroundednessAssessment | None = None
    instructions: InstructionCompliance | None = None
    cross_reference: CrossReference | None = None
    credit_estimate: CreditEstimate | None = None
    findings: list[Finding] = Field(default_factory=list)
