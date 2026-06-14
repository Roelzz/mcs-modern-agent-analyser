from pathlib import Path

import pytest

from agent_parser import parse_agent_yaml
from analysis import analyze
from transcript_parser import parse_transcript
from web.view_models import classify_tool, map_report

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def knowledge_vm():
    profile = parse_agent_yaml(FIX / "sample_agent.yaml")
    convo = parse_transcript(FIX / "sample_transcript.json")
    return map_report(analyze(profile, convo), convo)


@pytest.fixture(scope="module")
def agentic_vm():
    convo = parse_transcript(FIX / "sample_transcript_agentic.json")
    return map_report(analyze(None, convo), convo)


def test_knowledge_vm_basics(knowledge_vm):
    vm = knowledge_vm
    assert vm.has_agent and vm.has_convo
    assert vm.agent_name == "Knowledge agent"
    assert vm.model_label == "Claude Sonnet 4.6"
    assert vm.m_turns == 3
    assert len(vm.findings) == vm.f_critical + vm.f_warning + vm.f_info
    assert "sequenceDiagram" in vm.mermaid
    assert not vm.mermaid.lstrip().startswith("```")  # fence stripped for <pre class=mermaid>
    # Chat blocks: one per message.
    assert len(vm.chat) == 7
    assert vm.chat[0].kind == "agent"  # greeting
    assert vm.chat[1].kind == "user"


def test_knowledge_vm_retrieval_tool(knowledge_vm):
    searches = [tc for b in knowledge_vm.chat for tc in b.tool_calls if tc.kind == "retrieval"]
    assert searches
    assert searches[0].docs
    assert searches[0].docs[0].reference_id.startswith("turn")


def test_agentic_vm_tool_taxonomy(agentic_vm):
    vm = agentic_vm
    assert not vm.has_agent  # transcript only
    kinds = {r.name: r.kind for r in vm.tool_rows}
    assert kinds.get("KnowledgeSearch") == "retrieval"
    assert kinds.get("SendMessageToUser") == "action"
    assert kinds.get("SendMessageToSelf") == "action"
    assert kinds.get("ListChats") == "action"


def test_agentic_vm_action_fields(agentic_vm):
    actions = [tc for b in agentic_vm.chat for tc in b.tool_calls if tc.kind == "action"]
    sends = [a for a in actions if a.name == "SendMessageToUser"]
    assert sends
    a = sends[0]
    assert a.content_type.lower() == "html"
    assert a.content_html  # HTML content captured
    assert "@" in a.recipient
    # `content` must not leak into the generic params list.
    assert all(kv.key.lower() != "content" for kv in a.params)


def test_cross_turn_citation_mapping(agentic_vm):
    # Answers in later turns cite [1] reusing the single earlier KnowledgeSearch.
    cited = [c for b in agentic_vm.chat for c in b.citations if c.label == "[1]"]
    assert cited
    assert any(c.reference_id for c in cited)  # at least one [1] maps to a real doc


def test_turn_breakdown(agentic_vm):
    assert len(agentic_vm.turns) == 7
    last = agentic_vm.turns[-1]
    assert last.actions  # final turn performs actions


def test_classify_tool_other():
    from models import ToolCall

    assert classify_tool(ToolCall(name="WeirdThing", params={})) == "other"
    assert classify_tool(ToolCall(name="skill", display_name="Loaded Skill: x")) == "skill"
