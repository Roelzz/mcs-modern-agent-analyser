import json
from pathlib import Path

import pytest

from agent_parser import parse_agent_yaml
from analysis import analyze
from transcript_parser import parse_transcript, parse_transcript_text

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


def test_followup_retrievals_do_not_inflate_search_metrics_or_credits():
    convo = parse_transcript_text(
        json.dumps(
            [
                {"role": "user", "text": "Find and open the policy"},
                {
                    "role": "bot",
                    "text": "Here is the policy [1].",
                    "toolCalls": [
                        {
                            "id": "search",
                            "name": "sharepoint_semantic_search",
                            "category": "KnowledgeSearch",
                            "status": "completed",
                            "params": {"query": "policy"},
                            "result": json.dumps(
                                {
                                    "mode": "semantic",
                                    "count": 1,
                                    "results": [{"referenceId": "turn1doc1", "title": "Policy.docx"}],
                                }
                            ),
                        },
                        {
                            "id": "doc",
                            "name": "SharePoint_get_doc",
                            "category": "KnowledgeRetrieve",
                            "status": "completed",
                            "params": {"referenceId": "turn1doc1"},
                            "result": json.dumps(
                                {
                                    "referenceId": "turn1doc1",
                                    "title": "Policy.docx",
                                    "content": "Policy content.",
                                }
                            ),
                        },
                        {
                            "id": "snippets",
                            "name": "sharepoint_get_snippets",
                            "category": "KnowledgeRetrieve",
                            "status": "completed",
                            "params": {"referenceId": "turn1doc1"},
                            "result": json.dumps(
                                {
                                    "mode": "snippets",
                                    "referenceId": "turn1doc1",
                                    "returned": 1,
                                    "snippets": [{"rank": 1, "text": "Policy content."}],
                                }
                            ),
                        },
                    ],
                },
            ]
        )
    )

    report = analyze(None, convo)
    assert report.overview is not None
    assert report.overview.knowledge_search_count == 1
    assert report.knowledge is not None
    assert len(report.knowledge.queries) == 3
    assert report.credit_estimate is not None
    generative = [item for item in report.credit_estimate.line_items if item.kind == "generative_answer"]
    assert len(generative) == 1
    assert report.retrieval_depth is not None
    assert report.retrieval_depth.total_retrieved == 1
    assert report.retrieval_depth.overlap_docs == 0


def test_positive_count_without_document_metadata_is_not_zero_result():
    convo = parse_transcript_text(
        json.dumps(
            [
                {"role": "user", "text": "Find the policy"},
                {
                    "role": "bot",
                    "text": "The policy requires approval before execution.",
                    "toolCalls": [
                        {
                            "id": "search",
                            "name": "sharepoint_semantic_search",
                            "category": "KnowledgeSearch",
                            "status": "completed",
                            "params": {"query": "policy approval"},
                            "result": json.dumps({"count": 2}),
                        }
                    ],
                },
            ]
        )
    )

    report = analyze(None, convo)
    assert report.overview is not None
    assert report.overview.zero_result_search_count == 0
    assert report.groundedness is not None
    assert report.groundedness.hallucination_risk == []


def test_graceful_degradation_yaml_only():
    profile = parse_agent_yaml(FIX / "sample_agent.yaml")
    report = analyze(profile, None)
    assert report.overview is None
    assert report.agent is not None
    assert any(f.title == "No transcript provided" for f in report.findings)


# --------------------------------------------------------------------------- #
# Connector-failure transcript: analysis must surface full errors + diagnosis #
# --------------------------------------------------------------------------- #

CONNECTOR_FAIL = Path(__file__).parent.parent / "samples" / "sample_transcript_connector_fail.json"


@pytest.fixture(scope="module")
def fail_report():
    return analyze(None, parse_transcript(CONNECTOR_FAIL))


def test_fail_overview(fail_report):
    o = fail_report.overview
    assert o.turn_count == 4
    assert o.tool_call_count == 8
    assert o.failed_tool_count == 3


def test_fail_tool_failures_present(fail_report):
    tfa = fail_report.tool_failures
    assert tfa is not None
    assert tfa.total_failures == 3
    assert tfa.embedded_failures == 0
    assert len(tfa.failures) == 3


def test_fail_error_text_untruncated(fail_report):
    # Old code capped error_text at 160 chars; the passed-in-field row is >200.
    lengths = [len(f.error_text or "") for f in fail_report.tool_failures.failures]
    assert max(lengths) > 200
    assert all(n > 0 for n in lengths)


def test_fail_each_row_carries_diagnosis_and_call_id(fail_report):
    for f in fail_report.tool_failures.failures:
        assert f.call_id  # exact tool-call id surfaced
        assert f.diagnosis is not None
        assert f.diagnosis.category
        assert f.diagnosis.cause and f.diagnosis.fix


def test_fail_categories_are_sharp(fail_report):
    cats = {f.diagnosis.category for f in fail_report.tool_failures.failures}
    assert "configuration" in cats
    assert "parameter-schema" in cats


def test_fail_findings_include_failure(fail_report):
    # A failure-related finding is emitted for the connector errors.
    titles = " ".join(f.title.lower() for f in fail_report.findings)
    assert "fail" in titles or "error" in titles
