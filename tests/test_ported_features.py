"""Tests for the four ported classic features (#3 knowledge effectiveness,
#4 citation verification, #8 component explorer, #9 credit estimation)."""

from pathlib import Path

import pytest

import explainer
from agent_parser import parse_agent_yaml
from analysis import analyze, estimate_credits
from config import CREDIT_SOURCE_URL
from renderer import render_components, render_markdown
from transcript_parser import parse_transcript
from web.view_models import map_report

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def knowledge():
    profile = parse_agent_yaml(FIX / "sample_agent.yaml")
    convo = parse_transcript(FIX / "sample_transcript.json")
    return profile, convo, analyze(profile, convo)


@pytest.fixture(scope="module")
def agentic():
    convo = parse_transcript(FIX / "sample_transcript_agentic.json")
    return None, convo, analyze(None, convo)


# --- #3 Knowledge source effectiveness -------------------------------------


def test_effectiveness_maps_docs_to_configured_source(knowledge):
    _, _, report = knowledge
    eff = report.knowledge_effectiveness
    assert eff is not None
    assert eff.total_searches == 2
    assert eff.distinct_docs == 4
    assert eff.unattributed_docs == 0
    src = {s.display_name: s for s in eff.sources}
    hr = src["HR-Policies"]
    assert hr.configured is True
    assert hr.docs_retrieved == 4
    assert hr.docs_cited == 2
    assert hr.contribution_rate == 0.5
    assert hr.never_retrieved is False
    assert hr.zero_contribution is False


def test_effectiveness_transcript_only_synthesises_observed_sources(agentic):
    _, _, report = agentic
    eff = report.knowledge_effectiveness
    assert eff is not None
    assert eff.sources, "observed site-roots should become runtime sources"
    assert all(s.configured is False for s in eff.sources)
    assert eff.unattributed_docs > 0


# --- #4 Citation verification ----------------------------------------------


def test_citation_audit_resolves_and_flags(knowledge):
    _, _, report = knowledge
    audit = report.citation_audit
    assert audit is not None
    assert audit.resolved >= 3
    assert audit.dangling == 0  # repeated [1] markers reuse the turn's resolved doc
    assert audit.uncited_retrievals == 2
    resolved = [r for r in audit.rows if r.status == "resolved"]
    assert all(r.doc_title for r in resolved)
    assert any(r.provenance for r in resolved)
    assert any(r.status == "uncited_retrieval" for r in audit.rows)


def test_citation_audit_present_without_yaml(agentic):
    _, _, report = agentic
    assert report.citation_audit is not None
    assert report.citation_audit.uncited_retrievals >= 0


# --- #9 Credit / cost estimation -------------------------------------------


def test_credit_totals_knowledge(knowledge):
    _, _, report = knowledge
    est = report.credit_estimate
    assert est is not None
    # 2 searches (2 each) + 1 skill action (5) = 9
    assert est.total_credits == 9
    assert est.by_kind["generative_answer"] == 4
    assert est.by_kind["agent_action"] == 5
    assert any("Heuristic estimate" in n for n in est.notes)
    assert any(CREDIT_SOURCE_URL in n for n in est.notes)


def test_credit_rates_are_env_overridable(knowledge, monkeypatch):
    profile, convo, _ = knowledge
    monkeypatch.setenv("CREDIT_AGENT_ACTION", "50")
    est = estimate_credits(profile, convo)
    assert est.by_kind["agent_action"] == 50
    assert est.total_credits == 54  # 4 generative + 50 action


# --- #8 Component explorer (explainer KB + builder) ------------------------


def test_explainer_documented_entries_have_doc():
    ex = explainer.explain("model")
    assert ex.documented is True
    assert ex.doc and ex.doc.startswith("https://learn.microsoft.com")


def test_explainer_enum_value_appended():
    ex = explainer.explain("authenticationMode", "Integrated")
    assert ex.documented is True
    assert "Integrated" in ex.summary


def test_explainer_sentinel_for_cli_internal():
    for key in ("recognizer", "template", "runtimeProvider", "enableMemory"):
        ex = explainer.explain(key)
        assert ex.documented is False
        assert ex.doc is None
        assert "explainer KB" in ex.summary


def test_component_builder_groups_and_sentinels(knowledge):
    _, convo, report = knowledge
    vm = map_report(report, convo)
    cats = {c.category for c in vm.components}
    assert {"Agent settings", "Knowledge", "Environment variables"} <= cats
    by_label = {c.label: c for c in vm.components}
    assert by_label["Model"].documented is True
    assert by_label["Recognizer"].documented is False
    assert by_label["Memory"].documented is False


def test_component_explorer_runtime_only(agentic):
    _, convo, report = agentic
    vm = map_report(report, convo)
    labels = {c.label for c in vm.components}
    assert "SendMessageToUser" in labels
    assert all(c.category == "Tools & actions" for c in vm.components)


# --- map_report scalars + exports ------------------------------------------


def test_map_report_feature_scalars(knowledge):
    _, convo, report = knowledge
    vm = map_report(report, convo)
    assert vm.eff_total_searches == 2
    assert vm.cit_dangling == 0
    assert vm.cit_resolved >= 3
    assert vm.credit_total == "9"
    assert vm.has_credits is True
    assert len(vm.components) > 0


def test_exports_include_feature_sections(knowledge):
    _, convo, report = knowledge
    md = render_markdown(report, convo)
    for heading in (
        "## Knowledge source effectiveness",
        "## Citation verification",
        "## Credit estimate",
        "## Components",
    ):
        assert heading in md
    assert "Heuristic estimate" in md
    assert "[Learn](https://learn.microsoft.com" in md


def test_render_components_sentinel_dash(knowledge):
    profile, convo, report = knowledge
    table = render_components(report, convo)
    # CLI-internal settings have no doc → "—" reference cell
    assert "| Recognizer |" in table
    assert "—" in table
