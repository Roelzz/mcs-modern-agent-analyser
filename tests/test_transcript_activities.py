"""Bot Framework activity envelope (Dataverse `conversationtranscript`) parsing."""

import json
from pathlib import Path

import pytest

from transcript_parser import TRANSCRIPT_ENVELOPE_KEYS, parse_transcript, parse_transcript_text

ACTIVITIES = Path(__file__).parent.parent / "samples" / "sample_transcript_activities.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(ACTIVITIES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def convo():
    return parse_transcript(ACTIVITIES)


def test_only_message_activities_become_messages(convo):
    # trace / event / typing carry no transcript content.
    assert len(convo.messages) == 3
    assert len(convo.user_messages) == 1
    assert len(convo.bot_messages) == 2


def test_roles_are_mapped_from_numeric_from_role(convo):
    assert [m.role for m in convo.messages] == ["bot", "user", "bot"]


def test_messages_are_not_silently_empty(convo):
    """Regression: activities used to fall through as blank bot messages."""
    assert all(m.text.strip() for m in convo.messages)
    assert "carry-over" in convo.user_messages[0].text.lower()


def test_turn_grouping(convo):
    # Leading bot greeting (no user) + 1 user-led turn.
    assert len(convo.turns) == 2
    assert convo.turns[0].user_message is None
    assert convo.turns[1].user_message is not None


def test_trace_tool_call_attaches_to_next_bot_message(convo):
    tcs = convo.tool_calls
    assert len(tcs) == 1
    assert tcs[0].name == "KnowledgeSearch"
    assert tcs[0].is_knowledge_search
    assert len(tcs[0].retrieved_docs) == 2
    # Attached to the reply, not the greeting.
    assert convo.bot_messages[0].tool_calls == []
    assert len(convo.bot_messages[1].tool_calls) == 1


def test_trace_thought_is_captured(convo):
    thoughts = convo.thoughts
    assert len(thoughts) == 1
    assert "leave policy" in thoughts[0].title.lower()


def test_metadata_traces_are_dropped(convo):
    """ConversationInfo / SessionInfo are not tool calls or thoughts."""
    names = [t.name for t in convo.tool_calls]
    assert "ConversationInfo" not in names
    assert "SessionInfo" not in names


def test_timestamps_are_populated_from_timestamp_ms(convo):
    assert all(m.occurred_at for m in convo.messages)
    assert convo.messages[0].occurred_at.startswith("2025-")


def test_named_attachments_kept_cards_dropped(convo):
    atts = convo.bot_messages[1].file_attachments
    assert len(atts) == 1
    assert atts[0].name == "leave-policy-2025.pdf"
    assert atts[0].file_type == "pdf"


def test_bare_activity_array_matches_envelope(raw):
    """A bare list of activities parses the same as the wrapped envelope."""
    bare = parse_transcript_text(json.dumps(raw["activities"]))
    wrapped = parse_transcript_text(json.dumps(raw))
    assert [m.text for m in bare.messages] == [m.text for m in wrapped.messages]
    assert len(bare.tool_calls) == len(wrapped.tool_calls) == 1


def test_dataverse_records_wrapper(raw):
    """Dataverse row exports nest the activity JSON in a `content` column."""
    payload = {"value": [{"conversationtranscriptid": "abc", "content": json.dumps(raw)}]}
    convo = parse_transcript_text(json.dumps(payload))
    assert len(convo.messages) == 3
    assert len(convo.tool_calls) == 1


def test_modern_flat_array_still_wins():
    """Modern messages must never be mistaken for activities."""
    flat = [
        {"role": "user", "id": "u1", "text": "hello"},
        {"role": "bot", "id": "b1", "text": "hi there"},
    ]
    convo = parse_transcript_text(json.dumps(flat))
    assert [m.role for m in convo.messages] == ["user", "bot"]
    assert convo.messages[0].text == "hello"


def test_unrecognised_shape_raises():
    with pytest.raises(ValueError):
        parse_transcript_text(json.dumps({"nothing": "useful"}))


def test_activities_is_a_known_envelope_key():
    assert "activities" in TRANSCRIPT_ENVELOPE_KEYS
