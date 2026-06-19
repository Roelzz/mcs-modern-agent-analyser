"""Unit tests for the 8 heuristic features added in this build:
#2 per-answer groundedness, #5 repetition, #6 tool efficiency, #8 timeline,
#10 failed-tool & recovery, #11 quote-traceability, #12 coverage gaps, #16 turn-economy.

The knowledge sample exercises #2/#11/#12/#16; the agentic sample exercises #10/#6/#16.
"""

from pathlib import Path

import pytest

from analysis import analyze
from renderer import build_sections, render_markdown
from transcript_parser import parse_transcript
from web.view_models import map_report

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def knowledge():
    return parse_transcript(FIX / "sample_transcript.json")


@pytest.fixture(scope="module")
def agentic():
    return parse_transcript(FIX / "sample_transcript_agentic.json")


@pytest.fixture(scope="module")
def k_report(knowledge):
    return analyze(None, knowledge)


@pytest.fixture(scope="module")
def a_report(agentic):
    return analyze(None, agentic)


# --- #10 failed-tool & recovery ---------------------------------------------


def test_tool_failures_detected_on_agentic(a_report):
    tf = a_report.tool_failures
    assert tf is not None
    assert tf.total_failures == 2
    assert tf.embedded_failures == 2  # both hidden behind a "completed" status
    assert tf.recovered >= 1
    # every failure carries cleaned error text
    assert all(f.error_text for f in tf.failures)


def test_no_tool_failures_on_knowledge(k_report):
    tf = k_report.tool_failures
    assert tf is not None
    assert tf.total_failures == 0


def test_overview_failed_count_is_semantic(a_report):
    # status-only counting would give 0; semantic detection gives 2
    assert a_report.overview.failed_tool_count == 2


# --- #6 tool efficiency -----------------------------------------------------


def test_tool_efficiency_counts(a_report):
    eff = a_report.tool_efficiency
    assert eff is not None
    assert eff.total_calls == 5
    # the two SendMessageToUser calls differ by recipient, so NOT redundant
    assert eff.redundant_calls == 0
    assert eff.unique_calls == eff.total_calls


# --- #5 repetition ----------------------------------------------------------


def test_no_false_repetition(k_report, a_report):
    assert k_report.repetition is not None
    assert a_report.repetition is not None
    assert k_report.repetition.signals == []
    assert a_report.repetition.signals == []


# --- #2 per-answer groundedness ---------------------------------------------


def test_answer_groundedness_buckets(k_report):
    ag = k_report.answer_groundedness
    assert ag is not None
    assert ag.high_risk + ag.medium_risk + ag.low_risk == len(ag.answers)
    assert len(ag.answers) >= 1


def test_answer_groundedness_risk_values(a_report):
    ag = a_report.answer_groundedness
    assert ag is not None
    assert all(a.risk in {"low", "medium", "high"} for a in ag.answers)


# --- #11 quote-traceability -------------------------------------------------


def test_quote_traceability_on_knowledge(k_report):
    qf = k_report.quote_faithfulness
    assert qf is not None
    # one quoted span (blockquote + inline wrap the same text -> deduped to 1)
    assert len(qf.quotes) == 1
    q = qf.quotes[0]
    # cited to a retrieved doc whose full text lives in the sandbox
    assert q.verdict == "attributed-source-in-sandbox"
    assert qf.attributed == 1


def test_quote_traceability_empty_on_agentic(a_report):
    qf = a_report.quote_faithfulness
    assert qf is not None
    assert qf.quotes == []


# --- #12 coverage gaps ------------------------------------------------------


def test_coverage_gap_on_knowledge(k_report):
    cg = k_report.coverage_gaps
    assert cg is not None
    assert len(cg.gaps) == 1
    assert cg.gaps[0].reason in {"zero-result-search", "acknowledged-gap", "uncited-answer"}


def test_no_coverage_gap_false_positive_on_agentic(a_report):
    # action turns (send message) must not be flagged as knowledge gaps
    cg = a_report.coverage_gaps
    assert cg is not None
    assert cg.gaps == []


# --- #16 turn-economy -------------------------------------------------------


def test_turn_economy(k_report, a_report):
    ke = k_report.turn_economy
    assert ke is not None
    assert ke.user_turns == 2
    assert ke.searches_to_first_answer >= 1
    ae = a_report.turn_economy
    assert ae is not None
    assert ae.user_turns == 6
    assert ae.tool_calls == 5


# --- #8 timeline (view-model + export) --------------------------------------


def test_timeline_view_model(knowledge):
    vm = map_report(analyze(None, knowledge), knowledge)
    assert len(vm.timeline) == len(knowledge.turns)
    # every non-greeting turn has at least one event
    assert all(len(t.events) > 0 for t in vm.timeline)


def test_timeline_marks_failed_tools(agentic):
    vm = map_report(analyze(None, agentic), agentic)
    failed_events = [e for t in vm.timeline for e in t.events if e.failed]
    assert len(failed_events) == 2


# --- exports wire-up --------------------------------------------------------


def test_exports_include_new_sections_knowledge(k_report, knowledge):
    secs = build_sections(k_report, knowledge)
    for key in ("turn_economy", "timeline", "answer_grounding", "quote_traceability", "coverage_gaps"):
        assert secs[key].strip(), f"expected non-empty {key} section"
    # honest: no fabricated failure/repetition sections
    assert secs["tool_failures"] == ""
    assert secs["repetition"] == ""


def test_exports_include_new_sections_agentic(a_report, agentic):
    secs = build_sections(a_report, agentic)
    assert secs["tool_failures"].strip()
    assert secs["timeline"].strip()
    assert secs["turn_economy"].strip()


def test_render_markdown_contains_new_headings(a_report, agentic):
    md = render_markdown(a_report, agentic)
    assert "## Failed tools & recovery" in md
    assert "## Conversation timeline" in md
    assert "## Turn economy" in md
