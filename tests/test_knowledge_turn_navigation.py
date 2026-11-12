"""Knowledge conversation navigator state behavior."""

from web.state import State
from web.view_models import KnowledgeQueryVM, KnowledgeTurnVM, ToolCallVM, ToolTurnVM


def _new_state() -> State:
    return State(_reflex_internal_init=True)


def _turn(index: int, question: str, tool_name: str) -> KnowledgeTurnVM:
    return KnowledgeTurnVM(
        id=f"knowledge-turn-{index}",
        turn_index=index,
        turn_label=f"Turn {index}",
        question=question,
        question_excerpt=question,
        call_count=1,
        summary_label="1 call",
        queries=[KnowledgeQueryVM(query=question, tool_name=tool_name)],
    )


def test_turn_navigation_and_filtering():
    state = _new_state()
    state._knowledge_turns = [
        _turn(1, "Find the leave policy", "sharepoint_semantic_search"),
        _turn(4, "Open the selected document", "SharePoint_get_doc"),
        _turn(9, "Show ranked passages", "sharepoint_get_snippets"),
    ]
    state.active_knowledge_turn = "knowledge-turn-1"

    assert state.selected_knowledge_turn.turn_index == 1
    assert state.knowledge_turn_position == "1 of 3"
    assert state.has_previous_knowledge_turn is False
    assert state.has_next_knowledge_turn is True

    state.next_knowledge_turn()
    assert state.active_knowledge_turn == "knowledge-turn-4"
    assert state.knowledge_turn_position == "2 of 3"

    state.previous_knowledge_turn()
    assert state.active_knowledge_turn == "knowledge-turn-1"

    state.set_knowledge_turn_query("snippets")
    assert [turn.id for turn in state.filtered_knowledge_turns] == ["knowledge-turn-9"]
    assert state.active_knowledge_turn == "knowledge-turn-9"
    assert state.knowledge_turn_position == "1 of 1"
    assert state.has_previous_knowledge_turn is False
    assert state.has_next_knowledge_turn is False
    state.previous_knowledge_turn()
    state.next_knowledge_turn()
    assert state.active_knowledge_turn == "knowledge-turn-9"

    state.clear_knowledge_turn_query()
    assert len(state.filtered_knowledge_turns) == 3

    state.set_knowledge_turn_query("no match")
    assert state.filtered_knowledge_turns == []
    assert state.selected_knowledge_turn.id == ""


def test_tool_turn_navigation_and_filtering():
    state = _new_state()
    state._tool_turns = [
        ToolTurnVM(
            id="tool-turn-1",
            turn_index=1,
            turn_label="Turn 1",
            question="Search the policy",
            question_excerpt="Search the policy",
            call_count=1,
            summary_label="1 call · 1 retrieval",
            tool_calls=[ToolCallVM(name="sharepoint_semantic_search", display_name="Semantic Search")],
        ),
        ToolTurnVM(
            id="tool-turn-3",
            turn_index=3,
            turn_label="Turn 3",
            question="Send the result",
            question_excerpt="Send the result",
            call_count=1,
            summary_label="1 call · 1 action",
            tool_calls=[ToolCallVM(name="SendMessageToUser", display_name="Send Message", kind="action")],
        ),
    ]
    state.active_tool_turn = "tool-turn-1"

    assert state.selected_tool_turn.turn_index == 1
    assert state.tool_turn_position == "1 of 2"
    assert state.has_previous_tool_turn is False
    assert state.has_next_tool_turn is True

    state.next_tool_turn()
    assert state.active_tool_turn == "tool-turn-3"

    state.set_tool_turn_query("semantic")
    assert [turn.id for turn in state.filtered_tool_turns] == ["tool-turn-1"]
    assert state.active_tool_turn == "tool-turn-1"
    assert state.tool_turn_position == "1 of 1"
    assert state.has_previous_tool_turn is False
    assert state.has_next_tool_turn is False
    state.previous_tool_turn()
    state.next_tool_turn()
    assert state.active_tool_turn == "tool-turn-1"

    state.clear_tool_turn_query()
    assert len(state.filtered_tool_turns) == 2

    state.set_tool_turn_query("no match")
    assert state.filtered_tool_turns == []
    assert state.selected_tool_turn.id == ""
