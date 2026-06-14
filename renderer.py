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
    if est.by_kind:
        out.append(
            "- " + "  |  ".join(f"**{k.replace('_', ' ')}:** {v:g}" for k, v in sorted(est.by_kind.items()))
        )
        out.append("")
    out += ["| Step | Kind | Credits | Detail |", "| --- | --- | --- | --- |"]
    for it in est.line_items:
        out.append(f"| {_cell(it.label)} | {_cell(it.kind.replace('_', ' '))} | {it.credits:g} | {_cell(it.detail)} |")
    if est.notes:
        out += ["", "> " + "  \n> ".join(_cell(n) for n in est.notes)]
    return "\n".join(out)


def render_components(report: AnalysisReport, convo: Conversation | None = None) -> str:
    from analysis import classify_tool_kind
    from explainer import explain

    p = report.agent
    rows: list[tuple[str, str, str, str, str]] = []  # category, label, value, summary, doc

    def add(category: str, label: str, value: str, key: str, kvalue: str | None = None) -> None:
        ex = explain(key, kvalue)
        rows.append((category, label, value, ex.summary, ex.doc or ""))

    if p is not None:
        if p.model_label or p.model_series:
            add("Agent settings", "Model", p.model_label or p.model_series or "", "model")
        if p.is_modern:
            add("Agent settings", "Orchestration", "Generative orchestration", "orchestration")
        if p.instructions:
            add("Agent settings", "Instructions", f"{len(p.instruction_segments) or 1} segment(s)", "instructions")
        if p.authentication_mode:
            add("Agent settings", "Authentication mode", p.authentication_mode, "authenticationMode", p.authentication_mode)
        if p.authentication_trigger:
            add(
                "Agent settings", "Authentication trigger", p.authentication_trigger,
                "authenticationTrigger", p.authentication_trigger,
            )
        if p.access_control_policy:
            add("Agent settings", "Access control", p.access_control_policy, "accessControlPolicy", p.access_control_policy)
        add("Agent settings", "Memory", "Enabled" if p.enable_memory else "Disabled", "enableMemory")
        if p.conversation_starters:
            add("Agent settings", "Conversation starters", f"{len(p.conversation_starters)} starter(s)", "conversationStarters")
        if p.recognizer_kind:
            add("Agent settings", "Recognizer", p.recognizer_kind, "recognizer")
        if p.template:
            add("Agent settings", "Template", p.template, "template")
        if p.runtime_provider:
            add("Agent settings", "Runtime provider", p.runtime_provider, "runtimeProvider")

        for ks in p.knowledge_sources:
            specific = f"knowledge.{ks.source_kind}" if ks.source_kind else "knowledge"
            ex = explain(specific)
            if not ex.documented:
                ex = explain("knowledge")
            rows.append(("Knowledge", ks.display_name or "(knowledge source)", ks.source_kind or "", ex.summary, ex.doc or ""))

        for ev in p.environment_variables:
            add(
                "Environment variables",
                ev.display_name or ev.schema_name or "(env var)",
                ev.type or (ev.default_value or ""),
                "environmentVariable",
            )

        for tc in p.tool_components:
            add("Tools & actions", tc.display_name or tc.kind, tc.kind, "tool")

    defined = {(tc.display_name or "").lower() for tc in (p.tool_components if p else [])}
    seen: set[str] = set()
    if convo is not None:
        for tcall in convo.tool_calls:
            if classify_tool_kind(tcall) not in {"action", "skill"}:
                continue
            label = tcall.display_name or tcall.name or "action"
            if label.lower() in defined or label.lower() in seen:
                continue
            seen.add(label.lower())
            add("Tools & actions", label, "observed at runtime", "tool")

    if not rows:
        return ""
    out = ["## Components", "", "| Category | Component | Value | Explanation | Reference |", "| --- | --- | --- | --- | --- |"]
    for category, label, value, summary, doc in rows:
        ref = f"[Learn]({doc})" if doc else "—"
        out.append(f"| {_cell(category)} | {_cell(label)} | {_cell(value)} | {_cell(summary)} | {ref} |")
    return "\n".join(out)


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


def render_markdown(report: AnalysisReport, convo: Conversation | None = None, title: str | None = None) -> str:
    name = report.agent.display_name if report.agent else "Modern agent"
    heading = title or f"Agent analysis — {name}"

    sections = [
        f"# {heading}",
        render_findings(report),
        render_agent_profile(report),
        render_overview(report),
        render_conversation_flow(convo, name),
        render_tools(report),
        render_knowledge(report),
        render_knowledge_effectiveness(report),
        render_reasoning(report),
        render_quality(report),
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
        "profile": render_agent_profile(report),
        "cross_reference": render_cross_reference(report),
        "flow": render_conversation_flow(convo, name),
        "chat": render_chat(convo),
        "tools": render_tools(report),
        "knowledge": render_knowledge(report),
        "knowledge_effectiveness": render_knowledge_effectiveness(report),
        "reasoning": render_reasoning(report),
        "quality": render_quality(report),
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
