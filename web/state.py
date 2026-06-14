"""Reflex state: upload, parse, analyse, and expose structured view-models."""

import json

import reflex as rx
from loguru import logger

from agent_parser import parse_agent_yaml_text
from analysis import analyze
from models import AgentProfile, Conversation
from renderer import build_standalone_html, render_markdown
from transcript_parser import parse_transcript_text
from web.view_models import (
    ChatBlockVM,
    CheckVM,
    CitationRowVM,
    ComponentVM,
    CreditKindVM,
    CreditLineVM,
    DocVM,
    EnvVarVM,
    FindingVM,
    KnowledgeQueryVM,
    KSourceVM,
    SourceEffVM,
    ToolRowVM,
    TurnVM,
    map_report,
)

# Tab key -> label
TAB_DEFS: list[tuple[str, str]] = [
    ("overview", "Overview"),
    ("agent", "Agent"),
    ("conversation", "Conversation"),
    ("tools", "Tools & Actions"),
    ("reasoning", "Reasoning"),
    ("quality", "Quality"),
    ("components", "Components"),
]

_FILTERS = ("all", "critical", "warning", "info")


class State(rx.State):
    # Raw uploaded payloads
    transcript_text: str = ""
    agent_text: str = ""
    transcript_name: str = ""
    agent_name: str = ""

    # Status
    error: str = ""
    status: str = ""
    has_report: bool = False
    active_tab: str = "overview"

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
    skill_loads: list[str] = []
    retry_signals: list[str] = []
    tool_failures: list[str] = []

    # Knowledge
    knowledge_queries: list[KnowledgeQueryVM] = []
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

    # Component explorer (#8)
    components: list[ComponentVM] = []

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
    raw_transcript: str = ""

    # --- Interactive UI state ---
    finding_filter: str = "all"
    transcript_query: str = ""
    active_citation: str = ""
    raw_open: bool = False
    show_thoughts: bool = True
    component_query: str = ""
    active_component: str = ""

    # ------------------------------------------------------------------
    # Derived upload state
    # ------------------------------------------------------------------
    @rx.var
    def has_transcript(self) -> bool:
        return bool(self.transcript_text)

    @rx.var
    def has_agent(self) -> bool:
        return bool(self.agent_text)

    @rx.var
    def can_analyse(self) -> bool:
        return bool(self.transcript_text or self.agent_text)

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

    @rx.var
    def grounded_total(self) -> int:
        return self.grounded + self.ungrounded

    @rx.var
    def filtered_components(self) -> list[ComponentVM]:
        q = self.component_query.strip().lower()
        if not q:
            return self.components
        return [c for c in self.components if q in c.search_text]

    @rx.var
    def component_count(self) -> int:
        return len(self.filtered_components)

    @rx.var
    def selected_component(self) -> ComponentVM:
        comps = self.components
        if not comps:
            return ComponentVM()
        for c in comps:
            if c.id == self.active_component:
                return c
        fc = self.filtered_components
        return fc[0] if fc else comps[0]

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

    def toggle_citation(self, rid: str):
        self.active_citation = "" if self.active_citation == rid else rid

    def toggle_raw(self):
        self.raw_open = not self.raw_open

    def toggle_thoughts(self):
        self.show_thoughts = not self.show_thoughts

    def set_component_query(self, q: str):
        self.component_query = q

    def clear_component_query(self):
        self.component_query = ""

    def select_component(self, cid: str):
        self.active_component = cid

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
            return "transcript" if isinstance(obj, list) else ""
        except (ValueError, TypeError):
            return "agent" if (":" in text and "{" not in text[:80]) else ""

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
                self.transcript_text, self.transcript_name = text, name
            elif kind == "agent":
                self.agent_text, self.agent_name = text, name
            else:
                self.error = f"{name}: could not tell if this is a transcript or an agent YAML."
                continue
            logger.info(f"Uploaded {name} -> {kind}")
        self._set_status()

    def _set_status(self):
        bits = []
        if self.transcript_text:
            bits.append(f"transcript ({self.transcript_name})")
        if self.agent_text:
            bits.append(f"agent ({self.agent_name})")
        self.status = "Loaded: " + ", ".join(bits) if bits else ""

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def run_analysis(self):
        self.error = ""
        if not (self.transcript_text or self.agent_text):
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
            if self.transcript_text:
                convo = parse_transcript_text(self.transcript_text)
        except Exception as exc:  # noqa: BLE001
            self.error = f"Transcript parse failed: {exc}"
            logger.error(self.error)
            return

        report = analyze(profile, convo)
        self.full_md = render_markdown(report, convo)
        self._apply_vm(map_report(report, convo, raw_transcript=self.transcript_text))

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
        self.skill_loads = vm.skill_loads
        self.retry_signals = vm.retry_signals
        self.tool_failures = vm.tool_failures

        self.knowledge_queries = vm.knowledge_queries
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

        self.components = vm.components

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
        self.turns = vm.turns
        self.mermaid = vm.mermaid
        self.raw_transcript = vm.raw_transcript

    # ------------------------------------------------------------------
    # Samples
    # ------------------------------------------------------------------
    def load_sample(self, kind: str = "knowledge"):
        """Load a bundled sample. `knowledge` = agent YAML + transcript;
        `agentic` = transcript only (autonomous Teams agent)."""
        self.error = ""
        self.agent_text = self.agent_name = ""
        try:
            if kind == "agentic":
                with open("samples/sample_transcript_agentic.json", encoding="utf-8") as fh:
                    self.transcript_text = fh.read()
                    self.transcript_name = "sample_transcript_agentic.json"
            else:
                with open("samples/sample_agent.yaml", encoding="utf-8") as fh:
                    self.agent_text = fh.read()
                    self.agent_name = "sample_agent.yaml"
                with open("samples/sample_transcript.json", encoding="utf-8") as fh:
                    self.transcript_text = fh.read()
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

    def print_report(self):
        return rx.call_script("window.print()")

    def clear_all(self):
        self.transcript_text = self.agent_text = ""
        self.transcript_name = self.agent_name = ""
        self.full_md = ""
        self.has_report = False
        self.error = self.status = ""
        self.active_tab = "overview"
        self.finding_filter = "all"
        self.transcript_query = ""
        self.active_citation = ""
        self.raw_open = False
        self.component_query = ""
        self.active_component = ""
        return rx.clear_selected_files("upload")


# Re-export for tooling/tests
__all__ = ["State", "TAB_DEFS"]
