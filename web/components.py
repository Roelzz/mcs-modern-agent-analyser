"""Reflex UI shell: header, upload, dashboard, tab bar, native report panels."""

import reflex as rx

from web.mermaid import mermaid_script
from web.report import active_panel
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
                rx.button(
                    rx.icon("plus", size=15),
                    "Add data",
                    on_click=State.back_to_input,
                    variant="soft",
                    color_scheme="grass",
                    size="2",
                    class_name="no-print",
                ),
                rx.button(
                    rx.icon("rotate-ccw", size=15),
                    "New",
                    on_click=State.clear_all,
                    variant="soft",
                    color_scheme="gray",
                    size="2",
                    class_name="no-print",
                ),
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


def _dv_field(label: str, helper: str, placeholder: str, value, on_change, **kwargs) -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="medium"),
        rx.input(
            placeholder=placeholder,
            value=value,
            on_change=on_change,
            width="100%",
            font_family="var(--code-font-family, monospace)",
            **kwargs,
        ),
        rx.text(helper, size="1", color_scheme="gray"),
        spacing="1",
        width="100%",
    )


def _dataverse_connection_form() -> rx.Component:
    return rx.vstack(
        rx.callout(
            "Paste Session details from Copilot Studio to auto-fill the environment, tenant, and Copilot ID.",
            icon="info",
            color_scheme="grass",
            size="1",
            width="100%",
        ),
        rx.text_area(
            placeholder=(
                "Tenant ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\n"
                "Instance url: https://yourorg.crm4.dynamics.com\n"
                "Copilot Id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            ),
            value=State.dv_session_details_paste,
            on_change=State.set_dv_session_details_paste,
            min_height="100px",
            font_family="var(--code-font-family, monospace)",
            width="100%",
        ),
        rx.button(
            rx.icon("clipboard-paste", size=15),
            "Auto-fill",
            on_click=State.dv_autofill_from_session_details,
            variant="outline",
            color_scheme="grass",
            width="100%",
        ),
        rx.hstack(
            rx.icon("database", size=13, color="var(--gray-9)"),
            rx.text("Connection details are remembered in this browser.", size="1", color_scheme="gray"),
            rx.spacer(),
            rx.button(
                "Forget",
                on_click=State.dv_forget_connection_info,
                variant="ghost",
                color_scheme="gray",
                size="1",
            ),
            width="100%",
            align="center",
            spacing="2",
        ),
        rx.cond(
            State.dv_autofill_error != "",
            rx.callout(State.dv_autofill_error, icon="triangle-alert", color_scheme="amber", size="1"),
        ),
        _dv_field(
            "Environment URL",
            "Settings → Session details → Instance url",
            "https://yourorg.crm4.dynamics.com",
            State.dv_org_url,
            State.set_dv_org_url,
        ),
        _dv_field(
            "Tenant ID",
            "Settings → Session details → Tenant ID",
            "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            State.dv_tenant_id,
            State.set_dv_tenant_id,
        ),
        _dv_field(
            "Client ID",
            "The Microsoft Azure CLI client ID works in most tenants; use your own public-client app if blocked.",
            "04b07795-8ddb-461a-bbee-02f9e1bf7b46",
            State.dv_client_id,
            State.set_dv_client_id,
        ),
        _dv_field(
            "Copilot ID",
            "Settings → Session details → Copilot Id",
            "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            State.dv_bot_identifier,
            State.set_dv_bot_identifier,
        ),
        rx.cond(
            State.dv_auth_error != "",
            rx.callout(State.dv_auth_error, icon="triangle-alert", color_scheme="red", size="1"),
        ),
        rx.cond(
            State.dv_show_device_code,
            rx.callout(
                rx.vstack(
                    rx.text("Enter this code to sign in:", size="2"),
                    rx.text(
                        State.dv_device_code,
                        font_family="var(--code-font-family, monospace)",
                        font_size="24px",
                        font_weight="600",
                        letter_spacing="2px",
                    ),
                    rx.link(
                        "Open Microsoft device login",
                        href=State.dv_device_code_url,
                        is_external=True,
                    ),
                    align="center",
                    spacing="2",
                    width="100%",
                ),
                icon="key-round",
                color_scheme="grass",
                width="100%",
            ),
        ),
        rx.hstack(
            rx.button(
                rx.cond(
                    State.dv_is_authenticating,
                    rx.hstack(rx.spinner(size="1"), rx.text("Waiting for sign-in…"), spacing="2"),
                    rx.hstack(rx.icon("plug", size=15), rx.text("Connect to Dataverse"), spacing="2"),
                ),
                on_click=State.start_device_flow,
                disabled=State.dv_is_authenticating,
                color_scheme="grass",
                size="3",
                flex="1",
            ),
            rx.cond(
                State.dv_is_authenticating,
                rx.button(
                    "Cancel",
                    on_click=State.dv_cancel_auth,
                    variant="outline",
                    color_scheme="gray",
                    size="3",
                ),
                rx.fragment(),
            ),
            width="100%",
            spacing="2",
        ),
        rx.text(
            "Requires ConversationTranscript Read access. Tokens and transcript bodies remain server-side.",
            size="1",
            color_scheme="gray",
        ),
        spacing="3",
        width="100%",
    )


def _dataverse_row(transcript: dict) -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.hstack(
                rx.text(transcript["created_on"], size="1", weight="medium"),
                rx.text(transcript["short_id"], size="1", color_scheme="gray", font_family="monospace"),
                rx.badge(transcript["activity_label"], variant="soft", size="1"),
                spacing="2",
                align="center",
            ),
            rx.text(
                transcript["preview"],
                size="2",
                color_scheme="gray",
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
                width="100%",
            ),
            spacing="1",
            min_width="0",
            flex="1",
        ),
        rx.button(
            rx.icon("zap", size=14),
            "Analyse",
            on_click=State.dv_analyse_transcript(transcript["id"]),
            color_scheme="grass",
            variant="soft",
            size="2",
        ),
        width="100%",
        align="center",
        spacing="3",
        padding="10px 12px",
        border_bottom="1px solid var(--gray-a4)",
    )


def _dataverse_connected() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.badge(rx.icon("circle-check", size=12), "Connected", color_scheme="grass", variant="soft"),
            rx.spacer(),
            rx.button(
                rx.icon("unplug", size=14),
                "Disconnect",
                on_click=State.dv_disconnect,
                variant="outline",
                color_scheme="gray",
                size="2",
            ),
            width="100%",
            align="center",
        ),
        _dv_field(
            "Copilot ID",
            "The bot whose transcripts should be listed",
            "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            State.dv_bot_identifier,
            State.set_dv_bot_identifier,
        ),
        rx.hstack(
            _dv_field(
                "Since date",
                "Transcripts created after this date",
                "",
                State.dv_since_date,
                State.set_dv_since_date,
                type="date",
            ),
            _dv_field(
                "Maximum results",
                "Between 1 and 250",
                "50",
                State.dv_top_n.to(str),
                State.set_dv_top_n,
                type="number",
            ),
            width="100%",
            spacing="3",
        ),
        rx.button(
            rx.cond(
                State.dv_is_fetching,
                rx.hstack(rx.spinner(size="1"), rx.text("Fetching..."), spacing="2"),
                rx.hstack(rx.icon("refresh-cw", size=14), rx.text("Fetch transcripts"), spacing="2"),
            ),
            on_click=State.dv_fetch_transcripts,
            disabled=State.dv_is_fetching,
            color_scheme="grass",
            width="100%",
        ),
        rx.separator(),
        rx.text("Direct conversation lookup", size="2", weight="medium"),
        rx.text(
            "Enter the ConversationTranscript row ID when you already know the exact conversation.",
            size="1",
            color_scheme="gray",
        ),
        rx.hstack(
            rx.input(
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                value=State.dv_conversation_id,
                on_change=State.set_dv_conversation_id,
                font_family="monospace",
                width="100%",
            ),
            rx.button(
                rx.cond(State.dv_single_fetching, rx.spinner(size="1"), rx.icon("search", size=14)),
                "Fetch & analyse",
                on_click=State.dv_fetch_and_analyse_by_id,
                disabled=State.dv_single_fetching,
                color_scheme="grass",
                white_space="nowrap",
            ),
            width="100%",
            spacing="2",
        ),
        rx.cond(
            State.dv_fetch_error != "",
            rx.callout(State.dv_fetch_error, icon="triangle-alert", color_scheme="red", size="1"),
        ),
        rx.cond(
            State.dv_single_fetch_error != "",
            rx.callout(State.dv_single_fetch_error, icon="triangle-alert", color_scheme="red", size="1"),
        ),
        rx.cond(
            State.dv_has_transcripts,
            rx.box(
                rx.foreach(State.dv_transcripts, _dataverse_row),
                border="1px solid var(--gray-a4)",
                border_radius="10px",
                overflow="hidden",
                width="100%",
            ),
        ),
        spacing="3",
        width="100%",
    )


def _dataverse_tab() -> rx.Component:
    return rx.vstack(
        rx.text(
            "Connect with delegated device-code authentication and analyse a transcript directly from Dataverse.",
            size="1",
            color_scheme="gray",
        ),
        rx.cond(State.dv_is_connected, _dataverse_connected(), _dataverse_connection_form()),
        width="100%",
        spacing="3",
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
                rx.tabs.trigger(
                    rx.hstack(rx.icon("database", size=15), rx.text("Dataverse"), spacing="2", align="center"),
                    value="dataverse",
                ),
            ),
            rx.tabs.content(_upload_tab(), value="upload", padding_top="18px"),
            rx.tabs.content(_paste_tab(), value="paste", padding_top="18px"),
            rx.tabs.content(_dataverse_tab(), value="dataverse", padding_top="18px"),
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
                rx.cond(
                    State.memory,
                    rx.badge(rx.icon("save", size=12), "memory", color_scheme="blue", variant="soft", size="2"),
                    rx.fragment(),
                ),
                align="center",
                spacing="2",
                wrap="wrap",
            ),
            spacing="1",
            align="start",
        ),
        rx.spacer(),
        rx.hstack(
            rx.cond(
                State.has_transcript,
                rx.button(
                    rx.icon("braces", size=15),
                    "Raw JSON",
                    on_click=State.download_raw_transcript,
                    variant="soft",
                    size="2",
                ),
                rx.fragment(),
            ),
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
