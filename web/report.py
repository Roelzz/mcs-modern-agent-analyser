"""Native Reflex report panels bound to the structured view-models in state."""

import reflex as rx

from web.state import State

_KIND_COLOR = {"retrieval": "blue", "action": "grass", "skill": "purple", "other": "gray"}


# ---------------------------------------------------------------------------
# Small reusable atoms
# ---------------------------------------------------------------------------
def stat_card(label: str, value, icon: str, color: str = "gray") -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(icon, size=16, color=f"var(--{color}-9)"),
            rx.text(label, size="1", color_scheme="gray", weight="medium"),
            spacing="2",
            align="center",
        ),
        rx.text(value, size="7", weight="bold", color_scheme=color),
        padding="16px 18px",
        border="1px solid var(--gray-a5)",
        border_radius="12px",
        background="var(--gray-a2)",
        min_width="130px",
        flex="1",
    )


def _def_row(label: str, value) -> rx.Component:
    return rx.hstack(
        rx.text(label, size="2", color_scheme="gray", width="160px", flex_shrink="0"),
        rx.text(value, size="2", weight="medium"),
        spacing="3",
        align="start",
        width="100%",
    )


def section_title(text: str, icon: str) -> rx.Component:
    return rx.hstack(
        rx.icon(icon, size=18, color="var(--grass-9)"),
        rx.heading(text, size="4"),
        spacing="2",
        align="center",
    )


def empty(msg: str, icon: str = "inbox") -> rx.Component:
    return rx.vstack(
        rx.icon(icon, size=34, color="var(--gray-a8)"),
        rx.text(msg, size="2", color_scheme="gray"),
        spacing="3",
        align="center",
        justify="center",
        padding="48px",
        width="100%",
    )


def card(*children, **kw) -> rx.Component:
    style = dict(
        padding="18px",
        border="1px solid var(--gray-a5)",
        border_radius="12px",
        background="var(--color-panel-solid)",
        width="100%",
    )
    style.update(kw)
    return rx.box(*children, **style)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
def finding_card(f) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(f.icon, size=18, color=f"var(--{f.color}-9)", flex_shrink="0", margin_top="2px"),
            rx.vstack(
                rx.hstack(
                    rx.text(f.title, weight="bold", size="2"),
                    rx.badge(f.category, variant="soft", color_scheme="gray", size="1"),
                    spacing="2",
                    align="center",
                    wrap="wrap",
                ),
                rx.text(f.detail, size="2", color_scheme="gray"),
                spacing="1",
                align="start",
            ),
            spacing="3",
            align="start",
        ),
        padding="14px 16px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{f.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def _filter_chip(label: str, value: str, count, color: str) -> rx.Component:
    return rx.button(
        rx.icon(
            rx.match(value, ("critical", "octagon-alert"), ("warning", "triangle-alert"), ("info", "info"), "list"),
            size=13,
        ),
        f"{label} ({count})",
        on_click=lambda: State.set_finding_filter(value),
        variant=rx.cond(State.finding_filter == value, "solid", "soft"),
        color_scheme=color,
        size="1",
    )


def findings_block() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            section_title("Findings", "flag"),
            rx.spacer(),
            rx.hstack(
                _filter_chip("All", "all", State.findings_total, "gray"),
                _filter_chip("Critical", "critical", State.f_critical, "red"),
                _filter_chip("Warning", "warning", State.f_warning, "amber"),
                _filter_chip("Info", "info", State.f_info, "blue"),
                spacing="2",
                wrap="wrap",
            ),
            width="100%",
            align="center",
            wrap="wrap",
        ),
        rx.cond(
            State.filtered_findings.length() > 0,
            rx.vstack(rx.foreach(State.filtered_findings, finding_card), spacing="2", width="100%"),
            empty("No findings in this category.", "check-check"),
        ),
        spacing="3",
        width="100%",
    )


# ---------------------------------------------------------------------------
# Documents + citations
# ---------------------------------------------------------------------------
def doc_item(d) -> rx.Component:
    highlighted = (State.active_citation != "") & (d.reference_id == State.active_citation)
    return rx.box(
        rx.hstack(
            rx.cond(
                d.reference_id != "",
                rx.badge(d.reference_id, variant="soft", color_scheme="grass", size="1"),
                rx.fragment(),
            ),
            rx.text(d.title, size="2", weight="medium"),
            rx.spacer(),
            rx.cond(d.cited, rx.badge("cited", color_scheme="grass", size="1"), rx.fragment()),
            rx.cond(d.unused, rx.badge("unused", color_scheme="amber", variant="soft", size="1"), rx.fragment()),
            rx.cond(
                d.url != "",
                rx.link(rx.icon("external-link", size=13), href=d.url, is_external=True),
                rx.fragment(),
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        padding="8px 12px",
        border=rx.cond(highlighted, "2px solid var(--grass-9)", "1px solid var(--gray-a5)"),
        background=rx.cond(highlighted, "var(--grass-a3)", "var(--gray-a2)"),
        border_radius="8px",
        width="100%",
    )


def citation_chip(c) -> rx.Component:
    return rx.badge(
        c.label,
        on_click=lambda: State.toggle_citation(c.reference_id),
        variant=rx.cond((State.active_citation != "") & (State.active_citation == c.reference_id), "solid", "soft"),
        color_scheme=rx.cond(c.reference_id != "", "grass", "gray"),
        cursor="pointer",
        size="1",
    )


# ---------------------------------------------------------------------------
# Tool-call rendering (taxonomy-aware)
# ---------------------------------------------------------------------------
def _kv_table(params) -> rx.Component:
    return rx.vstack(
        rx.foreach(
            params,
            lambda kv: rx.hstack(
                rx.text(kv.key, size="1", color_scheme="gray", width="150px", flex_shrink="0", weight="medium"),
                rx.text(kv.value, size="1", style={"word_break": "break-word"}),
                spacing="2",
                align="start",
                width="100%",
            ),
        ),
        spacing="1",
        width="100%",
    )


def _retrieval_body(tc) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text("Query", size="1", color_scheme="gray", weight="medium"),
            rx.code(tc.query, size="1"),
            rx.spacer(),
            rx.cond(
                tc.zero_result,
                rx.badge("zero results", color_scheme="amber", size="1"),
                rx.badge(f"{tc.result_count} docs", color_scheme="blue", variant="soft", size="1"),
            ),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(
            tc.docs.length() > 0,
            rx.vstack(rx.foreach(tc.docs, doc_item), spacing="1", width="100%"),
            rx.fragment(),
        ),
        spacing="2",
        width="100%",
    )


def _action_body(tc) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.cond(
                tc.recipient != "",
                rx.hstack(
                    rx.icon("user", size=13, color="var(--gray-9)"),
                    rx.text(tc.recipient, size="1", weight="medium"),
                    spacing="1",
                    align="center",
                ),
                rx.fragment(),
            ),
            rx.spacer(),
            rx.cond(tc.content_type != "", rx.badge(tc.content_type, variant="soft", size="1"), rx.fragment()),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(
            tc.content_html != "",
            rx.box(
                rx.html(tc.content_html),
                padding="12px 14px",
                border="1px dashed var(--grass-a7)",
                border_radius="8px",
                background="var(--grass-a2)",
                width="100%",
                max_height="260px",
                overflow_y="auto",
            ),
            rx.fragment(),
        ),
        rx.cond(
            tc.content_text != "",
            rx.box(rx.text(tc.content_text, size="1"), padding="10px", background="var(--gray-a2)", border_radius="8px"),
            rx.fragment(),
        ),
        rx.cond(tc.params.length() > 0, _kv_table(tc.params), rx.fragment()),
        spacing="2",
        width="100%",
    )


def tool_call_card(tc) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(tc.icon, size=15, color="var(--gray-11)"),
            rx.text(tc.display_name, size="2", weight="bold"),
            rx.badge(tc.kind, color_scheme=_KIND_COLOR.get(tc.kind, "gray"), variant="soft", size="1"),
            rx.spacer(),
            rx.badge(
                rx.cond(tc.status != "", tc.status, "—"),
                color_scheme=rx.cond(tc.failed, "red", "gray"),
                variant=rx.cond(tc.failed, "solid", "soft"),
                size="1",
            ),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.box(
            rx.match(
                tc.kind,
                ("retrieval", _retrieval_body(tc)),
                ("action", _action_body(tc)),
                ("skill", rx.text("Skill invoked.", size="1", color_scheme="gray")),
                rx.cond(tc.params.length() > 0, _kv_table(tc.params), rx.fragment()),
            ),
            margin_top="8px",
        ),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


# ---------------------------------------------------------------------------
# Chat transcript
# ---------------------------------------------------------------------------
def _thoughts_block(b) -> rx.Component:
    return rx.cond(
        State.show_thoughts & (b.thoughts.length() > 0),
        rx.box(
            rx.hstack(rx.icon("brain", size=13, color="var(--purple-9)"), rx.text("Reasoning", size="1", weight="medium", color_scheme="purple"), spacing="1"),
            rx.vstack(
                rx.foreach(b.thoughts, lambda t: rx.text(t, size="1", color_scheme="gray", style={"font_style": "italic"})),
                spacing="1",
                width="100%",
                margin_top="4px",
            ),
            padding="10px 12px",
            border_left="2px solid var(--purple-a7)",
            background="var(--purple-a2)",
            border_radius="0 8px 8px 0",
            width="100%",
        ),
        rx.fragment(),
    )


def chat_bubble(b) -> rx.Component:
    user = b.kind == "user"
    return rx.box(
        rx.hstack(
            rx.icon(rx.cond(user, "user", "bot"), size=15, color=rx.cond(user, "var(--blue-9)", "var(--grass-9)")),
            rx.text(rx.cond(user, "User", "Agent"), size="1", weight="bold", color_scheme=rx.cond(user, "blue", "grass")),
            rx.spacer(),
            rx.text(f"#{b.idx}", size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
        ),
        rx.cond(
            b.text != "",
            rx.markdown(b.text, size="2", margin_top="6px"),
            rx.fragment(),
        ),
        rx.cond(user, rx.fragment(), _thoughts_block(b)),
        rx.cond(
            b.tool_calls.length() > 0,
            rx.vstack(rx.foreach(b.tool_calls, tool_call_card), spacing="2", width="100%", margin_top="8px"),
            rx.fragment(),
        ),
        rx.cond(
            b.citations.length() > 0,
            rx.hstack(
                rx.text("Citations:", size="1", color_scheme="gray"),
                rx.foreach(b.citations, citation_chip),
                spacing="2",
                align="center",
                margin_top="8px",
                wrap="wrap",
            ),
            rx.fragment(),
        ),
        padding="14px 16px",
        border="1px solid var(--gray-a5)",
        border_left=rx.cond(user, "3px solid var(--blue-9)", "1px solid var(--gray-a5)"),
        border_right=rx.cond(user, "1px solid var(--gray-a5)", "3px solid var(--grass-9)"),
        border_radius="10px",
        background=rx.cond(user, "var(--blue-a2)", "var(--gray-a2)"),
        max_width="80%",
        align_self=rx.cond(user, "flex-start", "flex-end"),
    )


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------
def turn_card(t) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.badge(f"Turn {t.index}", color_scheme="grass", variant="soft", size="1"),
            rx.text(t.question, size="2", weight="medium"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.hstack(
            rx.cond(
                t.searches.length() > 0,
                rx.hstack(rx.icon("search", size=12, color="var(--blue-9)"), rx.text(f"{t.searches.length()} search", size="1", color_scheme="gray"), spacing="1", align="center"),
                rx.fragment(),
            ),
            rx.cond(
                t.actions.length() > 0,
                rx.hstack(rx.icon("send", size=12, color="var(--grass-9)"), rx.text(f"{t.actions.length()} action", size="1", color_scheme="gray"), spacing="1", align="center"),
                rx.fragment(),
            ),
            rx.cond(
                t.doc_count > 0,
                rx.hstack(rx.icon("file-text", size=12, color="var(--gray-9)"), rx.text(f"{t.doc_count} docs", size="1", color_scheme="gray"), spacing="1", align="center"),
                rx.fragment(),
            ),
            rx.cond(
                t.citations.length() > 0,
                rx.hstack(rx.icon("quote", size=12, color="var(--grass-9)"), rx.text(f"{t.citations.length()} cited", size="1", color_scheme="gray"), spacing="1", align="center"),
                rx.fragment(),
            ),
            spacing="3",
            align="center",
            wrap="wrap",
            margin_top="6px",
        ),
        rx.cond(
            t.answer != "",
            rx.text(t.answer, size="1", color_scheme="gray", margin_top="6px", style={"display": "-webkit-box", "-webkit-line-clamp": "2", "-webkit-box-orient": "vertical", "overflow": "hidden"}),
            rx.fragment(),
        ),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def turns_block() -> rx.Component:
    return rx.cond(
        State.turns.length() > 0,
        rx.vstack(
            section_title("Turn-by-turn breakdown", "list-ordered"),
            rx.vstack(rx.foreach(State.turns, turn_card), spacing="2", width="100%"),
            spacing="3",
            width="100%",
        ),
        rx.fragment(),
    )


def overview_panel() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            stat_card("Turns", State.m_turns, "messages-square", "grass"),
            stat_card("Tool calls", State.m_tools, "wrench", "blue"),
            stat_card("Searches", State.m_searches, "search", "blue"),
            stat_card("Thoughts", State.m_thoughts, "brain", "purple"),
            stat_card("Failed tools", State.m_failed, "circle-x", rx.cond(State.m_failed > 0, "red", "gray")),
            stat_card("Zero-result", State.m_zero, "search-x", rx.cond(State.m_zero > 0, "amber", "gray")),
            spacing="3",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(
            State.convo_present,
            rx.hstack(
                stat_card("Calls / answer", State.te_calls_per_answer, "sigma", "blue"),
                stat_card("Searches → 1st answer", State.te_searches_to_first, "milestone", "gray"),
                stat_card("Avg bot msgs / turn", State.te_avg_bot_msgs, "message-square", "gray"),
                stat_card("User turns", State.te_user_turns, "user", "grass"),
                spacing="3",
                width="100%",
                wrap="wrap",
            ),
            rx.fragment(),
        ),
        rx.divider(),
        findings_block(),
        rx.cond(State.convo_present, rx.divider(), rx.fragment()),
        turns_block(),
        spacing="5",
        width="100%",
    )


def agent_panel() -> rx.Component:
    return rx.cond(
        State.agent_present,
        rx.vstack(
            card(
                section_title("Definition", "id-card"),
                rx.vstack(
                    _def_row("Display name", State.agent_title),
                    _def_row("Model", State.model_label),
                    _def_row("Template", State.template),
                    _def_row("Recognizer", State.recognizer),
                    _def_row("Authentication", State.auth),
                    _def_row("Memory", rx.cond(State.memory, "enabled", "disabled")),
                    _def_row("Created", State.created_at),
                    _def_row("Modified", State.modified_at),
                    spacing="2",
                    width="100%",
                    margin_top="12px",
                ),
            ),
            card(
                rx.hstack(
                    section_title("Instructions", "scroll-text"),
                    rx.spacer(),
                    rx.button(rx.icon("copy", size=14), "Copy", on_click=rx.set_clipboard(State.instructions), variant="soft", size="1"),
                    width="100%",
                    align="center",
                ),
                rx.box(
                    rx.text(State.instructions, size="2", style={"white_space": "pre-wrap"}),
                    margin_top="10px",
                    padding="12px",
                    background="var(--gray-a2)",
                    border_radius="8px",
                    max_height="320px",
                    overflow_y="auto",
                ),
            ),
            rx.cond(
                State.conversation_starters.length() > 0,
                card(
                    section_title("Conversation starters", "messages-square"),
                    rx.hstack(
                        rx.foreach(State.conversation_starters, lambda s: rx.badge(s, variant="soft", color_scheme="grass", size="2")),
                        spacing="2",
                        wrap="wrap",
                        margin_top="10px",
                    ),
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.knowledge_sources.length() > 0,
                card(
                    section_title("Knowledge sources", "database"),
                    rx.vstack(
                        rx.foreach(
                            State.knowledge_sources,
                            lambda k: rx.hstack(
                                rx.icon("database", size=14, color="var(--gray-9)"),
                                rx.text(k.name, size="2", weight="medium"),
                                rx.cond(k.type != "", rx.badge(k.type, variant="soft", size="1", color_scheme="gray"), rx.fragment()),
                                rx.spacer(),
                                rx.cond(k.unused, rx.badge("unused in convo", color_scheme="amber", variant="soft", size="1"), rx.fragment()),
                                spacing="2",
                                align="center",
                                width="100%",
                            ),
                        ),
                        spacing="2",
                        width="100%",
                        margin_top="10px",
                    ),
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.env_vars.length() > 0,
                card(
                    section_title("Environment variables", "settings"),
                    rx.vstack(
                        rx.foreach(
                            State.env_vars,
                            lambda e: _def_row(e.name, rx.cond(e.default != "", e.default, "—")),
                        ),
                        spacing="2",
                        width="100%",
                        margin_top="10px",
                    ),
                ),
                rx.fragment(),
            ),
            spacing="4",
            width="100%",
        ),
        empty("No agent YAML uploaded — this is a transcript-only analysis.", "file-question"),
    )


def conversation_panel() -> rx.Component:
    return rx.cond(
        State.convo_present,
        rx.vstack(
            card(
                section_title("Sequence diagram", "git-branch"),
                rx.box(
                    rx.el.pre(State.mermaid, class_name="mermaid"),
                    width="100%",
                    overflow_x="auto",
                    margin_top="10px",
                ),
            ),
            rx.hstack(
                rx.input(
                    rx.input.slot(rx.icon("search", size=15)),
                    placeholder="Search transcript…",
                    value=State.transcript_query,
                    on_change=State.set_transcript_query,
                    size="2",
                    width="100%",
                ),
                rx.cond(
                    State.transcript_query != "",
                    rx.button(f"{State.chat_hits} hits", rx.icon("x", size=13), on_click=State.clear_transcript_query, variant="soft", size="2"),
                    rx.fragment(),
                ),
                rx.button(
                    rx.icon("brain", size=14),
                    rx.cond(State.show_thoughts, "Hide reasoning", "Show reasoning"),
                    on_click=State.toggle_thoughts,
                    variant="soft",
                    color_scheme="purple",
                    size="2",
                ),
                spacing="2",
                width="100%",
                align="center",
                wrap="wrap",
            ),
            rx.cond(
                State.filtered_chat.length() > 0,
                rx.vstack(rx.foreach(State.filtered_chat, chat_bubble), spacing="3", width="100%"),
                empty("No messages match your search.", "search-x"),
            ),
            spacing="4",
            width="100%",
        ),
        empty("No transcript uploaded.", "file-question"),
    )


def _tool_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Tool"),
                rx.table.column_header_cell("Kind"),
                rx.table.column_header_cell("Calls"),
                rx.table.column_header_cell("Completed"),
                rx.table.column_header_cell("Failed"),
            )
        ),
        rx.table.body(
            rx.foreach(
                State.tool_rows,
                lambda r: rx.table.row(
                    rx.table.cell(rx.text(r.name, weight="medium", size="2")),
                    rx.table.cell(rx.badge(r.kind, color_scheme=_KIND_COLOR.get(r.kind, "gray"), variant="soft", size="1")),
                    rx.table.cell(r.count),
                    rx.table.cell(r.completed),
                    rx.table.cell(rx.cond(r.failed > 0, rx.text(r.failed, color_scheme="red", weight="bold"), rx.text("0", color_scheme="gray"))),
                ),
            )
        ),
        variant="surface",
        size="1",
        width="100%",
    )


def knowledge_query_card(q) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon("search", size=14, color="var(--blue-9)"),
            rx.code(q.query, size="1"),
            rx.spacer(),
            rx.cond(
                q.zero_result,
                rx.badge("zero results", color_scheme="amber", size="1"),
                rx.badge(f"{q.result_count} docs", color_scheme="blue", variant="soft", size="1"),
            ),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(
            q.docs.length() > 0,
            rx.vstack(rx.foreach(q.docs, doc_item), spacing="1", width="100%", margin_top="8px"),
            rx.fragment(),
        ),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def _chip_list(title: str, icon: str, items, color: str) -> rx.Component:
    return rx.cond(
        items.length() > 0,
        card(
            section_title(title, icon),
            rx.vstack(
                rx.foreach(items, lambda s: rx.hstack(rx.icon("dot", size=14, color=f"var(--{color}-9)"), rx.text(s, size="2"), spacing="1", align="center")),
                spacing="1",
                width="100%",
                margin_top="10px",
            ),
        ),
        rx.fragment(),
    )


def source_eff_row(s) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(s.icon, size=15, color=f"var(--{s.color}-9)", flex_shrink="0"),
            rx.vstack(
                rx.hstack(
                    rx.text(s.name, size="2", weight="medium"),
                    rx.cond(s.type != "", rx.badge(s.type, variant="soft", color_scheme="gray", size="1"), rx.fragment()),
                    rx.cond(
                        ~s.configured,
                        rx.badge("runtime", variant="soft", color_scheme="blue", size="1"),
                        rx.fragment(),
                    ),
                    spacing="2",
                    align="center",
                    wrap="wrap",
                ),
                rx.text(f"{s.cited} of {s.retrieved} retrieved doc(s) cited", size="1", color_scheme="gray"),
                spacing="1",
                align="start",
            ),
            rx.spacer(),
            rx.vstack(
                rx.badge(f"{s.rate_pct}%", color_scheme=s.color, variant="soft", size="1"),
                rx.progress(value=s.rate_pct, color_scheme=s.color, width="90px", size="1"),
                spacing="1",
                align="end",
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{s.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def knowledge_effectiveness_block() -> rx.Component:
    return rx.cond(
        State.source_effectiveness.length() > 0,
        card(
            section_title("Knowledge source effectiveness", "target"),
            rx.hstack(
                stat_card("Searches", State.eff_total_searches, "search", "blue"),
                stat_card("Distinct docs", State.eff_distinct_docs, "files", "gray"),
                stat_card("Avg docs/search", State.eff_avg_docs, "sigma", "gray"),
                stat_card(
                    "Unattributed",
                    State.eff_unattributed,
                    "file-question",
                    rx.cond(State.eff_unattributed > 0, "amber", "gray"),
                ),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.vstack(
                rx.foreach(State.source_effectiveness, source_eff_row),
                spacing="2",
                width="100%",
                margin_top="12px",
            ),
        ),
        rx.fragment(),
    )


def tool_failure_row(f) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(f.icon, size=15, color=f"var(--{f.color}-9)", flex_shrink="0"),
            rx.code(f.name, size="1"),
            rx.cond(f.embedded, rx.badge("hidden by status", variant="soft", color_scheme="amber", size="1"), rx.fragment()),
            rx.spacer(),
            rx.badge(f.recovery_label, variant="soft", color_scheme=f.color, size="1"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(f.params_summary != "", rx.text(f.params_summary, size="1", color_scheme="gray", margin_top="4px"), rx.fragment()),
        rx.cond(
            f.error_text != "",
            rx.text(f.error_text, size="1", font_family="monospace", color="var(--red-11)", margin_top="6px"),
            rx.fragment(),
        ),
        rx.cond(f.next_label != "", rx.text(f.next_label, size="1", color_scheme="grass", margin_top="4px"), rx.fragment()),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{f.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def tool_failures_block() -> rx.Component:
    return rx.cond(
        State.tf_total > 0,
        card(
            section_title("Failed tools & recovery", "circle-x"),
            rx.hstack(
                stat_card("Failures", State.tf_total, "circle-x", "red"),
                stat_card("Hidden by status", State.tf_embedded, "eye-off", rx.cond(State.tf_embedded > 0, "amber", "gray")),
                stat_card("Recovered", State.tf_recovered, "circle-check", rx.cond(State.tf_recovered > 0, "grass", "gray")),
                stat_card("Gave up", State.tf_gaveup, "ban", rx.cond(State.tf_gaveup > 0, "red", "gray")),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.text(
                "Calls whose status said 'completed' but whose result carried an error are counted here and in the Overview failed-tool metric.",
                size="1",
                color_scheme="gray",
                margin_top="8px",
            ),
            rx.vstack(rx.foreach(State.tool_failure_rows, tool_failure_row), spacing="2", width="100%", margin_top="12px"),
        ),
        rx.fragment(),
    )


def duplicate_group_row(d) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.badge(d.count_label, variant="soft", color_scheme="amber", size="1"),
            rx.code(d.name, size="1"),
            rx.spacer(),
            rx.text(d.turns_label, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(d.params_summary != "", rx.text(d.params_summary, size="1", color_scheme="gray", margin_top="4px"), rx.fragment()),
        padding="10px 14px",
        border="1px solid var(--gray-a5)",
        border_left="3px solid var(--amber-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def efficiency_block() -> rx.Component:
    return rx.cond(
        State.eff_total_calls > 0,
        card(
            section_title("Tool-call efficiency", "gauge"),
            rx.hstack(
                stat_card("Total calls", State.eff_total_calls, "wrench", "blue"),
                stat_card("Unique", State.eff_unique_calls, "fingerprint", "gray"),
                stat_card("Redundant", State.eff_redundant, "copy", rx.cond(State.eff_redundant > 0, "amber", "gray")),
                stat_card("Calls / answer", State.eff_calls_per_answer, "sigma", "gray"),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.cond(
                State.duplicate_groups.length() > 0,
                rx.vstack(rx.foreach(State.duplicate_groups, duplicate_group_row), spacing="2", width="100%", margin_top="12px"),
                rx.text("No redundant tool calls — every call used distinct parameters.", size="2", color_scheme="gray", margin_top="10px"),
            ),
        ),
        rx.fragment(),
    )


def coverage_gap_row(g) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(g.icon, size=15, color=f"var(--{g.color}-9)", flex_shrink="0"),
            rx.badge(g.reason_label, variant="soft", color_scheme=g.color, size="1"),
            rx.spacer(),
            rx.text(g.turn_label, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(g.user_question != "", rx.text(g.user_question, size="2", margin_top="6px"), rx.fragment()),
        rx.cond(g.query != "", rx.text(g.query, size="1", font_family="monospace", color_scheme="gray", margin_top="4px"), rx.fragment()),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{g.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def coverage_block() -> rx.Component:
    return rx.cond(
        State.coverage_gaps.length() > 0,
        card(
            section_title("Knowledge coverage gaps", "search-x"),
            rx.text(
                "Turns where a search returned nothing, the agent admitted a gap, or an answer had no grounding.",
                size="1",
                color_scheme="gray",
                margin_top="6px",
            ),
            rx.vstack(rx.foreach(State.coverage_gaps, coverage_gap_row), spacing="2", width="100%", margin_top="12px"),
        ),
        rx.fragment(),
    )


def artifact_row(a) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(a.type_icon, size=18, color=f"var(--{a.type_color}-9)", flex_shrink="0"),
            rx.vstack(
                rx.text(a.name, size="2", weight="bold"),
                rx.hstack(
                    rx.badge(a.type_label, color_scheme=a.type_color, variant="soft", size="1"),
                    rx.badge(
                        rx.icon(a.how_icon, size=11),
                        a.how_label,
                        color_scheme=a.how_color,
                        variant="soft",
                        size="1",
                    ),
                    rx.cond(a.turn_label != "", rx.text(a.turn_label, size="1", color_scheme="gray"), rx.fragment()),
                    spacing="2",
                    align="center",
                    wrap="wrap",
                ),
                spacing="1",
                align="start",
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        rx.cond(
            a.evidence != "",
            rx.text(a.evidence, size="1", color_scheme="gray", margin_top="6px", style={"font_style": "italic"}),
            rx.fragment(),
        ),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{a.type_color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def artifacts_block() -> rx.Component:
    return rx.cond(
        State.has_artifacts,
        card(
            rx.hstack(
                section_title("Generated outputs", "file-output"),
                rx.spacer(),
                rx.badge(State.artifact_types_label, color_scheme="amber", variant="soft", size="1"),
                rx.tooltip(
                    rx.icon("info", size=14, color="var(--gray-a9)"),
                    content="Files the agent produced and attached to the conversation (e.g. a PowerPoint "
                    "authored with python-pptx). Verify these are grounded in the cited sources.",
                ),
                align="center",
                width="100%",
            ),
            rx.vstack(rx.foreach(State.artifacts, artifact_row), spacing="2", width="100%", margin_top="12px"),
        ),
        rx.fragment(),
    )


def tools_panel() -> rx.Component:
    return rx.cond(
        (State.tool_rows.length() > 0) | State.has_artifacts,
        rx.vstack(
            card(section_title("Tool & action usage", "wrench"), rx.box(_tool_table(), margin_top="12px")),
            artifacts_block(),
            tool_failures_block(),
            efficiency_block(),
            knowledge_effectiveness_block(),
            coverage_block(),
            _chip_list("Skill loads", "puzzle", State.skill_loads, "purple"),
            _chip_list("Retry signals", "rotate-ccw", State.retry_signals, "amber"),
            _chip_list("Tool failures", "circle-x", State.tool_failures, "red"),
            rx.cond(
                State.knowledge_queries.length() > 0,
                card(
                    section_title("Knowledge queries", "search"),
                    rx.vstack(rx.foreach(State.knowledge_queries, knowledge_query_card), spacing="2", width="100%", margin_top="12px"),
                ),
                rx.fragment(),
            ),
            spacing="4",
            width="100%",
        ),
        empty("No tools or actions were used in this conversation.", "wrench"),
    )


def sandbox_signal_row(s) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(s.icon, size=14, color=f"var(--{s.color}-9)", flex_shrink="0"),
            rx.badge(s.category_label, color_scheme=s.color, variant="soft", size="1"),
            rx.cond(s.tool != "", rx.code(s.tool, size="1"), rx.fragment()),
            rx.spacer(),
            rx.text(s.turn_label, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.text(s.excerpt, size="1", color_scheme="gray", margin_top="6px", style={"font_style": "italic"}),
        padding="10px 12px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{s.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def sandbox_friction_row(f) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(f.icon, size=14, color=f"var(--{f.color}-9)", flex_shrink="0"),
            rx.badge(f.kind_label, color_scheme=f.color, variant="soft", size="1"),
            rx.spacer(),
            rx.badge(
                f.recovered_label,
                color_scheme=rx.cond(f.recovered, "grass", "red"),
                variant="soft",
                size="1",
            ),
            rx.text(f.turn_label, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.text(f.excerpt, size="1", color_scheme="gray", margin_top="6px", style={"font_style": "italic"}),
        padding="10px 12px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{f.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def skill_gap_row(g) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon("puzzle", size=14, color="var(--amber-9)", flex_shrink="0"),
            rx.badge(g.wanted_label, color_scheme="amber", variant="soft", size="1"),
            rx.icon("arrow-right", size=12, color="var(--gray-a8)"),
            rx.badge(g.fallback_label, color_scheme="purple", variant="soft", size="1"),
            rx.spacer(),
            rx.text(g.turn_label, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.text(g.excerpt, size="1", color_scheme="gray", margin_top="6px", style={"font_style": "italic"}),
        padding="10px 12px",
        border="1px solid var(--gray-a5)",
        border_left="3px solid var(--amber-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def sandbox_block() -> rx.Component:
    return rx.cond(
        State.sandbox_used,
        card(
            rx.hstack(
                section_title("Sandbox & code interpreter", "terminal"),
                rx.spacer(),
                rx.tooltip(
                    rx.icon("info", size=14, color="var(--gray-a9)"),
                    content="Modern agents run bash/grep/python in a sandbox to read full documents. "
                    "This activity lives in the reasoning trace, not in tool calls.",
                ),
                align="center",
                width="100%",
            ),
            rx.hstack(
                stat_card("Turns with code", State.sandbox_turns, "terminal", "purple"),
                stat_card(
                    "Friction episodes",
                    State.sandbox_friction_count,
                    "shield-alert",
                    rx.cond(State.sandbox_friction_count > 0, "amber", "gray"),
                ),
                stat_card("Doc-processing skills", State.sandbox_doc_skills, "file-text", "cyan"),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.hstack(
                rx.text("Tools observed:", size="1", color_scheme="gray", weight="medium"),
                rx.code(State.sandbox_tools_label, size="1"),
                spacing="2",
                align="center",
                margin_top="10px",
                wrap="wrap",
            ),
            rx.cond(
                (State.sandbox_authoring_count > 0) | (State.sandbox_analysis_count > 0),
                rx.box(
                    rx.text(
                        "What the code was for", size="1", weight="bold", color_scheme="gray", margin_top="14px"
                    ),
                    rx.hstack(
                        rx.box(
                            rx.hstack(
                                rx.icon("file-output", size=14, color="var(--purple-9)"),
                                rx.text("Authoring (generated files)", size="1", weight="medium"),
                                spacing="2",
                                align="center",
                            ),
                            rx.text(State.sandbox_authoring_label, size="2", color_scheme="purple", weight="bold"),
                            padding="8px 12px",
                            border="1px solid var(--purple-a6)",
                            border_radius="8px",
                            background="var(--purple-a2)",
                            flex="1",
                            min_width="180px",
                        ),
                        rx.box(
                            rx.hstack(
                                rx.icon("file-search", size=14, color="var(--blue-9)"),
                                rx.text("Analysis (read documents)", size="1", weight="medium"),
                                spacing="2",
                                align="center",
                            ),
                            rx.text(State.sandbox_analysis_label, size="2", color_scheme="blue", weight="bold"),
                            padding="8px 12px",
                            border="1px solid var(--blue-a6)",
                            border_radius="8px",
                            background="var(--blue-a2)",
                            flex="1",
                            min_width="180px",
                        ),
                        spacing="3",
                        width="100%",
                        wrap="wrap",
                        margin_top="6px",
                    ),
                    width="100%",
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.has_skill_gaps,
                rx.vstack(
                    rx.hstack(
                        rx.text("Skill gaps → code fallback", size="1", weight="bold", color_scheme="amber"),
                        rx.tooltip(
                            rx.icon("info", size=12, color="var(--gray-a9)"),
                            content="The agent looked for a skill, found none, and wrote code directly instead.",
                        ),
                        spacing="2",
                        align="center",
                        margin_top="14px",
                    ),
                    rx.foreach(State.skill_gaps, skill_gap_row),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.sandbox_skills.length() > 0,
                rx.vstack(
                    rx.foreach(
                        State.sandbox_skills,
                        lambda s: rx.hstack(
                            rx.icon(s.icon, size=14, color=f"var(--{s.color}-9)", flex_shrink="0"),
                            rx.text(s.name, size="2", weight="medium"),
                            rx.badge(s.category_label, color_scheme=s.color, variant="soft", size="1"),
                            rx.spacer(),
                            rx.text(s.turn_label, size="1", color_scheme="gray"),
                            spacing="2",
                            align="center",
                            width="100%",
                            wrap="wrap",
                        ),
                    ),
                    spacing="2",
                    width="100%",
                    margin_top="12px",
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.sandbox_friction.length() > 0,
                rx.vstack(
                    rx.text("Sandbox friction", size="1", weight="bold", color_scheme="amber", margin_top="14px"),
                    rx.foreach(State.sandbox_friction, sandbox_friction_row),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.sandbox_signals.length() > 0,
                rx.vstack(
                    rx.text("Activity signals", size="1", weight="bold", color_scheme="gray", margin_top="14px"),
                    rx.foreach(State.sandbox_signals, sandbox_signal_row),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
        ),
        rx.fragment(),
    )


def reasoning_panel() -> rx.Component:
    has_thoughts = State.m_thoughts > 0
    return rx.cond(
        has_thoughts | (State.premise_corrections.length() > 0) | State.sandbox_used,
        rx.vstack(
            sandbox_block(),
            card(
                section_title("Reasoning timeline", "brain"),
                rx.vstack(
                    rx.foreach(
                        State.chat,
                        lambda b: rx.cond(
                            b.thoughts.length() > 0,
                            rx.box(
                                rx.text(f"Message #{b.idx}", size="1", color_scheme="gray", weight="medium"),
                                rx.vstack(
                                    rx.foreach(b.thoughts, lambda t: rx.hstack(rx.icon("dot", size=14, color="var(--purple-9)"), rx.text(t, size="2"), spacing="1", align="start")),
                                    spacing="1",
                                    width="100%",
                                ),
                                padding="10px 12px",
                                border_left="2px solid var(--purple-a7)",
                                background="var(--purple-a2)",
                                border_radius="0 8px 8px 0",
                                width="100%",
                            ),
                            rx.fragment(),
                        ),
                    ),
                    spacing="2",
                    width="100%",
                    margin_top="12px",
                ),
            ),
            _chip_list("Premise corrections", "git-compare-arrows", State.premise_corrections, "amber"),
            spacing="4",
            width="100%",
        ),
        empty("No explicit reasoning steps were recorded.", "brain"),
    )


def check_row(c) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(c.icon, size=16, color=f"var(--{c.color}-9)", flex_shrink="0", margin_top="2px"),
            rx.vstack(
                rx.text(c.instruction, size="2", weight="medium"),
                rx.text(c.check, size="1", color_scheme="gray"),
                rx.cond(c.evidence != "", rx.text(c.evidence, size="1", color_scheme="gray", style={"font_style": "italic"}), rx.fragment()),
                spacing="1",
                align="start",
            ),
            spacing="3",
            align="start",
            width="100%",
        ),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{c.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def citation_audit_row(r) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.code(r.marker, size="1")),
        rx.table.cell(rx.badge(r.status_label, color_scheme=r.color, variant="soft", size="1")),
        rx.table.cell(
            rx.cond(
                r.doc_url != "",
                rx.link(r.doc_title, href=r.doc_url, is_external=True, size="1"),
                rx.text(rx.cond(r.doc_title != "", r.doc_title, "—"), size="1", color_scheme="gray"),
            )
        ),
        rx.table.cell(rx.text(rx.cond(r.turn_index != "", r.turn_index, "—"), size="1", color_scheme="gray")),
        rx.table.cell(
            rx.hstack(
                rx.cond(
                    r.provenance != "",
                    rx.text(r.provenance, size="1", color_scheme="gray"),
                    rx.text("—", size="1", color_scheme="gray"),
                ),
                rx.cond(
                    r.cross_turn,
                    rx.badge("cross-turn", color_scheme="violet", variant="soft", size="1"),
                    rx.fragment(),
                ),
                spacing="2",
                align="center",
                wrap="wrap",
            )
        ),
    )


def citation_audit_block() -> rx.Component:
    return rx.cond(
        State.citation_rows.length() > 0,
        card(
            section_title("Citation verification", "quote"),
            rx.hstack(
                stat_card("Resolved", State.cit_resolved, "circle-check", "grass"),
                stat_card("Dangling", State.cit_dangling, "circle-x", rx.cond(State.cit_dangling > 0, "red", "gray")),
                stat_card(
                    "Uncited retrievals",
                    State.cit_uncited,
                    "circle-minus",
                    rx.cond(State.cit_uncited > 0, "amber", "gray"),
                ),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.box(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Marker"),
                            rx.table.column_header_cell("Status"),
                            rx.table.column_header_cell("Document"),
                            rx.table.column_header_cell("Turn"),
                            rx.table.column_header_cell("Provenance (search)"),
                        )
                    ),
                    rx.table.body(rx.foreach(State.citation_rows, citation_audit_row)),
                    variant="surface",
                    size="1",
                    width="100%",
                ),
                margin_top="12px",
                width="100%",
                overflow_x="auto",
            ),
        ),
        rx.fragment(),
    )


def credit_line_row(it) -> rx.Component:
    return rx.hstack(
        rx.icon(it.icon, size=14, color=f"var(--{it.color}-9)", flex_shrink="0"),
        rx.text(it.label, size="2"),
        rx.spacer(),
        rx.cond(it.detail != "", rx.text(it.detail, size="1", color_scheme="gray"), rx.fragment()),
        rx.badge(it.credits, color_scheme=it.color, variant="soft", size="1"),
        spacing="2",
        align="center",
        width="100%",
    )


def credit_block() -> rx.Component:
    return rx.cond(
        State.has_credits,
        card(
            rx.hstack(
                section_title("Credit estimate", "coins"),
                rx.spacer(),
                rx.cond(
                    State.credit_reasoning_model,
                    rx.badge(
                        rx.icon("brain", size=12),
                        "reasoning model",
                        color_scheme="purple",
                        variant="soft",
                        size="1",
                    ),
                    rx.fragment(),
                ),
                align="center",
                width="100%",
            ),
            rx.hstack(
                stat_card("Total credits", State.credit_total, "coins", "grass"),
                rx.cond(
                    State.credit_reasoning_model,
                    stat_card("Est. tokens", State.credit_total_tokens, "hash", "purple"),
                    rx.fragment(),
                ),
                rx.foreach(
                    State.credit_by_kind,
                    lambda k: stat_card(k.kind_label, k.credits, k.icon, k.color),
                ),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.vstack(
                rx.foreach(State.credit_lines, credit_line_row),
                spacing="2",
                width="100%",
                margin_top="14px",
            ),
            rx.cond(
                State.credit_assumptions.length() > 0,
                rx.box(
                    rx.hstack(
                        rx.icon("triangle-alert", size=13, color="var(--amber-9)"),
                        rx.text("Assumptions", size="1", weight="bold", color_scheme="amber"),
                        spacing="2",
                        align="center",
                    ),
                    rx.vstack(
                        rx.foreach(
                            State.credit_assumptions,
                            lambda a: rx.hstack(
                                rx.icon("dot", size=12, color="var(--amber-9)", flex_shrink="0", margin_top="3px"),
                                rx.text(a, size="1", color_scheme="gray"),
                                spacing="1",
                                align="start",
                            ),
                        ),
                        spacing="1",
                        width="100%",
                        margin_top="6px",
                    ),
                    margin_top="12px",
                    padding="10px 12px",
                    border_radius="8px",
                    background="var(--amber-a2)",
                    border="1px solid var(--amber-a5)",
                    width="100%",
                ),
                rx.fragment(),
            ),
            rx.box(
                rx.vstack(
                    rx.foreach(
                        State.credit_notes,
                        lambda n: rx.hstack(
                            rx.icon("info", size=12, color="var(--gray-a9)", flex_shrink="0", margin_top="3px"),
                            rx.text(n, size="1", color_scheme="gray"),
                            spacing="2",
                            align="start",
                        ),
                    ),
                    rx.cond(
                        State.credit_estimator_url != "",
                        rx.hstack(
                            rx.icon("external-link", size=12, color="var(--blue-9)", flex_shrink="0", margin_top="3px"),
                            rx.link(
                                "Open the official Copilot Studio credit estimator",
                                href=State.credit_estimator_url,
                                is_external=True,
                                size="1",
                            ),
                            spacing="2",
                            align="start",
                        ),
                        rx.fragment(),
                    ),
                    spacing="1",
                    width="100%",
                ),
                margin_top="14px",
                padding="10px 12px",
                border_radius="8px",
                background="var(--gray-a2)",
                border="1px dashed var(--gray-a6)",
                width="100%",
            ),
        ),
        rx.fragment(),
    )


def answer_grounding_row(a) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(a.icon, size=15, color=f"var(--{a.color}-9)", flex_shrink="0"),
            rx.badge(a.risk_label, variant="soft", color_scheme=a.color, size="1"),
            rx.text(a.turn_label, size="1", color_scheme="gray"),
            rx.spacer(),
            rx.text(a.claims_label, size="1", color_scheme="gray"),
            rx.text("·", size="1", color_scheme="gray"),
            rx.text(a.retrieval_label, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(a.excerpt != "", rx.text(a.excerpt, size="1", color_scheme="gray", margin_top="6px"), rx.fragment()),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{a.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def grounding_block() -> rx.Component:
    return rx.cond(
        State.answer_grounding.length() > 0,
        card(
            section_title("Per-answer groundedness", "scan-search"),
            rx.hstack(
                stat_card("High risk", State.ag_high, "triangle-alert", rx.cond(State.ag_high > 0, "red", "gray")),
                stat_card("Medium", State.ag_medium, "circle-alert", rx.cond(State.ag_medium > 0, "amber", "gray")),
                stat_card("Low", State.ag_low, "circle-check", "grass"),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.text(
                "High = factual claims with no citation despite a search returning documents. A heuristic signal, not a verdict.",
                size="1",
                color_scheme="gray",
                margin_top="8px",
            ),
            rx.vstack(rx.foreach(State.answer_grounding, answer_grounding_row), spacing="2", width="100%", margin_top="12px"),
        ),
        rx.fragment(),
    )


def repetition_row(r) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(r.icon, size=15, color=f"var(--{r.color}-9)", flex_shrink="0"),
            rx.badge(r.kind_label, variant="soft", color_scheme=r.color, size="1"),
            rx.text(r.turns_label, size="1", color_scheme="gray"),
            rx.spacer(),
            rx.badge(r.similarity, variant="soft", color_scheme="gray", size="1"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.cond(r.excerpt != "", rx.text(r.excerpt, size="1", color_scheme="gray", margin_top="6px"), rx.fragment()),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{r.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def repetition_block() -> rx.Component:
    return rx.cond(
        State.repetition.length() > 0,
        card(
            section_title("Repetition & loops", "repeat"),
            rx.vstack(rx.foreach(State.repetition, repetition_row), spacing="2", width="100%", margin_top="12px"),
        ),
        rx.fragment(),
    )


def quote_row(q) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(q.icon, size=15, color=f"var(--{q.color}-9)", flex_shrink="0"),
            rx.badge(q.verdict_label, variant="soft", color_scheme=q.color, size="1"),
            rx.text(q.turn_label, size="1", color_scheme="gray"),
            rx.spacer(),
            rx.cond(q.source_title != "", rx.text(q.source_title, size="1", color_scheme="gray"), rx.fragment()),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.text(
            q.excerpt,
            size="1",
            font_style="italic",
            color_scheme="gray",
            margin_top="6px",
            padding_left="10px",
            border_left="2px solid var(--gray-a6)",
        ),
        padding="12px 14px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{q.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def quote_block() -> rx.Component:
    return rx.cond(
        State.quote_rows.length() > 0,
        card(
            section_title("Quote traceability", "quote"),
            rx.hstack(
                stat_card("Verified", State.qf_verified, "circle-check", rx.cond(State.qf_verified > 0, "grass", "gray")),
                stat_card("In sandbox", State.qf_attributed, "file-check", rx.cond(State.qf_attributed > 0, "blue", "gray")),
                stat_card("Dangling", State.qf_dangling, "unlink", rx.cond(State.qf_dangling > 0, "red", "gray")),
                stat_card("Unattributed", State.qf_unattributed, "triangle-alert", rx.cond(State.qf_unattributed > 0, "amber", "gray")),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.text(
                "Modern RAG reads documents in a sandbox, so cited source text rarely reaches the transcript. "
                "'In sandbox' means the quote is attributed to a retrieved doc whose full text isn't transcript-verifiable.",
                size="1",
                color_scheme="gray",
                margin_top="8px",
            ),
            rx.vstack(rx.foreach(State.quote_rows, quote_row), spacing="2", width="100%", margin_top="12px"),
        ),
        rx.fragment(),
    )


def quality_panel() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            stat_card("Grounded", State.grounded, "circle-check", "grass"),
            stat_card("Ungrounded", State.ungrounded, "circle-alert", rx.cond(State.ungrounded > 0, "red", "gray")),
            stat_card("Citations", State.citation_markers, "quote", "blue"),
            stat_card("Uncited answers", State.uncited_answer_count, "message-square-warning", rx.cond(State.uncited_answer_count > 0, "amber", "gray")),
            spacing="3",
            width="100%",
            wrap="wrap",
        ),
        _chip_list("Hallucination risk", "triangle-alert", State.hallucination_risk, "red"),
        _chip_list("Honest grounding", "shield-check", State.honest_grounding, "grass"),
        grounding_block(),
        quote_block(),
        repetition_block(),
        citation_audit_block(),
        credit_block(),
        rx.cond(
            State.checks.length() > 0,
            card(
                section_title("Instruction compliance", "list-checks"),
                rx.vstack(rx.foreach(State.checks, check_row), spacing="2", width="100%", margin_top="12px"),
            ),
            empty("No instruction-compliance checks (no agent YAML).", "list-checks"),
        ),
        spacing="4",
        width="100%",
    )


def raw_inspector() -> rx.Component:
    return rx.cond(
        State.raw_transcript != "",
        rx.vstack(
            rx.button(
                rx.icon("code", size=14),
                rx.cond(State.raw_open, "Hide raw transcript", "Show raw transcript JSON"),
                on_click=State.toggle_raw,
                variant="soft",
                color_scheme="gray",
                size="1",
            ),
            rx.cond(
                State.raw_open,
                rx.box(
                    rx.code_block(State.raw_transcript, language="json", show_line_numbers=True, can_copy=True, wrap_long_lines=True),
                    width="100%",
                    max_height="420px",
                    overflow="auto",
                ),
                rx.fragment(),
            ),
            spacing="2",
            width="100%",
        ),
        rx.fragment(),
    )


def component_node_row(c) -> rx.Component:
    selected = c.id == State.selected_component.id
    chevron = rx.cond(
        c.is_branch,
        rx.icon(
            rx.cond(State.collapsed_nodes.contains(c.id), "chevron-right", "chevron-down"),
            size=14,
            color="var(--gray-9)",
            flex_shrink="0",
        ),
        rx.box(width="14px", flex_shrink="0"),
    )
    return rx.box(
        rx.hstack(
            chevron,
            rx.icon(c.icon, size=15, color="var(--grass-9)", flex_shrink="0"),
            rx.text(
                c.label,
                size="2",
                weight=rx.cond(c.is_branch, "bold", "regular"),
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
            ),
            rx.cond(
                c.kind_badge != "",
                rx.badge(c.kind_badge, color_scheme="grass", variant="soft", size="1", flex_shrink="0"),
                rx.fragment(),
            ),
            rx.spacer(),
            rx.cond(
                c.is_branch,
                rx.badge(c.child_count, color_scheme="gray", variant="soft", size="1", flex_shrink="0"),
                rx.cond(
                    (~c.documented) & (c.doc == ""),
                    rx.icon("circle-help", size=13, color="var(--amber-9)", flex_shrink="0"),
                    rx.fragment(),
                ),
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        on_click=lambda: State.on_node_click(c.id),
        cursor="pointer",
        padding="8px 10px",
        padding_left=c.indent,
        border_left=rx.cond(selected, "3px solid var(--grass-9)", "3px solid transparent"),
        background=rx.cond(
            selected,
            "var(--grass-a3)",
            rx.cond(c.is_branch, "var(--gray-a2)", "transparent"),
        ),
        border_radius="6px",
        width="100%",
    )


def component_detail() -> rx.Component:
    c = State.selected_component
    return rx.vstack(
        rx.hstack(
            rx.icon(c.icon, size=18, color="var(--grass-9)"),
            rx.heading(c.label, size="4"),
            rx.spacer(),
            rx.cond(
                c.doc != "",
                rx.badge("MS Learn", color_scheme="grass", variant="soft", size="1"),
                rx.cond(
                    ~c.documented,
                    rx.badge("Not documented", color_scheme="amber", variant="soft", size="1"),
                    rx.fragment(),
                ),
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        rx.hstack(
            rx.badge(c.category, color_scheme="gray", variant="soft", size="1"),
            rx.cond(
                c.kind_badge != "",
                rx.badge(c.kind_badge, color_scheme="grass", variant="soft", size="1"),
                rx.fragment(),
            ),
            spacing="2",
        ),
        rx.cond(
            c.value != "",
            rx.box(
                rx.text("Value", size="1", color_scheme="gray", weight="medium"),
                rx.code(c.value, size="2"),
                margin_top="6px",
            ),
            rx.fragment(),
        ),
        rx.box(
            rx.text("Explanation", size="1", color_scheme="gray", weight="medium"),
            rx.text(c.summary, size="2"),
            margin_top="10px",
            width="100%",
        ),
        rx.cond(
            c.doc != "",
            rx.link(
                rx.hstack(rx.icon("external-link", size=13), rx.text("Microsoft Learn reference", size="1"), spacing="1"),
                href=c.doc,
                is_external=True,
                margin_top="6px",
            ),
            rx.fragment(),
        ),
        spacing="2",
        align="start",
        width="100%",
    )


def components_panel() -> rx.Component:
    return rx.cond(
        State.component_nodes.length() > 0,
        rx.hstack(
            rx.box(
                rx.vstack(
                    rx.input(
                        rx.input.slot(rx.icon("search", size=14)),
                        placeholder="Filter components…",
                        value=State.component_query,
                        on_change=State.set_component_query,
                        size="2",
                        width="100%",
                    ),
                    rx.text(f"{State.component_count} component(s)", size="1", color_scheme="gray"),
                    rx.vstack(
                        rx.foreach(State.visible_nodes, component_node_row),
                        spacing="1",
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                ),
                width="360px",
                flex_shrink="0",
                max_height="70vh",
                overflow_y="auto",
                padding_right="4px",
            ),
            card(component_detail(), flex="1", align_self="flex-start"),
            spacing="4",
            width="100%",
            align="start",
        ),
        empty("No components to explore — upload an agent YAML to see its settings, knowledge and tools.", "boxes"),
    )


def timeline_event(e) -> rx.Component:
    return rx.hstack(
        rx.icon(e.icon, size=14, color=f"var(--{e.color}-9)", flex_shrink="0"),
        rx.text(e.label, size="1", weight="bold", color_scheme=e.color, width="120px", flex_shrink="0"),
        rx.text(e.text, size="1", color_scheme="gray"),
        spacing="2",
        align="start",
        width="100%",
    )


def timeline_turn(t) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon("git-commit-horizontal", size=15, color="var(--grass-9)"),
            rx.heading(t.title, size="3"),
            spacing="2",
            align="center",
        ),
        rx.vstack(
            rx.foreach(t.events, timeline_event),
            spacing="2",
            width="100%",
            margin_top="10px",
            padding_left="6px",
            border_left="2px solid var(--gray-a5)",
        ),
        padding="14px 16px",
        border="1px solid var(--gray-a5)",
        border_radius="12px",
        background="var(--color-panel-solid)",
        width="100%",
    )


def timeline_panel() -> rx.Component:
    return rx.cond(
        State.timeline.length() > 0,
        rx.vstack(
            rx.text(
                "Sequence of events per turn — user message, agent thoughts, tool calls (red = failed) and answers. "
                "Modern transcripts carry no timestamps, so this is ordering, not timing.",
                size="1",
                color_scheme="gray",
            ),
            rx.foreach(State.timeline, timeline_turn),
            spacing="3",
            width="100%",
        ),
        empty("No conversation to chart — upload a transcript to see its timeline.", "git-commit-horizontal"),
    )


def search_precision_row(s) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(s.icon, size=14, color=f"var(--{s.color}-9)", flex_shrink="0"),
            rx.code(s.query, size="1"),
            rx.spacer(),
            rx.badge(s.precision_label, color_scheme=s.color, variant="soft", size="1"),
            rx.text(s.turn_label, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        padding="10px 12px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{s.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def folder_row(f) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon("folder-tree", size=15, color="var(--amber-9)", flex_shrink="0"),
            rx.vstack(
                rx.text(f.path, size="2", weight="medium"),
                rx.cond(
                    f.doc_titles.length() > 0,
                    rx.text(f.doc_titles.join(" · "), size="1", color_scheme="gray"),
                    rx.fragment(),
                ),
                spacing="1",
                align="start",
            ),
            rx.spacer(),
            rx.badge(f.count_label, color_scheme="amber", variant="soft", size="1"),
            spacing="3",
            align="center",
            width="100%",
        ),
        padding="10px 12px",
        border="1px solid var(--gray-a5)",
        border_left="3px solid var(--amber-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def doc_retrieval_row(d) -> rx.Component:
    return rx.hstack(
        rx.icon(d.icon, size=14, color=f"var(--{d.color}-9)", flex_shrink="0"),
        rx.vstack(
            rx.text(d.title, size="2"),
            rx.text(d.turns_label, size="1", color_scheme="gray"),
            spacing="0",
            align="start",
        ),
        rx.spacer(),
        rx.cond(
            d.retrieval_count > 1,
            rx.badge(d.count_label, color_scheme="blue", variant="soft", size="1"),
            rx.fragment(),
        ),
        rx.badge(d.cited_label, color_scheme=d.color, variant="soft", size="1"),
        spacing="2",
        align="center",
        width="100%",
        wrap="wrap",
    )


def search_strategy_block() -> rx.Component:
    return rx.cond(
        State.has_search_strategy,
        card(
            section_title("Search strategy", "search-code"),
            rx.hstack(
                stat_card("Productive searches", State.ss_productive, "circle-check", "grass"),
                stat_card(
                    "Unproductive",
                    State.ss_unproductive,
                    "circle-x",
                    rx.cond(State.ss_unproductive > 0, "red", "gray"),
                ),
                stat_card("Answered from recall", State.recall_turns.length(), "history", "blue"),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.cond(
                State.search_precision.length() > 0,
                rx.vstack(
                    rx.text("Search → citation precision", size="1", weight="bold", color_scheme="gray", margin_top="14px"),
                    rx.foreach(State.search_precision, search_precision_row),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.recall_turns.length() > 0,
                rx.vstack(
                    rx.text("Answered from earlier retrieval", size="1", weight="bold", color_scheme="blue", margin_top="14px"),
                    rx.foreach(
                        State.recall_turns,
                        lambda t: rx.hstack(
                            rx.icon("history", size=14, color="var(--blue-9)", flex_shrink="0"),
                            rx.text(t.excerpt, size="2"),
                            rx.spacer(),
                            rx.text(t.turn_label, size="1", color_scheme="gray"),
                            spacing="2",
                            align="center",
                            width="100%",
                            wrap="wrap",
                        ),
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
        ),
        rx.fragment(),
    )


def retrieval_depth_block() -> rx.Component:
    return rx.cond(
        State.has_retrieval_depth,
        card(
            rx.hstack(
                section_title("Retrieval depth", "layers"),
                rx.spacer(),
                rx.badge(State.rd_mode, color_scheme="violet", variant="soft", size="1"),
                align="center",
                width="100%",
            ),
            rx.hstack(
                stat_card("Unique docs", State.rd_unique_docs, "files", "gray"),
                stat_card("Total retrieved", State.rd_total_retrieved, "copy", "gray"),
                stat_card(
                    "Cross-search overlap",
                    State.rd_overlap_docs,
                    "git-merge",
                    rx.cond(State.rd_overlap_docs > 0, "blue", "gray"),
                ),
                stat_card("Cited docs", State.rd_cited_docs, "circle-check", "grass"),
                stat_card(
                    "Over-retrieval",
                    State.rd_over_retrieval_label,
                    "package-x",
                    rx.cond(State.rd_over_retrieval_pct >= 80, "amber", "gray"),
                ),
                stat_card(
                    "Full doc reads",
                    State.rd_full_reads,
                    "file-search",
                    rx.cond(State.rd_full_reads > 0, "purple", "gray"),
                ),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.cond(
                State.rd_folders.length() > 0,
                rx.vstack(
                    rx.text("Document taxonomy (SharePoint folders)", size="1", weight="bold", color_scheme="gray", margin_top="14px"),
                    rx.foreach(State.rd_folders, folder_row),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.rd_docs.length() > 0,
                rx.vstack(
                    rx.text("Most-retrieved documents", size="1", weight="bold", color_scheme="gray", margin_top="14px"),
                    rx.foreach(State.rd_docs, doc_retrieval_row),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
        ),
        rx.fragment(),
    )


def grounding_doc_row(d) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(d.icon, size=14, color=f"var(--{d.color}-9)", flex_shrink="0"),
            rx.text(d.title, size="2", weight="medium"),
            rx.spacer(),
            rx.badge(d.cited_label, color_scheme=rx.cond(d.cited, "grass", "gray"), variant="soft", size="1"),
            spacing="2",
            align="center",
            width="100%",
            wrap="wrap",
        ),
        rx.hstack(
            rx.icon("git-branch", size=12, color="var(--gray-a8)", flex_shrink="0"),
            rx.text(d.chain_label, size="1", color_scheme="gray"),
            spacing="1",
            align="center",
            margin_top="4px",
        ),
        padding="10px 12px",
        border="1px solid var(--gray-a5)",
        border_left=f"3px solid var(--{d.color}-9)",
        border_radius="10px",
        background="var(--gray-a2)",
        width="100%",
    )


def grounding_pipeline_block() -> rx.Component:
    return rx.cond(
        State.has_grounding_pipeline,
        card(
            rx.hstack(
                section_title("Grounding pipeline", "git-branch"),
                rx.spacer(),
                rx.tooltip(
                    rx.icon("info", size=14, color="var(--gray-a9)"),
                    content="How knowledge search results turned into a grounded answer, and how precisely "
                    "the transcript lets you trace a citation back to a passage.",
                ),
                align="center",
                width="100%",
            ),
            rx.hstack(
                rx.box(
                    rx.text("Snippet mode", size="1", color_scheme="gray", weight="medium"),
                    rx.hstack(
                        rx.icon(State.gp_snippet_mode_icon, size=15, color=f"var(--{State.gp_snippet_mode_color}-9)"),
                        rx.badge(
                            State.gp_snippet_mode_label,
                            color_scheme=State.gp_snippet_mode_color,
                            variant="soft",
                            size="2",
                        ),
                        spacing="2",
                        align="center",
                        margin_top="4px",
                    ),
                    flex="1",
                    min_width="200px",
                ),
                rx.box(
                    rx.text("Citation precision", size="1", color_scheme="gray", weight="medium"),
                    rx.hstack(
                        rx.icon(State.gp_span_icon, size=15, color=f"var(--{State.gp_span_color}-9)"),
                        rx.badge(State.gp_span_label, color_scheme=State.gp_span_color, variant="soft", size="2"),
                        spacing="2",
                        align="center",
                        margin_top="4px",
                    ),
                    flex="1",
                    min_width="200px",
                ),
                spacing="3",
                width="100%",
                wrap="wrap",
                margin_top="12px",
            ),
            rx.cond(
                State.gp_notes.length() > 0,
                rx.vstack(
                    rx.foreach(
                        State.gp_notes,
                        lambda n: rx.hstack(
                            rx.icon("triangle-alert", size=13, color="var(--amber-9)", flex_shrink="0"),
                            rx.text(n, size="1", color_scheme="gray"),
                            spacing="2",
                            align="start",
                            width="100%",
                        ),
                    ),
                    spacing="2",
                    width="100%",
                    margin_top="12px",
                    padding="10px 12px",
                    border="1px solid var(--amber-a5)",
                    border_radius="10px",
                    background="var(--amber-a2)",
                ),
                rx.fragment(),
            ),
            rx.cond(
                State.grounding_docs.length() > 0,
                rx.vstack(
                    rx.text(
                        "Per-document grounding chain", size="1", weight="bold", color_scheme="gray", margin_top="14px"
                    ),
                    rx.foreach(State.grounding_docs, grounding_doc_row),
                    spacing="2",
                    width="100%",
                ),
                rx.fragment(),
            ),
        ),
        rx.fragment(),
    )


def knowledge_panel() -> rx.Component:
    return rx.cond(
        State.has_search_strategy | State.has_retrieval_depth | (State.knowledge_queries.length() > 0),
        rx.vstack(
            search_strategy_block(),
            retrieval_depth_block(),
            grounding_pipeline_block(),
            knowledge_effectiveness_block(),
            coverage_block(),
            rx.cond(
                State.knowledge_queries.length() > 0,
                card(
                    section_title("Knowledge queries", "search"),
                    rx.vstack(rx.foreach(State.knowledge_queries, knowledge_query_card), spacing="2", width="100%", margin_top="12px"),
                ),
                rx.fragment(),
            ),
            spacing="4",
            width="100%",
        ),
        empty("No knowledge searches or retrieval happened in this conversation.", "book-open"),
    )


def active_panel() -> rx.Component:
    return rx.match(
        State.active_tab,
        ("overview", overview_panel()),
        ("agent", agent_panel()),
        ("conversation", conversation_panel()),
        ("tools", tools_panel()),
        ("knowledge", knowledge_panel()),
        ("reasoning", reasoning_panel()),
        ("quality", quality_panel()),
        ("timeline", timeline_panel()),
        ("components", components_panel()),
        overview_panel(),
    )
