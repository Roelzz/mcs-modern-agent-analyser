"""Render an `AnalysisReport` (+ optional `Conversation`) to Markdown with a
Mermaid sequence diagram. The Reflex web layer reuses these same section
builders, so keep them pure (string in, string out)."""

from __future__ import annotations

import html

from models import AnalysisReport, Conversation

_SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
_STATUS_ICON = {"pass": "✅", "fail": "❌", "unknown": "❔"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cell(text: str | None) -> str:
    """Make a value safe for a Markdown table cell."""
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _yn(value: bool) -> str:
    return "yes" if value else "—"


def _seq(text: str, limit: int = 70) -> str:
    """Sanitise text for a Mermaid sequenceDiagram message (single line)."""
    t = " ".join((text or "").split())
    t = t.replace(";", ",").replace("#", "＃").replace("<", "‹").replace(">", "›")
    if len(t) > limit:
        t = t[: limit - 1] + "…"
    return t or "…"


# ---------------------------------------------------------------------------
# Sequence diagram
# ---------------------------------------------------------------------------


def render_sequence_diagram(convo: Conversation, agent_name: str = "Agent") -> str:
    if not convo.turns:
        return ""
    has_search = any(tc.is_knowledge_search for tc in convo.tool_calls)
    other_tools = any(not tc.is_knowledge_search for tc in convo.tool_calls)

    lines = ["```mermaid", "sequenceDiagram", "    participant U as User", "    participant A as Agent"]
    if has_search:
        lines.append("    participant K as Knowledge")
    if other_tools:
        lines.append("    participant T as Tools")

    for turn in convo.turns:
        if turn.user_message is not None:
            lines.append(f"    U->>A: {_seq(turn.user_message.text)}")
        else:
            lines.append("    Note over A: session start")

        for tc in turn.tool_calls:
            if tc.is_knowledge_search:
                lines.append(f"    A->>K: search {_seq(tc.query or '', 50)}")
                if tc.zero_result:
                    lines.append("    K--xA: no results")
                else:
                    n = tc.result_count if tc.result_count is not None else len(tc.retrieved_docs)
                    lines.append(f"    K-->>A: {n} result(s)")
            else:
                arrow = "T--xA" if tc.failed else "T-->>A"
                lines.append(f"    A->>T: {_seq(tc.name or 'tool', 40)}")
                lines.append(f"    {arrow}: {tc.status or 'done'}")

        intermediate = [m for m in turn.bot_messages if m.text.strip()][:-1]
        if intermediate:
            lines.append(f"    Note over A,U: {len(intermediate)} intermediate message(s)")
        if turn.final_bot_text.strip():
            lines.append(f"    A->>U: {_seq(turn.final_bot_text)}")

    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def render_agent_profile(report: AnalysisReport) -> str:
    p = report.agent
    if p is None:
        return ""
    rows = [
        ("Display name", p.display_name),
        ("Model", f"{p.model_label or p.model_series or 'Unknown'}"),
        ("Template", p.template),
        ("Recognizer", p.recognizer_kind),
        ("Memory enabled", "Yes" if p.enable_memory else "No"),
        ("Authentication", p.authentication_mode),
        ("Knowledge sources", str(len(p.knowledge_sources))),
        ("Environment variables", str(len(p.environment_variables))),
        ("Tool components", str(len(p.tool_components))),
        ("Last modified", p.modified_at),
    ]
    out = ["## Agent profile", "", "| Property | Value |", "| --- | --- |"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows if v not in (None, "")]

    if p.instructions.strip():
        out += ["", "**Instructions**", "", "> " + p.instructions.replace("\n", "\n> ")]

    if p.knowledge_sources:
        out += ["", "**Knowledge sources**", "", "| Name | Kind | Location | State |", "| --- | --- | --- | --- |"]
        for ks in p.knowledge_sources:
            out.append(
                f"| {_cell(ks.display_name)} | {_cell(ks.source_kind)} | {_cell(ks.source_site)} | {_cell(ks.state)} |"
            )

    if p.environment_variables:
        out += [
            "",
            "**Environment variables**",
            "",
            "| Name | Type | Default |",
            "| --- | --- | --- |",
        ]
        for ev in p.environment_variables:
            out.append(f"| {_cell(ev.display_name)} | {_cell(ev.type)} | {_cell(ev.default_value)} |")
    return "\n".join(out)


def render_findings(report: AnalysisReport) -> str:
    if not report.findings:
        return ""
    out = ["## Findings", "", "| | Category | Finding | Detail |", "| --- | --- | --- | --- |"]
    for f in report.findings:
        icon = _SEVERITY_ICON.get(f.severity, "•")
        out.append(f"| {icon} | {_cell(f.category)} | {_cell(f.title)} | {_cell(f.detail)} |")
    return "\n".join(out)


def render_overview(report: AnalysisReport) -> str:
    o = report.overview
    if o is None:
        return ""
    rows = [
        ("Turns", o.turn_count),
        ("User messages", o.user_message_count),
        ("Bot messages", o.bot_message_count),
        ("Tool calls", o.tool_call_count),
        ("Knowledge searches", o.knowledge_search_count),
        ("Reasoning blocks", o.thought_count),
        ("Failed tool calls", o.failed_tool_count),
        ("Zero-result searches", o.zero_result_search_count),
    ]
    out = ["## Conversation overview", "", "| Metric | Value |", "| --- | --- |"]
    out += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(out)


def render_tools(report: AnalysisReport) -> str:
    t = report.tools
    if t is None or (not t.usage and not t.skill_loads and not t.retry_signals):
        return ""
    out = ["## Tools", ""]
    if t.usage:
        out += ["| Tool | Calls | Completed | Failed |", "| --- | --- | --- | --- |"]
        out += [f"| {_cell(u.name)} | {u.count} | {u.completed} | {u.failed} |" for u in t.usage]
    if t.skill_loads:
        out += ["", "**Skills loaded:** " + ", ".join(_cell(s) for s in t.skill_loads)]
    if t.failures:
        out += ["", "**Failures:**"] + [f"- {_cell(f)}" for f in t.failures]
    if t.retry_signals:
        out += ["", "**Retry / approach changes:**"] + [f"- {_cell(s)}" for s in t.retry_signals]
    return "\n".join(out)


def render_knowledge(report: AnalysisReport) -> str:
    k = report.knowledge
    if k is None or not k.queries:
        return ""
    out = ["## Knowledge", "", "**Searches**", "", "| Query | Results | Zero-result |", "| --- | --- | --- |"]
    for q in k.queries:
        out.append(f"| {_cell(q.query)} | {q.result_count} | {'⚠️ yes' if q.zero_result else 'no'} |")

    if k.distinct_docs:
        out += ["", "**Documents retrieved**", "", "| Title | Reference | Used in answer |", "| --- | --- | --- |"]
        uncited_keys = {d.reference_id or d.title for d in k.uncited_docs}
        for d in k.distinct_docs:
            used = "no" if (d.reference_id or d.title) in uncited_keys else "yes"
            out.append(f"| {_cell(d.title)} | {_cell(d.reference_id)} | {used} |")

    if k.sources_seen:
        out += ["", "**Source locations:** " + ", ".join(_cell(s) for s in k.sources_seen)]
    return "\n".join(out)


def render_reasoning(report: AnalysisReport) -> str:
    r = report.reasoning
    if r is None or r.total_thoughts == 0 and not r.premise_corrections:
        return ""
    out = ["## Reasoning", "", f"- **Reasoning blocks:** {r.total_thoughts}"]
    if r.thoughts_per_turn:
        out.append(f"- **Per turn:** {', '.join(str(n) for n in r.thoughts_per_turn)}")
    if r.retry_signals:
        out += ["", "**Retry / confusion signals:**"] + [f"- {_cell(s)}" for s in r.retry_signals]
    if r.premise_corrections:
        out += ["", "**Premise corrections (agent corrected the user):**"] + [
            f"- {_cell(s)}" for s in r.premise_corrections
        ]
    return "\n".join(out)


def render_quality(report: AnalysisReport) -> str:
    g = report.groundedness
    if g is None:
        return ""
    out = [
        "## Quality & groundedness",
        "",
        f"- **Grounded answers:** {g.grounded_answers}",
        f"- **Ungrounded answers:** {g.ungrounded_answers}",
    ]
    if g.hallucination_risk:
        out += ["", "**Hallucination risk:**"] + [f"- 🔴 {_cell(h)}" for h in g.hallucination_risk]
    if g.honest_grounding:
        out += ["", "**Honest knowledge gaps (good):**"] + [f"- ✅ {_cell(h)}" for h in g.honest_grounding]
    if g.notes:
        out += ["", "**Notes:**"] + [f"- {_cell(n)}" for n in g.notes]
    return "\n".join(out)


def render_instructions(report: AnalysisReport) -> str:
    ic = report.instructions
    if ic is None or not ic.checks:
        return ""
    out = ["## Instruction compliance", "", "| | Instruction | Check | Evidence |", "| --- | --- | --- | --- |"]
    for c in ic.checks:
        icon = _STATUS_ICON.get(c.status, "❔")
        out.append(f"| {icon} | {_cell(c.instruction)} | {_cell(c.check)} | {_cell(c.evidence)} |")
    return "\n".join(out)


def render_cross_reference(report: AnalysisReport) -> str:
    x = report.cross_reference
    if x is None:
        return ""
    bits: list[str] = []
    if x.model_in_use:
        bits.append(f"- **Model in use:** {_cell(x.model_in_use)}")
    if x.defined_knowledge_sources:
        bits.append(f"- **Defined knowledge sources:** {', '.join(_cell(s) for s in x.defined_knowledge_sources)}")
    if x.contributing_knowledge_sources:
        bits.append(
            f"- **Contributed in conversation:** {', '.join(_cell(s) for s in x.contributing_knowledge_sources)}"
        )
    if x.unused_knowledge_sources:
        bits.append(f"- **⚠️ Never contributed:** {', '.join(_cell(s) for s in x.unused_knowledge_sources)}")
    if x.tools_used_not_defined:
        bits.append(f"- **Tools used but not defined:** {', '.join(_cell(s) for s in x.tools_used_not_defined)}")
    if not bits:
        return ""
    return "## Cross-reference\n\n" + "\n".join(bits)


def render_knowledge_effectiveness(report: AnalysisReport) -> str:
    eff = report.knowledge_effectiveness
    if eff is None or not eff.sources:
        return ""
    out = [
        "## Knowledge source effectiveness",
        "",
        f"- **Searches:** {eff.total_searches}  |  **Distinct docs:** {eff.distinct_docs}  "
        f"|  **Avg docs/search:** {eff.avg_docs_per_search:g}  |  **Unattributed docs:** {eff.unattributed_docs}",
        "",
        "| Source | Kind | Retrieved | Cited | Contribution | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for s in eff.sources:
        if s.never_retrieved:
            status = "🔴 dead"
        elif s.zero_contribution:
            status = "🟠 no citations"
        elif not s.configured:
            status = "🔵 runtime-observed"
        else:
            status = "✅ active"
        out.append(
            f"| {_cell(s.display_name)} | {_cell(s.source_kind)} | {s.docs_retrieved} | {s.docs_cited} "
            f"| {int(round(s.contribution_rate * 100))}% | {status} |"
        )
    return "\n".join(out)


def render_citation_audit(report: AnalysisReport) -> str:
    audit = report.citation_audit
    if audit is None or not audit.rows:
        return ""
    rank = {"dangling": 0, "resolved": 1, "uncited_retrieval": 2}
    label = {"resolved": "✅ resolved", "dangling": "❌ dangling", "uncited_retrieval": "🟠 uncited retrieval"}
    out = [
        "## Citation verification",
        "",
        f"- **Resolved:** {audit.resolved}  |  **Dangling:** {audit.dangling}  "
        f"|  **Uncited retrievals:** {audit.uncited_retrievals}",
        "",
        "| Marker | Status | Document | Turn | Provenance |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in sorted(audit.rows, key=lambda r: (rank.get(r.status, 3), r.turn_index or 0)):
        turn = str(r.turn_index) if r.turn_index is not None else ""
        out.append(
            f"| {_cell(r.marker)} | {label.get(r.status, r.status)} | {_cell(r.doc_title)} "
            f"| {turn} | {_cell(r.provenance)} |"
        )
    return "\n".join(out)


def render_credits(report: AnalysisReport) -> str:
    est = report.credit_estimate
    if est is None or not est.line_items:
        return ""
    out = [
        "## Credit estimate",
        "",
        f"**Estimated total: {est.total_credits:g} Copilot Credits**",
        "",
    ]
    if est.reasoning_model:
        out.append(f"> Reasoning model — premium token meter applies (≈{est.total_tokens} tokens estimated).")
        out.append("")
    if est.by_kind:
        out.append(
            "- " + "  |  ".join(f"**{k.replace('_', ' ')}:** {v:g}" for k, v in sorted(est.by_kind.items()))
        )
        out.append("")
    out += ["| Step | Kind | Credits | Detail |", "| --- | --- | --- | --- |"]
    for it in est.line_items:
        out.append(f"| {_cell(it.label)} | {_cell(it.kind.replace('_', ' '))} | {it.credits:g} | {_cell(it.detail)} |")
    if est.assumptions:
        out += ["", "**Assumptions:**"]
        out += [f"- {_cell(a)}" for a in est.assumptions]
    if est.notes:
        out += ["", "> " + "  \n> ".join(_cell(n) for n in est.notes)]
    return "\n".join(out)


def render_sandbox(report: AnalysisReport) -> str:
    ci = report.code_interpreter
    if ci is None or not ci.used:
        return ""
    out = [
        "## Sandbox & code interpreter",
        "",
        f"Code-interpreter activity detected in **{ci.turns_with_code} turn(s)**"
        + (f"; tools observed: {', '.join(ci.distinct_tools)}." if ci.distinct_tools else "."),
        "",
    ]
    if ci.authoring_turns or ci.analysis_turns:
        auth = ", ".join(str(t) for t in ci.authoring_turns) or "—"
        anal = ", ".join(str(t) for t in ci.analysis_turns) or "—"
        out += [
            f"- Authoring (generated files): turn **{auth}**",
            f"- Analysis (read documents): turn **{anal}**",
            "",
        ]
    if ci.skill_gaps:
        out += ["**Skill gaps → code fallback:**"]
        for g in ci.skill_gaps:
            wanted = f"wanted `{_cell(g.wanted)}`" if g.wanted else "no matching skill"
            fb = f" → fell back to {_cell(g.fallback)}" if g.fallback else " → fell back to raw code"
            out.append(f"- Turn {g.turn_index}: {wanted}{fb} — {_cell(g.excerpt)}")
        out.append("")
    if ci.skills:
        out += ["**Skills used:**"]
        for s in ci.skills:
            label = "document processing" if s.category == "document-processing" else "skill"
            turn = f" (turn {s.turn_index})" if s.turn_index is not None else ""
            out.append(f"- `{_cell(s.name)}` — {label}{turn}")
        out.append("")
    if ci.friction:
        out += [f"**Sandbox friction ({ci.friction_count}):**"]
        for f in ci.friction:
            tag = "recovered" if f.recovered else "unresolved"
            out.append(f"- Turn {f.turn_index} — {_cell(f.kind)} ({tag}): {_cell(f.excerpt)}")
        out.append("")
    if ci.signals:
        out += ["| Turn | Activity | Tool | Evidence |", "| --- | --- | --- | --- |"]
        for s in ci.signals:
            out.append(f"| {s.turn_index} | {_cell(s.category)} | {_cell(s.tool)} | {_cell(s.excerpt)} |")
    return "\n".join(out)


def render_retrieval_depth(report: AnalysisReport) -> str:
    rd = report.retrieval_depth
    if rd is None or not (rd.folders or rd.doc_retrievals):
        return ""
    out = [
        "## Retrieval depth",
        "",
        f"- Retrieval mode: **{rd.retrieval_mode}**",
        f"- Unique documents: **{rd.unique_docs}** (from {rd.total_retrieved} retrievals; "
        f"{rd.overlap_docs} returned by more than one search)",
        f"- Cited: **{rd.cited_docs}** of {rd.unique_docs} — over-retrieval "
        f"{int(rd.over_retrieval_ratio * 100)}%",
        f"- Full-document sandbox reads: **{rd.full_doc_reads}**",
        "",
    ]
    if rd.folders:
        out += ["**Document taxonomy (SharePoint folders):**", "", "| Folder | Docs |", "| --- | --- |"]
        for f in rd.folders:
            out.append(f"| {_cell(f.path)} | {f.count} |")
        out.append("")
    if rd.doc_retrievals:
        out += ["**Most-retrieved documents:**", "", "| Document | Retrievals | Turns | Cited |", "| --- | --- | --- | --- |"]
        for d in rd.doc_retrievals:
            turns = ", ".join(str(t) for t in d.turns)
            out.append(f"| {_cell(d.title)} | {d.retrieval_count} | {_cell(turns)} | {'yes' if d.cited else 'no'} |")
    return "\n".join(out)


def render_search_strategy(report: AnalysisReport) -> str:
    ss = report.search_strategy
    if ss is None or not (ss.searches or ss.recall_turns):
        return ""
    out = [
        "## Search strategy",
        "",
        f"- Productive searches: **{ss.productive_searches}**  |  Unproductive: **{ss.unproductive_searches}**",
        f"- Turns answered from earlier retrieval (no new search): **{len(ss.recall_turns)}**",
        "",
    ]
    if ss.searches:
        out += ["| Turn | Query | Retrieved | Cited | Productive |", "| --- | --- | --- | --- | --- |"]
        for s in ss.searches:
            out.append(
                f"| {s.turn_index} | {_cell(s.query)} | {s.retrieved} | {s.cited_from_search} | "
                f"{'yes' if s.productive else 'no'} |"
            )
        out.append("")
    if ss.recall_turns:
        out += ["**Answered from earlier retrieval:**"]
        for t in ss.recall_turns:
            out.append(f"- Turn {t.turn_index}: {_cell(t.excerpt)}")
    return "\n".join(out)


def render_generated_artifacts(report: AnalysisReport) -> str:
    ga = report.generated_artifacts
    if ga is None or not ga.items:
        return ""
    kinds = ", ".join(f"{v}× {k}" for k, v in ga.by_type.items())
    out = [
        "## Generated outputs",
        "",
        f"The agent produced **{ga.count} downloadable file(s)** ({kinds}). Verify each is grounded in the "
        "cited sources.",
        "",
        "| File | Type | Produced with | Turn |",
        "| --- | --- | --- | --- |",
    ]
    for it in ga.items:
        turn = str(it.turn_index) if it.turn_index is not None else "—"
        out.append(f"| {_cell(it.name)} | {_cell(it.file_type)} | {_cell(it.how_made)} | {turn} |")
    return "\n".join(out)


def render_grounding_pipeline(report: AnalysisReport) -> str:
    gp = report.grounding_pipeline
    if gp is None or not gp.docs:
        return ""
    out = [
        "## Grounding pipeline",
        "",
        f"- Snippet mode: **{gp.snippet_mode}** ({gp.stub_results} download stub(s), "
        f"{gp.content_results} with inline text)",
        f"- Citation precision: **{gp.span_visibility}**",
        "",
    ]
    for n in gp.notes:
        out.append(f"> {_cell(n)}")
    if gp.notes:
        out.append("")
    out += ["| Document | Searched | Downloaded | Preprocessed | Read | Cited |", "| --- | --- | --- | --- | --- | --- |"]
    for d in gp.docs:
        out.append(
            f"| {_cell(d.title)} | {_yn(d.searched)} | {_yn(d.downloaded)} | {_yn(d.preprocessed)} | "
            f"{_yn(d.read_full)} | {_yn(d.cited)} |"
        )
    return "\n".join(out)


def render_components(report: AnalysisReport, convo: Conversation | None = None) -> str:
    from analysis import PROVIDER_META, build_tool_hierarchy
    from explainer import explain

    p = report.agent
    out: list[str] = []

    def line(label: str, value: str, key: str, kvalue: str | None = None) -> None:
        ex = explain(key, kvalue)
        ref = f" ([Learn]({ex.doc}))" if ex.doc else ""
        val = f" — `{value}`" if value else ""
        out.append(f"- **{label}**{val} — {ex.summary}{ref}")

    if p is not None:
        agent_rows: list[tuple[str, str, str, str | None]] = []
        if p.model_label or p.model_series:
            agent_rows.append(("Model", p.model_label or p.model_series or "", "model", None))
        if p.is_modern:
            agent_rows.append(("Orchestration", "Generative orchestration", "orchestration", None))
        if p.instructions:
            agent_rows.append(("Instructions", f"{len(p.instruction_segments) or 1} segment(s)", "instructions", None))
        if p.authentication_mode:
            agent_rows.append(("Authentication mode", p.authentication_mode, "authenticationMode", p.authentication_mode))
        if p.authentication_trigger:
            agent_rows.append(
                ("Authentication trigger", p.authentication_trigger, "authenticationTrigger", p.authentication_trigger)
            )
        if p.access_control_policy:
            agent_rows.append(("Access control", p.access_control_policy, "accessControlPolicy", p.access_control_policy))
        agent_rows.append(("Memory", "Enabled" if p.enable_memory else "Disabled", "enableMemory", None))
        if p.conversation_starters:
            agent_rows.append(
                ("Conversation starters", f"{len(p.conversation_starters)} starter(s)", "conversationStarters", None)
            )
        if p.recognizer_kind:
            agent_rows.append(("Recognizer", p.recognizer_kind, "recognizer", None))
        if p.template:
            agent_rows.append(("Template", p.template, "template", None))
        if p.runtime_provider:
            agent_rows.append(("Runtime provider", p.runtime_provider, "runtimeProvider", None))
        if agent_rows:
            out.append("### Agent")
            for label, value, key, kvalue in agent_rows:
                line(label, value, key, kvalue)
            out.append("")

        if p.knowledge_sources:
            out.append("### Knowledge sources")
            for ks in p.knowledge_sources:
                specific = f"knowledge.{ks.source_kind}" if ks.source_kind else "knowledge"
                ex = explain(specific)
                if not ex.documented:
                    ex = explain("knowledge")
                ref = f" ([Learn]({ex.doc}))" if ex.doc else ""
                kind = f" — `{ks.source_kind}`" if ks.source_kind else ""
                out.append(f"- **{ks.display_name or '(knowledge source)'}**{kind} — {ex.summary}{ref}")
            out.append("")

    providers = build_tool_hierarchy(p, convo)
    if providers:
        out.append("### Tools")
        for pr in providers:
            badge, _icon, kbkey = PROVIDER_META.get(pr.kind, ("Tool", "wrench", "tool"))
            pex = explain(kbkey)
            ref = f" ([Learn]({pex.doc}))" if pex.doc else ""
            origin = "declared in agent" if pr.configured else "observed at runtime"
            src = f" · {pr.source}" if pr.source else ""
            out.append(
                f"- **{badge}: {pr.display_name}** "
                f"({len(pr.operations)} operation(s), {origin}{src}) — {pex.summary}{ref}"
            )
            for op in pr.operations:
                olabel = op.display_name or op.name
                desc = f" — {op.description}" if op.description else ""
                out.append(f"  - `{olabel}`{desc}")
        out.append("")

    if p is not None and p.environment_variables:
        out.append("### Environment variables")
        for ev in p.environment_variables:
            label = ev.display_name or ev.schema_name or "(env var)"
            value = ev.type or (ev.default_value or "")
            line(label, value, "environmentVariable")
        out.append("")

    if not out:
        return ""
    return "## Components\n\n" + "\n".join(out).rstrip()


def render_conversation_flow(convo: Conversation | None, agent_name: str = "Agent") -> str:
    if convo is None or not convo.turns:
        return ""
    diagram = render_sequence_diagram(convo, agent_name)
    return "## Conversation flow\n\n" + diagram if diagram else ""


def render_chat(convo: Conversation | None) -> str:
    """Web-safe markdown transcript (no raw HTML): user/agent turns with inline
    tool calls and reasoning."""
    if convo is None or not convo.messages:
        return ""
    out = ["## Transcript", ""]
    for m in convo.messages:
        if m.is_user:
            out += [f"**🧑 User:** {_cell(m.text)}", ""]
            continue
        if m.text.strip():
            out += [f"**🤖 Agent:** {m.text.strip()}", ""]
        for th in m.thoughts:
            if th.text.strip():
                out.append(f"> 💭 _{_cell(th.text)}_")
        for tc in m.tool_calls:
            if tc.is_knowledge_search:
                n = tc.result_count if tc.result_count is not None else len(tc.retrieved_docs)
                detail = "no results" if tc.zero_result else f"{n} result(s)"
                out.append(f"> 🔎 **KnowledgeSearch** `{_cell(tc.query)}` → {detail}")
                for d in tc.retrieved_docs:
                    out.append(f">   - {_cell(d.title)} (`{_cell(d.reference_id)}`)")
            else:
                flag = "❌" if tc.failed else "✅"
                out.append(f"> 🔧 **{_cell(tc.name)}** {flag} {_cell(tc.display_name)}")
        if m.thoughts or m.tool_calls:
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def render_turn_economy(report: AnalysisReport) -> str:
    eco = report.turn_economy
    if eco is None or eco.turns == 0:
        return ""
    return "\n".join(
        [
            "## Turn economy",
            "",
            f"- **User turns:** {eco.user_turns}",
            f"- **Tool calls:** {eco.tool_calls}  |  **Calls per answer:** {eco.calls_per_answer:g}",
            f"- **Searches to first answer:** {eco.searches_to_first_answer}",
            f"- **Avg bot messages per turn:** {eco.avg_bot_msgs_per_turn:g}",
        ]
    )


def render_tool_failures(report: AnalysisReport) -> str:
    tf = report.tool_failures
    if tf is None or not tf.failures:
        return ""
    label = {
        "recovered-other-tool": "recovered via other tool",
        "retried-same": "retried same tool",
        "unhandled-but-answered": "answered without recovery",
        "gave-up": "gave up",
    }
    out = [
        "## Failed tools & recovery",
        "",
        f"- **Failures:** {tf.total_failures}  |  **Hidden behind a 'completed' status:** {tf.embedded_failures}  "
        f"|  **Recovered:** {tf.recovered}  |  **Gave up:** {tf.gave_up}",
        "",
        "| Turn | Tool | Error | Recovery | Next action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in tf.failures:
        out.append(
            f"| {f.turn_index} | {_cell(f.name)} | {_cell(f.error_text)} "
            f"| {label.get(f.recovery, f.recovery)} | {_cell(f.next_action)} |"
        )
    return "\n".join(out)


def render_tool_efficiency(report: AnalysisReport) -> str:
    eff = report.tool_efficiency
    if eff is None or eff.total_calls == 0:
        return ""
    out = [
        "## Tool-call efficiency",
        "",
        f"- **Total calls:** {eff.total_calls}  |  **Unique:** {eff.unique_calls}  "
        f"|  **Redundant:** {eff.redundant_calls}  |  **Calls per answer:** {eff.calls_per_answer:g}",
    ]
    if eff.duplicate_groups:
        out += ["", "| Tool | Repeats | Turns | Parameters |", "| --- | --- | --- | --- |"]
        for d in eff.duplicate_groups:
            turns = ", ".join(str(t) for t in d.turns)
            out.append(f"| {_cell(d.name)} | {d.count}× | {turns} | {_cell(d.params_summary)} |")
    else:
        out += ["", "_No redundant tool calls — every call used distinct parameters._"]
    return "\n".join(out)


def render_repetition(report: AnalysisReport) -> str:
    rep = report.repetition
    if rep is None or not rep.signals:
        return ""
    label = {"agent-answer": "Repeated answer", "agent-tool": "Tool loop", "user-question": "Repeated question"}
    out = [
        "## Repetition & loops",
        "",
        "| Kind | Turns | Similarity | Excerpt |",
        "| --- | --- | --- | --- |",
    ]
    for s in rep.signals:
        turns = ", ".join(str(t) for t in s.turns)
        out.append(
            f"| {label.get(s.kind, s.kind)} | {turns} | {int(round(s.similarity * 100))}% | {_cell(s.excerpt)} |"
        )
    return "\n".join(out)


def render_answer_grounding(report: AnalysisReport) -> str:
    ag = report.answer_groundedness
    if ag is None or not ag.answers:
        return ""
    rank = {"high": 0, "medium": 1, "low": 2}
    badge = {"high": "🔴 high", "medium": "🟠 medium", "low": "🟢 low"}
    out = [
        "## Per-answer groundedness",
        "",
        f"- **High risk:** {ag.high_risk}  |  **Medium:** {ag.medium_risk}  |  **Low:** {ag.low_risk}",
        "",
        "> High = factual claims with no citation despite a search returning documents. A heuristic signal, not a verdict.",
        "",
        "| Turn | Risk | Factual claims | Cited | Had retrieval |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in sorted(ag.answers, key=lambda a: rank.get(a.risk, 3)):
        out.append(
            f"| {a.turn_index} | {badge.get(a.risk, a.risk)} | {a.factual_claims} | {a.cited_claims} "
            f"| {'yes' if a.had_retrieval else 'no'} |"
        )
    return "\n".join(out)


def render_quote_traceability(report: AnalysisReport) -> str:
    qf = report.quote_faithfulness
    if qf is None or not qf.quotes:
        return ""
    rank = {"unattributed-quote": 0, "dangling-attribution": 1, "attributed-source-in-sandbox": 2, "verified-in-tool-output": 3}
    label = {
        "verified-in-tool-output": "✅ verified in tool output",
        "attributed-source-in-sandbox": "🔵 attributed — source in sandbox",
        "dangling-attribution": "❌ dangling attribution",
        "unattributed-quote": "🟠 unattributed quote",
    }
    out = [
        "## Quote traceability",
        "",
        f"- **Verified:** {qf.verified}  |  **In sandbox:** {qf.attributed}  "
        f"|  **Dangling:** {qf.dangling}  |  **Unattributed:** {qf.unattributed}",
        "",
        "> Modern RAG reads documents in a sandbox, so cited source text rarely reaches the transcript. "
        "'In sandbox' means the quote is attributed to a retrieved doc whose full text isn't transcript-verifiable.",
        "",
        "| Turn | Verdict | Source | Quote |",
        "| --- | --- | --- | --- |",
    ]
    for q in sorted(qf.quotes, key=lambda q: rank.get(q.verdict, 4)):
        out.append(
            f"| {q.turn_index} | {label.get(q.verdict, q.verdict)} | {_cell(q.source_title)} | {_cell(q.excerpt)} |"
        )
    return "\n".join(out)


def render_coverage_gaps(report: AnalysisReport) -> str:
    cg = report.coverage_gaps
    if cg is None or not cg.gaps:
        return ""
    label = {
        "zero-result-search": "🔴 zero-result search",
        "acknowledged-gap": "🟠 acknowledged gap",
        "uncited-answer": "🟠 uncited answer",
    }
    out = [
        "## Knowledge coverage gaps",
        "",
        "| Turn | Reason | User question | Query |",
        "| --- | --- | --- | --- |",
    ]
    for g in cg.gaps:
        out.append(
            f"| {g.turn_index} | {label.get(g.reason, g.reason)} | {_cell(g.user_question)} | {_cell(g.query)} |"
        )
    return "\n".join(out)


def render_timeline(convo: Conversation | None) -> str:
    if convo is None or not convo.turns:
        return ""
    from analysis import classify_tool_kind, tool_failed

    out = [
        "## Conversation timeline",
        "",
        "_Sequence of events per turn (no timestamps in modern transcripts — ordering only)._",
    ]
    for turn in convo.turns:
        title = "Greeting" if turn.user_message is None else f"Turn {turn.index}"
        out += ["", f"### {title}"]
        if turn.user_message is not None and turn.user_message.text.strip():
            out.append(f"- 👤 **User:** {_cell(_seq(turn.user_message.text, 120))}")
        for m in turn.bot_messages:
            for th in m.thoughts:
                if th.text.strip():
                    out.append(f"- 💭 **Thought:** {_cell(_seq(th.text, 120))}")
            for tc in m.tool_calls:
                mark = "❌" if tool_failed(tc) else "•"
                kind = classify_tool_kind(tc)
                name = tc.display_name or tc.name or "tool"
                detail = tc.query or ""
                suffix = f" — {_cell(_seq(detail, 80))}" if detail else ""
                out.append(f"- {mark} **{_cell(name)}** _({kind})_{suffix}")
            if m.text.strip():
                out.append(f"- 🤖 **Agent:** {_cell(_seq(m.text, 140))}")
    return "\n".join(out)


def render_markdown(report: AnalysisReport, convo: Conversation | None = None, title: str | None = None) -> str:
    name = report.agent.display_name if report.agent else "Modern agent"
    heading = title or f"Agent analysis — {name}"

    sections = [
        f"# {heading}",
        render_findings(report),
        render_agent_profile(report),
        render_overview(report),
        render_turn_economy(report),
        render_conversation_flow(convo, name),
        render_timeline(convo),
        render_tools(report),
        render_tool_failures(report),
        render_tool_efficiency(report),
        render_generated_artifacts(report),
        render_knowledge(report),
        render_knowledge_effectiveness(report),
        render_search_strategy(report),
        render_retrieval_depth(report),
        render_grounding_pipeline(report),
        render_coverage_gaps(report),
        render_reasoning(report),
        render_sandbox(report),
        render_quality(report),
        render_answer_grounding(report),
        render_quote_traceability(report),
        render_repetition(report),
        render_citation_audit(report),
        render_credits(report),
        render_instructions(report),
        render_cross_reference(report),
        render_components(report, convo),
    ]
    return "\n\n".join(s for s in sections if s.strip()) + "\n"


def build_sections(report: AnalysisReport, convo: Conversation | None = None) -> dict[str, str]:
    """Per-tab markdown sections for the web UI. Keys map to UI tabs."""
    name = report.agent.display_name if report.agent else "Modern agent"
    return {
        "findings": render_findings(report),
        "overview": render_overview(report),
        "turn_economy": render_turn_economy(report),
        "profile": render_agent_profile(report),
        "cross_reference": render_cross_reference(report),
        "flow": render_conversation_flow(convo, name),
        "timeline": render_timeline(convo),
        "chat": render_chat(convo),
        "tools": render_tools(report),
        "tool_failures": render_tool_failures(report),
        "tool_efficiency": render_tool_efficiency(report),
        "generated_artifacts": render_generated_artifacts(report),
        "knowledge": render_knowledge(report),
        "knowledge_effectiveness": render_knowledge_effectiveness(report),
        "search_strategy": render_search_strategy(report),
        "retrieval_depth": render_retrieval_depth(report),
        "grounding_pipeline": render_grounding_pipeline(report),
        "coverage_gaps": render_coverage_gaps(report),
        "reasoning": render_reasoning(report),
        "sandbox": render_sandbox(report),
        "quality": render_quality(report),
        "answer_grounding": render_answer_grounding(report),
        "quote_traceability": render_quote_traceability(report),
        "repetition": render_repetition(report),
        "citation_audit": render_citation_audit(report),
        "credits": render_credits(report),
        "instructions": render_instructions(report),
        "components": render_components(report, convo),
    }


def build_standalone_html(markdown: str, title: str) -> str:
    """Self-contained HTML export rendered client-side via CDN marked.js +
    mermaid.js. No server, no Python deps — just a portable file."""
    escaped = markdown.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/marked@15/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.7; font-size: 15px; max-width: 980px; margin: 0 auto; padding: 32px 24px; color: #18181b; background: #fff; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
  th, td {{ border: 1px solid #d4d4d8; padding: 8px 14px; text-align: left; }}
  th {{ background: #f4f4f5; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
  tr:nth-child(even) td {{ background: #fafafa; }}
  pre:not(.mermaid) {{ background: #f4f4f5; border: 1px solid #d4d4d8; border-radius: 8px; padding: 16px; overflow-x: auto; font-size: 13px; }}
  code:not(pre code) {{ background: #f4f4f5; border-radius: 4px; padding: 2px 6px; font-size: 0.875em; }}
  pre.mermaid {{ background: #fafafa; border: 1px solid #e4e4e7; border-radius: 10px; padding: 24px; margin: 16px 0; text-align: center; }}
  h1 {{ font-size: 26px; }}
  h2 {{ margin-top: 2.4em; }}
  h3 {{ margin-top: 1.8em; padding-bottom: 0.4em; border-bottom: 1px solid #e4e4e7; }}
  blockquote {{ border-left: 3px solid #f59e0b; padding: 8px 16px; margin: 12px 0; background: #fffbeb; border-radius: 0 6px 6px 0; }}
  hr {{ border: none; border-top: 1px solid #e4e4e7; margin: 2em 0; }}
  @media print {{ body {{ max-width: 100%; padding: 0; }} pre.mermaid {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<div id="content"></div>
<script>
(function() {{
  const md = `{escaped}`;
  const renderer = new marked.Renderer();
  const origCode = renderer.code.bind(renderer);
  renderer.code = function(token) {{
    if (token.lang === 'mermaid') {{ return '<pre class="mermaid">' + token.text + '</pre>'; }}
    return origCode(token);
  }};
  document.getElementById('content').innerHTML = marked.parse(md, {{ renderer: renderer }});
  mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
  mermaid.run();
}})();
</script>
</body>
</html>"""
