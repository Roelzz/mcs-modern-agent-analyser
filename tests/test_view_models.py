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


# --------------------------------------------------------------------------- #
# Connector-failure VMs: full transparency + per-call diagnosis in the UI VMs #
# --------------------------------------------------------------------------- #

CONNECTOR_FAIL = Path(__file__).parent.parent / "samples" / "sample_transcript_connector_fail.json"

_CATEGORIES = {
    "authentication", "configuration", "conflict", "not-found", "parameter-schema",
    "permission", "rate-limit", "server-error", "timeout", "unknown", "validation",
}


@pytest.fixture(scope="module")
def fail_vm():
    convo = parse_transcript(CONNECTOR_FAIL)
    return map_report(analyze(None, convo), convo)


def _all_calls(vm):
    return [tc for b in vm.chat for tc in b.tool_calls]


def test_fail_every_call_has_activity_and_id(fail_vm):
    calls = _all_calls(fail_vm)
    assert len(calls) == 8
    # Deep-dive narration + exact call id present for EVERY call, success or fail.
    assert all(tc.activity_summary for tc in calls)
    assert all(tc.call_id for tc in calls)


def test_fail_success_call_narrates_outcome(fail_vm):
    getitem = next(tc for tc in _all_calls(fail_vm) if tc.name == "Getitem")
    assert not getitem.failed
    assert getitem.activity_summary == "Read item with id=11 → returned 1 record (Title=AP Agent)"


def test_fail_calls_carry_untruncated_error_and_diagnosis(fail_vm):
    failed = [tc for tc in _all_calls(fail_vm) if tc.failed]
    assert len(failed) == 3
    for tc in failed:
        assert tc.has_error
        assert len(tc.error or "") > 160  # old cap was 160 — proves untruncated
        assert tc.diagnosis_category in _CATEGORIES
        assert tc.diagnosis_cause and tc.diagnosis_fix


def test_fail_categories_across_calls(fail_vm):
    cats = {tc.diagnosis_category for tc in _all_calls(fail_vm) if tc.failed}
    assert cats == {"configuration", "parameter-schema"}


def test_fail_failure_rows_full_and_diagnosed(fail_vm):
    rows = fail_vm.tool_failure_rows
    assert len(rows) == 3
    assert max(len(r.error_text or "") for r in rows) > 200  # untruncated
    for r in rows:
        assert r.diagnosis_category in _CATEGORIES
        assert r.diagnosis_cause and r.diagnosis_fix
    # Named rules sharpen wording when a signature matches.
    rules = {r.diagnosis_rule for r in rows}
    assert "sharepoint-site-address-placeholder" in rules
    assert "passed-in-field-not-found" in rules


# --------------------------------------------------------------------------- #
# Flat "All tool calls" list — every call, all detail, nothing truncated      #
# --------------------------------------------------------------------------- #


def test_tool_calls_all_covers_every_call(fail_vm):
    # The flat list the Tools & actions tab renders must hold EVERY call across
    # every turn — the same objects the chat cards flatten to.
    flat = fail_vm.tool_calls_all
    chat_calls = _all_calls(fail_vm)
    assert len(flat) == len(chat_calls) == 8
    assert [tc.call_id for tc in flat] == [tc.call_id for tc in chat_calls]
    # 3 failures + 5 successes are all present, none dropped.
    assert sum(tc.failed for tc in flat) == 3
    assert all(tc.call_id and tc.activity_summary for tc in flat)


def test_tool_calls_all_untruncated_params_and_bodies(fail_vm):
    flat = fail_vm.tool_calls_all
    # A successful retrieval keeps its full response body (thousands of chars,
    # no clip) and its query param.
    big = max(flat, key=lambda tc: len(tc.raw_result or ""))
    assert len(big.raw_result) > 1000
    # Every failed call carries all its input params + the full error body.
    for tc in flat:
        if tc.failed:
            assert tc.params  # inputs preserved as KV pairs
            assert tc.has_error and len(tc.error or "") > 160  # old cap was 160
            # No ellipsis clipping on the error body.
            assert not (tc.error or "").endswith("…")


def test_failure_rows_embed_full_detail_vm(fail_vm):
    # Parity: each failure row carries the SAME full ToolCallVM the flat list /
    # chat cards use, so the universal renderer shows all params + response/error.
    by_id = {tc.call_id: tc for tc in fail_vm.tool_calls_all}
    for r in fail_vm.tool_failure_rows:
        assert r.has_detail
        d = r.detail
        assert d.call_id == r.call_id
        src = by_id[r.call_id]
        # Same untruncated inputs and error body as the flat surface.
        assert len(d.params) == len(src.params) and d.params  # inputs present
        assert d.error == src.error and len(d.error) > 160
        assert d.raw_result == src.raw_result

# --------------------------------------------------------------------------- #
# Generic per-call "Context" — every call (even payload-less skill loads) gets #
# derived, honest, causal debugging value; never a bare echo.                 #
# --------------------------------------------------------------------------- #


def _skill_loads(vm):
    return [tc for tc in vm.tool_calls_all if tc.kind == "skill"]


def test_every_call_has_nonempty_context(fail_vm):
    # The whole point: no call — success, fail, or payload-less — is ever empty.
    for tc in fail_vm.tool_calls_all:
        assert tc.context_lines
        assert all(isinstance(ln, str) and ln.strip() for ln in tc.context_lines)


def test_context_carries_sequence_provenance(fail_vm):
    # Every call names its position + turn so you can locate it in the run.
    for tc in fail_vm.tool_calls_all:
        assert any(
            ("Call " in ln and " in this turn (turn " in ln)
            or ln.startswith("Only tool call in this turn")
            for ln in tc.context_lines
        )


def test_skill_load_context_is_rich_not_a_bare_echo(fail_vm):
    skills = _skill_loads(fail_vm)
    assert skills  # project-membership + analyzing-csv
    pm = next(tc for tc in skills if "project-membership" in (tc.display_name or ""))
    joined = " ".join(pm.context_lines)
    # Nature of a skill load (kills the old bare "Loaded Skill: x -> completed").
    assert "capability activation" in joined.lower()
    assert "no request body or response payload" in joined.lower()
    # A category line is always present.
    assert any(ln.startswith("Category:") for ln in pm.context_lines)


def test_document_processing_skill_flags_billing(fail_vm):
    skills = _skill_loads(fail_vm)
    csv = next(tc for tc in skills if "analyzing-csv" in (tc.display_name or ""))
    joined = " ".join(csv.context_lines)
    assert "document-processing" in joined.lower()
    assert "billing" in joined.lower()


def test_context_has_causal_in_turn_linkage(fail_vm):
    # The real payoff: a skill load is tied to what the agent did right after it
    # in the same turn (here: searches + the failed connector calls).
    pm = next(
        tc for tc in _skill_loads(fail_vm) if "project-membership" in (tc.display_name or "")
    )
    after = next((ln for ln in pm.context_lines if ln.startswith("Immediately after:")), None)
    assert after is not None
    assert "searched knowledge" in after.lower()


def test_last_call_has_no_following_linkage(fail_vm):
    # The final call in a turn has nothing after it — no fabricated linkage.
    last = fail_vm.tool_calls_all[-1]
    assert not any(ln.startswith("Immediately after:") for ln in last.context_lines)


def test_context_shared_across_surfaces(fail_vm):
    # The flat Tools-tab list and the chat cards reuse the SAME VM object, so the
    # Context appears everywhere without extra wiring.
    chat_by_id = {tc.call_id: tc for tc in _all_calls(fail_vm)}
    for tc in fail_vm.tool_calls_all:
        assert chat_by_id[tc.call_id] is tc
