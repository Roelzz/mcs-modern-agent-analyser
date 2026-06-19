"""Tests for the hierarchical Component Explorer (Agent → Knowledge →
Tools[grouped by provider kind → operations] → Environment variables)."""

from pathlib import Path

import pytest

from agent_parser import parse_agent_yaml
from analysis import analyze, build_tool_hierarchy, classify_runtime_provider
from models import ToolCall
from renderer import render_components
from transcript_parser import parse_transcript
from web.view_models import map_report

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def mcp():
    profile = parse_agent_yaml(FIX / "sample_agent_mcp.yaml")
    convo = parse_transcript(FIX / "sample_transcript_mcp.json")
    return profile, convo, analyze(profile, convo)


@pytest.fixture(scope="module")
def agentic():
    convo = parse_transcript(FIX / "sample_transcript_agentic.json")
    return None, convo, analyze(None, convo)


# --- Parser: provider/operation extraction ---------------------------------


def test_parser_extracts_providers_and_operations():
    profile = parse_agent_yaml(FIX / "sample_agent_mcp.yaml")
    by_name = {p.display_name: p for p in profile.tool_providers}
    assert by_name["Zava Expense MCP"].kind == "mcpServer"  # 'mcp' detected
    assert by_name["MSN Weather"].kind == "connector"
    assert by_name["HR Assistant"].kind == "connectedAgent"
    assert by_name["Notify Manager"].kind == "flow"
    mcp_ops = {o.name for o in by_name["Zava Expense MCP"].operations}
    assert mcp_ops == {"create_new_expense_report", "get_expense_categories", "submit_expense_report"}


def test_parser_no_providers_for_knowledge_agent():
    profile = parse_agent_yaml(FIX / "sample_agent.yaml")
    assert profile.tool_providers == []


# --- Runtime provider classification ---------------------------------------


def test_classify_runtime_mcp_colon_split():
    tc = ToolCall(name="ZavaExpenseMCP:create_new_expense_report", status="completed", params={"content": "x"})
    assert classify_runtime_provider(tc) == ("mcpServer", "ZavaExpenseMCP", "create_new_expense_report", None)


def test_classify_runtime_skill_strips_prefix():
    tc = ToolCall(name="skill", display_name="Loaded Skill: analyzing-docx", status="completed")
    kind, provider, op, _ = classify_runtime_provider(tc)
    assert (kind, provider, op) == ("skill", "Skills", "analyzing-docx")


def test_classify_runtime_plain_action_bucketed():
    tc = ToolCall(name="SendMessageToUser", display_name="SendMessageToUser", status="completed", params={"recipient": "u"})
    kind, provider, op, _ = classify_runtime_provider(tc)
    assert (kind, provider, op) == ("action", "Agent actions", "SendMessageToUser")


def test_classify_runtime_ignores_knowledge_search():
    tc = ToolCall(name="KnowledgeSearch", status="completed", params={"query": "q"})
    assert classify_runtime_provider(tc) is None


# --- Hierarchy build + merge -----------------------------------------------


def test_hierarchy_merges_runtime_into_declared_provider(mcp):
    profile, convo, _ = mcp
    providers = build_tool_hierarchy(profile, convo)
    names = [p.display_name for p in providers]
    # Runtime 'ZavaExpenseMCP:...' must NOT fork a duplicate of 'Zava Expense MCP'.
    assert names.count("Zava Expense MCP") == 1
    assert "ZavaExpenseMCP" not in names
    # Connected-agent runtime call keeps the declared connectedAgent kind.
    hr = next(p for p in providers if p.display_name == "HR Assistant")
    assert hr.kind == "connectedAgent"


def test_hierarchy_runtime_only_bucket(agentic):
    _, convo, _ = agentic
    providers = build_tool_hierarchy(None, convo)
    assert len(providers) == 1
    assert providers[0].kind == "action"
    assert {o.name for o in providers[0].operations} == {"SendMessageToUser", "ListChats", "SendMessageToSelf"}


def test_build_tool_hierarchy_does_not_mutate_profile(mcp):
    profile, convo, _ = mcp
    before = [(p.display_name, len(p.operations)) for p in profile.tool_providers]
    build_tool_hierarchy(profile, convo)
    after = [(p.display_name, len(p.operations)) for p in profile.tool_providers]
    assert before == after


# --- View-model tree --------------------------------------------------------


def test_component_nodes_tree_shape(mcp):
    profile, convo, report = mcp
    nodes = map_report(report, convo).component_nodes
    groups = [n.label for n in nodes if n.node_type == "group"]
    assert groups == ["Agent", "Knowledge sources", "Tools", "Environment variables"]
    # Provider nodes live under the Tools group (depth 1) and carry a kind badge.
    providers = [n for n in nodes if n.node_type == "provider"]
    assert {n.kind_badge for n in providers} == {"MCP server", "Connector", "Connected agent", "Flow", "Action"}
    assert all(n.parent_id == "g-tools" and n.depth == 1 for n in providers)


def test_component_nodes_operations_nested_under_provider(mcp):
    profile, convo, report = mcp
    nodes = map_report(report, convo).component_nodes
    mcp_provider = next(n for n in nodes if n.node_type == "provider" and n.label == "Zava Expense MCP")
    ops = [n for n in nodes if n.parent_id == mcp_provider.id]
    assert len(ops) == 3
    assert all(n.depth == 2 and n.node_type == "leaf" for n in ops)
    # Declared ops show their own description (no invented doc link).
    create = next(n for n in ops if n.label == "Create new expense report")
    assert create.summary == "Creates a draft expense report for the current user."
    assert create.doc == ""
    # An op WITHOUT its own description inherits the grounded provider doc.
    flow_provider = next(n for n in nodes if n.node_type == "provider" and n.label == "Notify Manager")
    run = next(n for n in nodes if n.parent_id == flow_provider.id)
    assert "advanced-use-flow" in run.doc


def test_component_nodes_branch_flags_and_indent(mcp):
    profile, convo, report = mcp
    nodes = map_report(report, convo).component_nodes
    for n in nodes:
        assert n.is_branch == (n.node_type in {"group", "provider"})
    # Indent grows with depth.
    depths = {n.depth: n.indent for n in nodes}
    assert depths[0] != depths[1] != depths[2]


# --- Nested export ----------------------------------------------------------


def test_render_components_nested_hierarchy(mcp):
    profile, convo, report = mcp
    md = render_components(report, convo)
    assert "### Agent" in md
    assert "### Knowledge sources" in md
    assert "### Tools" in md
    assert "### Environment variables" in md
    # Provider header + indented operation bullets.
    assert "**MCP server: Zava Expense MCP**" in md
    assert "  - `Create new expense report`" in md
    assert "[Learn](https://learn.microsoft.com/microsoft-copilot-studio/mcp-add-components-to-agent)" in md


def test_render_components_groups_connected_agent_and_flow(mcp):
    profile, convo, report = mcp
    md = render_components(report, convo)
    assert "**Connected agent: HR Assistant**" in md
    assert "**Flow: Notify Manager**" in md
    assert "**Connector: MSN Weather**" in md
