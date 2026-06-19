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


class ToolOperation(BaseModel):
    """A single capability ("what it can do") exposed by a tool provider — e.g.
    one MCP tool, one connector action, or one runtime tool call."""

    name: str
    display_name: str | None = None
    description: str | None = None
    configured: bool = True  # False = only observed at runtime, not in the YAML


class ToolProvider(BaseModel):
    """A container that groups one or more operations: an MCP server, a
    connector, a connected agent, a flow, a skill, or a bucket of built-in
    actions. The hierarchy is Provider → Operation."""

    kind: str  # mcpServer | connector | connectedAgent | flow | skill | action
    display_name: str
    schema_name: str | None = None
    description: str | None = None
    source: str | None = None  # best-effort url / connector id / bot id
    configured: bool = True  # True = declared in YAML, False = inferred at runtime
    operations: list[ToolOperation] = Field(default_factory=list)


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
    tool_providers: list[ToolProvider] = Field(default_factory=list)

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
    snippet: str | None = None  # summary text returned with the doc (often a sandbox notice)


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


class FileAttachment(BaseModel):
    name: str = ""  # e.g. HR_Onboarding_Policies.pptx
    file_type: str = ""  # pptx / docx / xlsx / pdf / png ...
    content_type: str = ""  # MIME, e.g. application/octet-stream


class Message(BaseModel):
    role: str  # bot / user
    id: str | None = None
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    thoughts: list[Thought] = Field(default_factory=list)
    file_attachments: list[FileAttachment] = Field(default_factory=list)
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

    @property
    def file_attachments(self) -> list[FileAttachment]:
        return [a for m in self.messages for a in m.file_attachments]


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
    cross_turn: bool = False  # resolved from a doc retrieved in an earlier turn (C1)


class CitationAudit(BaseModel):
    rows: list[CitationAuditRow] = Field(default_factory=list)
    resolved: int = 0
    dangling: int = 0  # cited refid not found in any search result
    uncited_retrievals: int = 0  # doc retrieved but never cited


class CreditLineItem(BaseModel):
    label: str
    kind: str  # generative_answer / agent_action / classic_answer / premium_reasoning / content_processing / tenant_graph
    credits: float
    detail: str = ""
    turn_index: int | None = None  # for the per-turn credit stack
    tokens: int = 0  # estimated tokens (premium reasoning meter)


class CreditEstimate(BaseModel):
    line_items: list[CreditLineItem] = Field(default_factory=list)
    total_credits: float = 0.0
    by_kind: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    # Modern-agent credit model extras
    reasoning_model: bool = False  # model is reasoning-capable → premium token surcharge applies
    total_tokens: int = 0  # heuristic token total feeding the premium meter
    assumptions: list[str] = Field(default_factory=list)


# --- #10 Failed-tool & recovery deep-dive -----------------------------------


class ToolFailure(BaseModel):
    turn_index: int
    name: str
    params_summary: str = ""
    error_text: str = ""
    embedded: bool = False  # True = status said "completed" but the result carried an error
    recovery: str = "gave-up"  # retried-same / recovered-other-tool / unhandled-but-answered / gave-up
    next_action: str | None = None  # the tool that recovered (if any)


class ToolFailureAnalysis(BaseModel):
    failures: list[ToolFailure] = Field(default_factory=list)
    total_failures: int = 0
    embedded_failures: int = 0  # subset hidden behind a "completed" status
    recovered: int = 0  # retried-same or recovered-other-tool
    gave_up: int = 0


# --- #6 Tool-call redundancy / efficiency -----------------------------------


class DuplicateGroup(BaseModel):
    name: str
    params_summary: str = ""
    count: int = 0
    turns: list[int] = Field(default_factory=list)


class ToolEfficiency(BaseModel):
    total_calls: int = 0
    unique_calls: int = 0
    redundant_calls: int = 0  # calls beyond the first in each duplicate group
    calls_per_answer: float = 0.0
    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list)


# --- #5 Repetition / loop detection -----------------------------------------


class RepetitionSignal(BaseModel):
    kind: str  # agent-answer / agent-tool / user-question
    turns: list[int] = Field(default_factory=list)
    similarity: float = 0.0
    excerpt: str = ""


class RepetitionAnalysis(BaseModel):
    signals: list[RepetitionSignal] = Field(default_factory=list)


# --- #2 Per-answer groundedness / hallucination risk ------------------------


class AnswerGroundedness(BaseModel):
    turn_index: int
    factual_claims: int = 0
    cited_claims: int = 0
    had_retrieval: bool = False
    risk: str = "low"  # low / medium / high
    excerpt: str = ""


class AnswerGroundednessAnalysis(BaseModel):
    answers: list[AnswerGroundedness] = Field(default_factory=list)
    high_risk: int = 0
    medium_risk: int = 0
    low_risk: int = 0


# --- #11 Citation quote-traceability ----------------------------------------


class QuoteCheck(BaseModel):
    turn_index: int
    excerpt: str = ""
    ref_id: str | None = None
    source_title: str | None = None
    # verified-in-tool-output / attributed-source-in-sandbox / dangling-attribution / unattributed-quote
    verdict: str = "unattributed-quote"


class QuoteFaithfulness(BaseModel):
    quotes: list[QuoteCheck] = Field(default_factory=list)
    verified: int = 0
    attributed: int = 0  # cited to a retrieved doc whose full text isn't in the transcript
    dangling: int = 0
    unattributed: int = 0


# --- #12 Knowledge coverage-gap report --------------------------------------


class CoverageGap(BaseModel):
    turn_index: int
    user_question: str = ""
    reason: str = "uncited-answer"  # zero-result-search / acknowledged-gap / uncited-answer
    query: str = ""


class CoverageGapAnalysis(BaseModel):
    gaps: list[CoverageGap] = Field(default_factory=list)


# --- #16 Turn-economy -------------------------------------------------------


class TurnEconomy(BaseModel):
    turns: int = 0
    user_turns: int = 0
    tool_calls: int = 0
    calls_per_answer: float = 0.0
    searches_to_first_answer: int = 0
    avg_bot_msgs_per_turn: float = 0.0


# --- D · Code interpreter / sandbox activity (D1, D2, D3) --------------------


class SandboxSignal(BaseModel):
    turn_index: int
    category: str  # read-document / preprocess / inspect-fs / permissions / shell-other
    tool: str = ""  # bash / grep / view / python / sudo / ...
    excerpt: str = ""


class SandboxFriction(BaseModel):
    turn_index: int
    kind: str  # permission-denied / retry / alternative-approach
    excerpt: str = ""
    recovered: bool = False


class SkillUse(BaseModel):
    turn_index: int | None = None
    name: str  # e.g. analyzing-docx
    category: str = "other"  # document-processing / other
    note: str = ""


class SkillGap(BaseModel):
    """G3 — the agent wanted a skill that wasn't available and fell back to raw code."""

    turn_index: int
    wanted: str = ""  # e.g. creating-pptx / a deck skill
    fallback: str = ""  # e.g. python (python-pptx)
    excerpt: str = ""


class CodeInterpreterAnalysis(BaseModel):
    signals: list[SandboxSignal] = Field(default_factory=list)
    friction: list[SandboxFriction] = Field(default_factory=list)
    skills: list[SkillUse] = Field(default_factory=list)
    skill_gaps: list[SkillGap] = Field(default_factory=list)  # G3
    turns_with_code: int = 0
    distinct_tools: list[str] = Field(default_factory=list)
    friction_count: int = 0
    document_processing_skills: int = 0
    authoring_turns: list[int] = Field(default_factory=list)  # G2 — turns generating/authoring files
    analysis_turns: list[int] = Field(default_factory=list)  # G2 — turns reading/preprocessing docs
    used: bool = False


# --- G1 · Generated file artifacts -----------------------------------------


class GeneratedArtifact(BaseModel):
    turn_index: int | None = None
    name: str = ""  # HR_Onboarding_Policies.pptx
    file_type: str = ""  # pptx / docx / xlsx ...
    content_type: str = ""
    how_made: str = "unknown"  # python / skill / unknown
    evidence: str = ""


class GeneratedArtifacts(BaseModel):
    items: list[GeneratedArtifact] = Field(default_factory=list)
    count: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)


# --- G4 · Document grounding pipeline ---------------------------------------


class GroundingDoc(BaseModel):
    title: str = ""
    url: str | None = None
    reference_id: str | None = None
    searched: bool = False  # returned by a KnowledgeSearch
    downloaded: bool = False  # full file pushed to the sandbox
    preprocessed: bool = False  # converted (e.g. docx -> converted.md) in the sandbox
    read_full: bool = False  # agent read/greped the full file
    cited: bool = False  # referenced by a [n] marker in an answer


class GroundingPipeline(BaseModel):
    snippet_mode: str = "unknown"  # stub / content / mixed — does search return real text or a download notice?
    span_visibility: str = "unknown"  # document-level / span-level — can we tell which passage was used?
    docs: list[GroundingDoc] = Field(default_factory=list)
    stub_results: int = 0  # number of retrieved docs whose "snippet" was only a sandbox-download notice
    content_results: int = 0  # retrieved docs that carried real snippet text
    notes: list[str] = Field(default_factory=list)


# --- B · Knowledge retrieval depth (B1, B2, B3, B4) -------------------------


class KnowledgeFolder(BaseModel):
    path: str  # e.g. Recruitment-and-Onboarding/Hiring
    area: str  # top-level area, e.g. Recruitment-and-Onboarding
    count: int = 0  # distinct docs retrieved from this folder
    doc_titles: list[str] = Field(default_factory=list)


class DocRetrieval(BaseModel):
    reference_id: str | None = None
    title: str = ""
    retrieval_count: int = 0  # how many searches returned this doc
    turns: list[int] = Field(default_factory=list)
    cited: bool = False


class RetrievalDepth(BaseModel):
    folders: list[KnowledgeFolder] = Field(default_factory=list)
    doc_retrievals: list[DocRetrieval] = Field(default_factory=list)
    total_retrieved: int = 0  # sum across searches (with duplicates)
    unique_docs: int = 0
    overlap_docs: int = 0  # docs returned by more than one search
    cited_docs: int = 0
    over_retrieval_ratio: float = 0.0  # 1 - cited/unique
    retrieval_mode: str = "inline"  # inline / snippet+sandbox
    full_doc_reads: int = 0  # turns where the agent read the full sandbox file


# --- A · Search & query strategy (A1, A2) -----------------------------------


class RecallTurn(BaseModel):
    turn_index: int
    excerpt: str = ""


class SearchPrecision(BaseModel):
    turn_index: int
    query: str = ""
    retrieved: int = 0
    cited_from_search: int = 0  # docs from THIS search that ended up cited
    productive: bool = False  # at least one retrieved doc was cited


class SearchStrategy(BaseModel):
    recall_turns: list[RecallTurn] = Field(default_factory=list)  # answered from prior retrieval, no new search
    searches: list[SearchPrecision] = Field(default_factory=list)
    productive_searches: int = 0
    unproductive_searches: int = 0


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
    # New analysis features
    tool_failures: ToolFailureAnalysis | None = None  # #10
    tool_efficiency: ToolEfficiency | None = None  # #6
    repetition: RepetitionAnalysis | None = None  # #5
    answer_groundedness: AnswerGroundednessAnalysis | None = None  # #2
    quote_faithfulness: QuoteFaithfulness | None = None  # #11
    coverage_gaps: CoverageGapAnalysis | None = None  # #12
    turn_economy: TurnEconomy | None = None  # #16
    # Modern-agent deep-analysis features
    code_interpreter: CodeInterpreterAnalysis | None = None  # D1, D2, D3
    retrieval_depth: RetrievalDepth | None = None  # B1, B2, B3, B4
    search_strategy: SearchStrategy | None = None  # A1, A2
    generated_artifacts: GeneratedArtifacts | None = None  # G1
    grounding_pipeline: GroundingPipeline | None = None  # G4
    findings: list[Finding] = Field(default_factory=list)
