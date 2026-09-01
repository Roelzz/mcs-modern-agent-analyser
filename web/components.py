"""Reflex UI shell: header, upload, dashboard, tab bar, native report panels."""

import reflex as rx

from web.mermaid import mermaid_script
from web.report import active_panel, raw_inspector
from web.state import TAB_DEFS, State


def header() -> rx.Component:
    return rx.hstack(
        rx.hstack(
            rx.icon("scan-search", size=26, color="var(--grass-9)"),
            rx.heading("Agent Analyser — Modern", size="5"),
            rx.badge("cliagent", color_scheme="grass", variant="soft"),
            align="center",
            spacing="3",
        ),
        rx.spacer(),
        rx.tooltip(
            rx.hstack(
                rx.text(State.cat_emoji, font_size="16px"),
                rx.text(
                    State.analyses_count,
                    font_family="monospace",
                    font_weight="600",
                    color="var(--grass-11)",
                    class_name=rx.cond(State.counter_animating, "counter-pop", ""),
                ),
                rx.cond(
                    State.milestone_reached,
                    rx.text("\U0001f389", font_size="13px", class_name="milestone-flash"),
                    rx.fragment(),
                ),
                align="center",
                spacing="2",
                padding="3px 10px",
                background="var(--grass-a2)",
                border="1px solid var(--grass-a5)",
                border_radius="20px",
            ),
            content=State.cat_title,
        ),
        rx.cond(
            State.has_report,
            rx.hstack(
                rx.button(rx.icon("plus", size=15), "Add data", on_click=State.back_to_input, variant="soft", color_scheme="grass", size="2", class_name="no-print"),
                rx.button(rx.icon("rotate-ccw", size=15), "New", on_click=State.clear_all, variant="soft", color_scheme="gray", size="2", class_name="no-print"),
                spacing="2",
            ),
            rx.fragment(),
        ),
        rx.color_mode.button(),
        width="100%",
        padding="14px 20px",
        border_bottom="1px solid var(--gray-a5)",
        position="sticky",
        top="0",
        background="var(--color-background)",
        z_index="20",
        class_name="no-print",
    )


def _upload_tab() -> rx.Component:
    return rx.vstack(
        rx.upload(
            rx.vstack(
                rx.icon("upload", size=30, color="var(--grass-9)"),
                rx.text("Drag files here or click to browse", size="2", weight="medium"),
                rx.text(".json transcript · .yaml / .yml agent", size="1", color_scheme="gray"),
                align="center",
                spacing="2",
            ),
            id="upload",
            multiple=True,
            accept={
                "application/json": [".json"],
                "application/x-yaml": [".yaml", ".yml"],
                "text/yaml": [".yaml", ".yml"],
            },
            max_files=2,
            border="2px dashed var(--gray-a7)",
            border_radius="12px",
            padding="34px",
            width="100%",
        ),
        rx.hstack(
            rx.foreach(
                rx.selected_files("upload"),
                lambda f: rx.badge(f, variant="soft", color_scheme="grass"),
            ),
            spacing="2",
            wrap="wrap",
        ),
        rx.hstack(
            rx.button(
                "Analyse",
                on_click=[State.handle_upload(rx.upload_files("upload")), State.refresh_counter],
                disabled=(rx.selected_files("upload").length() == 0) & ~State.can_analyse,
                color_scheme="grass",
                size="3",
            ),
            rx.spacer(),
            rx.button("Reset", on_click=State.clear_all, variant="soft", color_scheme="gray", size="3"),
            width="100%",
            spacing="3",
        ),
        spacing="3",
        width="100%",
    )


def _paste_tab() -> rx.Component:
    return rx.vstack(
        rx.text(
            "Paste the JSON straight from the Dataverse conversationtranscript row — both the "
            'flat message array and the { "activities": [ ... ] } envelope are detected automatically.',
            size="1",
            color_scheme="gray",
        ),
        rx.debounce_input(
            rx.text_area(
                value=State.paste_text,
                on_change=State.set_paste_text,
                placeholder='{ "activities": [ { "type": "message", "from": { "role": 0 }, "text": "..." } ] }',
                rows="14",
                spell_check=False,
                font_family="var(--code-font-family, monospace)",
                font_size="12px",
                width="100%",
                resize="vertical",
            ),
            debounce_timeout=300,
        ),
        rx.cond(
            State.paste_preview != "",
            rx.hstack(
                rx.icon(
                    rx.cond(State.paste_is_valid, "circle-check", "circle-alert"),
                    size=14,
                    color=rx.cond(State.paste_is_valid, "var(--grass-9)", "var(--red-9)"),
                ),
                rx.text(
                    State.paste_preview,
                    size="1",
                    color_scheme=rx.cond(State.paste_is_valid, "grass", "red"),
                ),
                rx.spacer(),
                rx.text(State.paste_text.length().to_string() + " chars", size="1", color_scheme="gray"),
                width="100%",
                align="center",
                spacing="2",
            ),
        ),
        rx.hstack(
            rx.button(
                "Analyse",
                on_click=[State.analyse_pasted, State.refresh_counter],
                disabled=~State.paste_is_valid,
                color_scheme="grass",
                size="3",
            ),
            rx.spacer(),
            rx.button("Clear", on_click=State.clear_paste, variant="soft", color_scheme="gray", size="3"),
            width="100%",
            spacing="3",
        ),
        spacing="3",
        width="100%",
    )


def upload_zone() -> rx.Component:
    return rx.vstack(
        rx.heading("Analyse a modern agent", size="6"),
        rx.text(
            "Drop a transcript JSON and/or an agent YAML (BotDefinition), or paste the transcript "
            "JSON straight in. Either one works — both together gives the full cross-referenced report.",
            size="2",
            color_scheme="gray",
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger(
                    rx.hstack(rx.icon("upload", size=15), rx.text("Upload files"), spacing="2", align="center"),
                    value="upload",
                ),
                rx.tabs.trigger(
                    rx.hstack(rx.icon("clipboard-paste", size=15), rx.text("Paste JSON"), spacing="2", align="center"),
                    value="paste",
                ),
            ),
            rx.tabs.content(_upload_tab(), value="upload", padding_top="18px"),
            rx.tabs.content(_paste_tab(), value="paste", padding_top="18px"),
            default_value="upload",
            width="100%",
        ),
        rx.divider(),
        rx.text("Or load a bundled sample:", size="2", color_scheme="gray"),
        rx.hstack(
            rx.button(
                rx.icon("book-open", size=16),
                "Knowledge agent",
                on_click=lambda: [State.load_sample("knowledge"), State.refresh_counter],
                variant="soft",
                size="3",
            ),
            rx.button(
                rx.icon("bot", size=16),
                "Autonomous agent",
                on_click=lambda: [State.load_sample("agentic"), State.refresh_counter],
                variant="soft",
                size="3",
            ),
            rx.button(
                rx.icon("terminal", size=16),
                "Code interpreter",
                on_click=lambda: [State.load_sample("sandbox"), State.refresh_counter],
                variant="soft",
                size="3",
            ),
            rx.button(
                rx.icon("presentation", size=16),
                "Generated deck",
                on_click=lambda: [State.load_sample("deck"), State.refresh_counter],
                variant="soft",
                size="3",
            ),
            rx.button(
                rx.icon("plug-zap", size=16),
                "Connector failure",
                on_click=lambda: [State.load_sample("connector"), State.refresh_counter],
                variant="soft",
                color_scheme="red",
                size="3",
            ),
            spacing="3",
            wrap="wrap",
        ),
        rx.cond(State.status != "", rx.callout(State.status, icon="info", size="1", color_scheme="grass")),
        rx.cond(State.error != "", rx.callout(State.error, icon="triangle-alert", size="1", color_scheme="red")),
        spacing="4",
        width="100%",
        max_width="720px",
        padding="40px 28px",
    )


def identity_bar() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.hstack(
                rx.heading(State.agent_title, size="6"),
                rx.cond(
                    State.agent_present,
                    rx.badge(State.model_label, color_scheme="grass", variant="soft", size="2"),
                    rx.badge("transcript-only", color_scheme="amber", variant="soft", size="2"),
                ),
                rx.cond(
                    State.template != "",
                    rx.badge(State.template, color_scheme="gray", variant="soft", size="2"),
                    rx.fragment(),
                ),
                rx.cond(State.memory, rx.badge(rx.icon("save", size=12), "memory", color_scheme="blue", variant="soft", size="2"), rx.fragment()),
                align="center",
                spacing="2",
                wrap="wrap",
            ),
            spacing="1",
            align="start",
        ),
        rx.spacer(),
        rx.hstack(
            rx.button(rx.icon("download", size=15), "MD", on_click=State.download_md, variant="soft", size="2"),
            rx.button(rx.icon("file-code", size=15), "HTML", on_click=State.download_html, variant="soft", size="2"),
            rx.button(rx.icon("printer", size=15), "Print", on_click=State.print_report, variant="soft", size="2"),
            spacing="2",
            class_name="no-print",
        ),
        width="100%",
        align="center",
        wrap="wrap",
        spacing="3",
    )


def tab_bar() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.foreach(
                TAB_DEFS,
                lambda t: rx.button(
                    t[1],
                    on_click=lambda: State.set_tab(t[0]),
                    variant=rx.cond(State.active_tab == t[0], "solid", "soft"),
                    color_scheme=rx.cond(State.active_tab == t[0], "grass", "gray"),
                    size="2",
                ),
            ),
            spacing="2",
            wrap="wrap",
            width="100%",
        ),
        position="sticky",
        top="61px",
        background="var(--color-background)",
        padding="10px 0",
        z_index="15",
        width="100%",
        class_name="no-print",
    )


def report_view() -> rx.Component:
    return rx.vstack(
        identity_bar(),
        tab_bar(),
        rx.box(active_panel(), width="100%", id="report-content"),
        raw_inspector(),
        spacing="4",
        width="100%",
        max_width="1040px",
        padding="24px 28px 64px",
    )


def index() -> rx.Component:
    return rx.vstack(
        mermaid_script(),
        header(),
        rx.center(
            rx.cond(State.has_report, report_view(), upload_zone()),
            width="100%",
        ),
        spacing="0",
        width="100%",
        min_height="100vh",
    )
