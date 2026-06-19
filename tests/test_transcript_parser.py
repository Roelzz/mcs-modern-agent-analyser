from pathlib import Path

import pytest

from transcript_parser import parse_knowledge_result, parse_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


@pytest.fixture(scope="module")
def convo():
    return parse_transcript(FIXTURE)


def test_message_counts(convo):
    assert len(convo.messages) == 7
    assert len(convo.user_messages) == 2
    assert len(convo.bot_messages) == 5


def test_turn_grouping(convo):
    # Leading bot greeting (no user) + 2 user-led turns = 3 turns.
    assert len(convo.turns) == 3
    assert convo.turns[0].user_message is None  # greeting
    assert convo.turns[1].user_message is not None
    assert "consent" in convo.turns[1].user_message.text.lower()
    # The whistleblower turn fans out into multiple bot messages.
    assert len(convo.turns[2].bot_messages) == 3


def test_tool_calls(convo):
    tcs = convo.tool_calls
    ks = [t for t in tcs if t.is_knowledge_search]
    assert len(ks) == 2
    assert ks[0].query == "background check consent timing"
    assert ks[0].result_count == 2
    assert len(ks[0].retrieved_docs) == 2
    assert ks[0].retrieved_docs[0].reference_id == "turn1doc1"
    assert "Background-Check-Policy" in (ks[0].retrieved_docs[0].title or "")


def test_skill_tool_call(convo):
    skills = [t for t in convo.tool_calls if (t.name or "") == "skill"]
    assert len(skills) == 1
    assert "analyzing-docx" in (skills[0].display_name or "")


def test_thoughts(convo):
    assert len(convo.thoughts) >= 4


def test_parse_knowledge_result_blocks():
    text = (
        "[2 results]\r\n\r\n"
        "Title: A.docx\r\nURL: https://example.com/A.docx\r\nReferenceId: turn1doc1\r\n\r\n"
        "Title: B.txt\r\nURL: https://example.com/B.txt\r\nReferenceId: turn1doc2\r\n"
    )
    docs, count, zero = parse_knowledge_result(text)
    assert count == 2
    assert zero is False
    assert [d.reference_id for d in docs] == ["turn1doc1", "turn1doc2"]


def test_parse_knowledge_result_zero():
    docs, count, zero = parse_knowledge_result("[0 results]")
    assert count == 0
    assert zero is True
    assert docs == []


def test_parse_knowledge_result_empty():
    docs, count, zero = parse_knowledge_result(None)
    assert docs == [] and count is None and zero is False
