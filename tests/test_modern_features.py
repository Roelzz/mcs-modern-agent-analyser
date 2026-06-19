"""Tests for the 15 modern-agent analysis features (groups A-F) built on the
code-interpreter sample (`Knowledge agent` + sandbox/reasoning transcript):

- D  code interpreter / sandbox (D1 detection, D2 friction, D3 skills)
- E  modern-agent credits (E1 reasoning premium, E2 content processing,
     E3 multi-meter stack, E4 token transparency)
- B  retrieval depth (B1 taxonomy, B2 overlap, B3 over-retrieval, B4 mode)
- A  search strategy (A1 answered-from-recall, A2 search precision)
- C1 cross-turn citation provenance
- F1 instruction adherence (intermediate chain-of-thought)
"""

from pathlib import Path

import pytest

from agent_parser import parse_agent_yaml
from analysis import analyze
from renderer import build_sections, render_markdown
from transcript_parser import parse_transcript
from web.view_models import map_report

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sandbox():
    profile = parse_agent_yaml(FIX / "sample_agent_sandbox.yaml")
    convo = parse_transcript(FIX / "sample_transcript_sandbox.json")
    return profile, convo, analyze(profile, convo)


# --- D: code interpreter / sandbox -----------------------------------------


def test_d1_code_interpreter_detected(sandbox):
    _, _, report = sandbox
    ci = report.code_interpreter
    assert ci is not None and ci.used is True
    assert ci.turns_with_code == 3
    assert {"sandbox", "sudo", "view"}.issubset(set(ci.distinct_tools))
    assert ci.signals  # per-turn evidence present


def test_d2_sandbox_friction(sandbox):
    _, _, report = sandbox
    ci = report.code_interpreter
    assert ci.friction_count == 1
    fr = ci.friction[0]
    assert fr.turn_index == 4
    assert fr.recovered is True


def test_d3_skill_characterisation(sandbox):
    _, _, report = sandbox
    skills = report.code_interpreter.skills
    assert any(s.name == "analyzing-docx" and s.category == "document-processing" for s in skills)


# --- E: modern-agent credit model ------------------------------------------


def test_e1_reasoning_premium_surcharge(sandbox):
    _, _, report = sandbox
    est = report.credit_estimate
    assert est.reasoning_model is True
    assert est.by_kind.get("premium_reasoning", 0) > 0
    # premium is additive on top of the feature meters
    feature_only = est.by_kind.get("generative_answer", 0) + est.by_kind.get("agent_action", 0)
    assert est.total_credits > feature_only


def test_e2_content_processing_meter(sandbox):
    _, _, report = sandbox
    est = report.credit_estimate
    # analyzing-docx skill => content processing @ 8 CC/page (1-page floor)
    assert est.by_kind.get("content_processing", 0) == 8


def test_e3_feature_meters_unchanged_and_total_consistent(sandbox):
    _, _, report = sandbox
    est = report.credit_estimate
    # 4 generative answers @ 2, one agent action (skill) @ 5 — unchanged by the rewrite
    assert est.by_kind.get("generative_answer") == 8
    assert est.by_kind.get("agent_action") == 5
    assert est.total_credits == round(sum(li.credits for li in est.line_items), 2)


def test_e4_token_transparency(sandbox):
    _, _, report = sandbox
    est = report.credit_estimate
    assert est.total_tokens > 0
    assert est.assumptions  # surfaced as estimates, not hard numbers


# --- B: retrieval depth ----------------------------------------------------


def test_b1_sharepoint_taxonomy(sandbox):
    _, _, report = sandbox
    rd = report.retrieval_depth
    assert rd is not None and len(rd.folders) >= 6
    assert all(f.count >= 1 for f in rd.folders)


def test_b2_cross_search_overlap(sandbox):
    _, _, report = sandbox
    rd = report.retrieval_depth
    assert rd.total_retrieved == 20
    assert rd.unique_docs == 14
    assert rd.overlap_docs == 6


def test_b3_over_retrieval_ratio(sandbox):
    _, _, report = sandbox
    rd = report.retrieval_depth
    assert rd.cited_docs == 1
    assert 0.9 < rd.over_retrieval_ratio < 1.0


def test_b4_retrieval_mode_and_full_reads(sandbox):
    _, _, report = sandbox
    rd = report.retrieval_depth
    assert rd.retrieval_mode == "snippet+sandbox"
    assert rd.full_doc_reads == 3


# --- A: search strategy ----------------------------------------------------


def test_a1_answered_from_recall(sandbox):
    _, _, report = sandbox
    ss = report.search_strategy
    assert ss is not None
    assert {t.turn_index for t in ss.recall_turns} == {2, 4}


def test_a2_search_precision(sandbox):
    _, _, report = sandbox
    ss = report.search_strategy
    assert ss.productive_searches == 2
    assert ss.unproductive_searches == 0
    for s in ss.searches:
        assert s.retrieved == 10
        assert s.cited_from_search == 1
        assert s.productive is True


# --- C1: cross-turn citation provenance ------------------------------------


def test_c1_cross_turn_citations_resolved(sandbox):
    _, _, report = sandbox
    rows = report.citation_audit.rows
    cross = [r for r in rows if r.cross_turn]
    assert len(cross) == 2
    assert all(r.status == "resolved" for r in cross)


# --- F1: instruction adherence ---------------------------------------------


def test_f1_intermediate_message_adherence(sandbox):
    _, _, report = sandbox
    checks = report.instructions.checks
    cot = next((c for c in checks if "intermediate" in c.instruction.lower()), None)
    assert cot is not None
    assert cot.status == "pass"
    assert "intermediate" in cot.evidence.lower()


# --- exports / view-model wiring -------------------------------------------


def test_exports_include_modern_sections(sandbox):
    _, convo, report = sandbox
    md = render_markdown(report, convo)
    for header in (
        "## Sandbox & code interpreter",
        "## Retrieval depth",
        "## Search strategy",
        "## Credit estimate",
    ):
        assert header in md
    sections = build_sections(report, convo)
    assert sections["sandbox"].strip()
    assert sections["retrieval_depth"].strip()
    assert sections["search_strategy"].strip()


def test_map_report_modern_scalars(sandbox):
    _, convo, report = sandbox
    vm = map_report(report, convo)
    assert vm.sandbox_used is True
    assert vm.sandbox_tools == ["sandbox", "sudo", "view"]
    assert vm.has_retrieval_depth is True
    assert vm.rd_over_retrieval_pct == 93
    assert vm.has_search_strategy is True
    assert vm.credit_reasoning_model is True
    assert vm.credit_total_tokens > 0
