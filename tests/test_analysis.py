from pathlib import Path

import pytest

from agent_parser import parse_agent_yaml
from analysis import analyze
from transcript_parser import parse_transcript

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def report():
    profile = parse_agent_yaml(FIX / "sample_agent.yaml")
    convo = parse_transcript(FIX / "sample_transcript.json")
    return analyze(profile, convo)


def test_overview(report):
    o = report.overview
    assert o.turn_count == 3
    assert o.user_message_count == 2
    assert o.bot_message_count == 5
    assert o.tool_call_count == 3
    assert o.knowledge_search_count == 2
    assert o.thought_count == 6
    assert o.failed_tool_count == 0
    assert o.zero_result_search_count == 0


def test_tools(report):
    names = {u.name: u for u in report.tools.usage}
    assert names["KnowledgeSearch"].count == 2
    assert names["KnowledgeSearch"].completed == 2
    assert "skill" in names
    assert any("analyzing-docx" in s for s in report.tools.skill_loads)
    assert len(report.tools.retry_signals) >= 1


def test_knowledge(report):
    k = report.knowledge
    assert len(k.queries) == 2
    # turn1doc1, turn1doc2, turn2doc1, turn2doc2
    assert len(k.distinct_docs) == 4
    # _copy-test.txt and Anti-Harassment were retrieved but never used
    uncited_titles = {d.title for d in k.uncited_docs}
    assert any("copy-test" in (t or "") for t in uncited_titles)
    assert any("Anti-Harassment" in (t or "") for t in uncited_titles)
    assert not k.zero_result_queries


def test_citations(report):
    c = report.citations
    assert c.total_markers >= 2
    assert len(c.reference_ids_in_results) == 4
    # Both answers carry [n] citations, so no uncited substantive answers.
    assert c.uncited_answer_count == 0


def test_reasoning(report):
    r = report.reasoning
    assert r.total_thoughts == 6
    assert len(r.premise_corrections) >= 1  # "Actually, the premise of your question..."


def test_groundedness(report):
    g = report.groundedness
    assert g.hallucination_risk == []
    assert g.ungrounded_answers == 0
    assert len(g.honest_grounding) >= 1  # whistleblower email "does not mention"


def test_instruction_compliance(report):
    checks = report.instructions.checks
    assert len(checks) >= 1
    cot = [c for c in checks if "chain-of-thought" in c.check or "intermediate" in c.instruction.lower()]
    assert cot and cot[0].status == "pass"


def test_cross_reference(report):
    x = report.cross_reference
    assert x.model_in_use == "Claude Sonnet 4.6"
    assert x.defined_knowledge_sources == ["HR-Policies"]
    assert x.contributing_knowledge_sources == ["HR-Policies"]
    assert x.unused_knowledge_sources == []
    assert x.tools_used_not_defined == []


def test_findings_no_critical(report):
    severities = {f.severity for f in report.findings}
    assert "critical" not in severities
    assert len(report.findings) >= 1


def test_graceful_degradation_transcript_only():
    convo = parse_transcript(FIX / "sample_transcript.json")
    report = analyze(None, convo)
    assert report.agent is None
    assert report.overview is not None
    assert any(f.title == "No agent YAML provided" for f in report.findings)


def test_graceful_degradation_yaml_only():
    profile = parse_agent_yaml(FIX / "sample_agent.yaml")
    report = analyze(profile, None)
    assert report.overview is None
    assert report.agent is not None
    assert any(f.title == "No transcript provided" for f in report.findings)
