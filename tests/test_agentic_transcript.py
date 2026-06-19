from pathlib import Path

import pytest

from analysis import analyze
from transcript_parser import parse_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript_agentic.json"


@pytest.fixture(scope="module")
def convo():
    return parse_transcript(FIXTURE)


def test_message_and_turn_counts(convo):
    assert len(convo.messages) == 15
    assert len(convo.user_messages) == 6
    assert len(convo.bot_messages) == 9
    # Leading greeting + 6 user-led turns.
    assert len(convo.turns) == 7
    assert convo.turns[0].user_message is None
    # Agentic multi-message tail: final turn fans out into 3 bot messages.
    assert len(convo.turns[-1].bot_messages) == 3


def test_action_tools_present(convo):
    names = sorted({(t.name or "") for t in convo.tool_calls})
    assert names == ["KnowledgeSearch", "ListChats", "SendMessageToSelf", "SendMessageToUser"]
    assert len(convo.tool_calls) == 5


def test_send_message_params(convo):
    sends = [t for t in convo.tool_calls if t.name == "SendMessageToUser"]
    assert len(sends) == 2
    first = sends[0]
    assert first.params.get("contentType") == "html"
    assert "@" in first.params.get("userIdOrUpn", "")
    assert "<b>" in (first.params.get("content") or "")


def test_knowledge_search_many_docs(convo):
    ks = [t for t in convo.tool_calls if t.is_knowledge_search]
    assert len(ks) == 1
    assert ks[0].result_count == 10
    assert len(ks[0].retrieved_docs) == 10


def test_analysis_without_agent_yaml(convo):
    report = analyze(None, convo)
    assert report.agent is None
    assert report.overview is not None
    assert report.overview.tool_call_count == 5
    # Two SendMessageToUser calls report status "completed" but embed an
    # "Error executing tool" message — semantic failure detection (#10) counts them.
    assert report.overview.failed_tool_count == 2
    assert report.tool_failures is not None
    assert report.tool_failures.total_failures == 2
    assert report.tool_failures.embedded_failures == 2
    # At least one failure was recovered via a different tool (SendMessageToSelf).
    assert report.tool_failures.recovered >= 1
