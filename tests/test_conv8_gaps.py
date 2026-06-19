"""Tests for the 5 conv8 gap-analysis features (G1-G5), built on the
`Generated deck` sample (HR Knowledge agent that authors a PowerPoint):

- G1 generated file artifacts (fileAttachments)
- G2 code-interpreter purpose split (authoring vs analysis)
- G3 skill-availability gap + code fallback
- G4 document-grounding pipeline (snippet mode + span visibility)
- G5 unverifiable grounding (cited [n] whose source was never retrieved)
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
def deck():
    profile = parse_agent_yaml(FIX / "sample_agent_sandbox.yaml")
    convo = parse_transcript(FIX / "sample_transcript_deck.json")
    return profile, convo, analyze(profile, convo)


@pytest.fixture(scope="module")
def sandbox():
    profile = parse_agent_yaml(FIX / "sample_agent_sandbox.yaml")
    convo = parse_transcript(FIX / "sample_transcript_sandbox.json")
    return profile, convo, analyze(profile, convo)


# --- G1: generated file artifacts ------------------------------------------


def test_g1_artifact_parsed_from_attachments(deck):
    _, convo, _ = deck
    attachments = convo.file_attachments
    assert any(a.file_type == "pptx" and "HR_Onboarding" in a.name for a in attachments)


def test_g1_generated_artifacts_report(deck):
    _, _, report = deck
    ga = report.generated_artifacts
    assert ga is not None and ga.count == 1
    assert ga.by_type.get("pptx") == 1
    item = ga.items[0]
    assert item.file_type == "pptx"
    assert item.how_made == "python"
    assert item.turn_index == 7


def test_g1_finding_present(deck):
    _, _, report = deck
    assert any(f.category == "Artifacts" and "downloadable" in f.title for f in report.findings)


# --- G2: code-interpreter purpose split ------------------------------------


def test_g2_authoring_vs_analysis(deck):
    _, _, report = deck
    ci = report.code_interpreter
    assert ci is not None
    # turns 6/7 author the deck; turns 1/4 read documents
    assert 7 in ci.authoring_turns
    assert ci.analysis_turns
    assert set(ci.authoring_turns).isdisjoint(ci.analysis_turns)


def test_g2_authoring_finding(deck):
    _, _, report = deck
    assert any(f.category == "Sandbox" and "authored a file" in f.title for f in report.findings)


# --- G3: skill gap -> code fallback ----------------------------------------


def test_g3_skill_gap_detected(deck):
    _, _, report = deck
    gaps = report.code_interpreter.skill_gaps
    assert gaps, "expected a skill gap"
    g = gaps[0]
    assert g.fallback == "python"
    assert g.wanted == "creating-pptx"
    assert g.wanted.lower() not in {"relevant", "available", "suitable", "a", "the"}


def test_g3_skill_gap_finding(deck):
    _, _, report = deck
    assert any(f.category == "Sandbox" and "Skill gap" in f.title for f in report.findings)


# --- G4: document grounding pipeline ---------------------------------------


def test_g4_pipeline_stub_mode(deck):
    _, _, report = deck
    gp = report.grounding_pipeline
    assert gp is not None
    assert gp.snippet_mode == "stub"
    assert gp.span_visibility == "document-level"
    assert gp.stub_results == 10
    assert gp.content_results == 0
    assert gp.notes, "expected explanatory notes"


def test_g4_pipeline_cited_doc_chain(deck):
    _, _, report = deck
    gp = report.grounding_pipeline
    cited = [d for d in gp.docs if d.cited]
    assert cited, "expected at least one cited doc"
    # the orientation policy was searched, downloaded, preprocessed and read
    orientation = next((d for d in gp.docs if "Orientation" in d.title), None)
    assert orientation is not None
    assert orientation.preprocessed is True
    assert orientation.read_full is True


def test_g4_finding_document_level(deck):
    _, _, report = deck
    assert any("document-level" in f.title for f in report.findings)


# --- G5: unverifiable grounding --------------------------------------------


def test_g5_unverifiable_fires_on_dangling(sandbox):
    _, _, report = sandbox
    # the sandbox sample cites [1] in turn 1 with no retrieval -> dangling
    assert any(f.category == "Citations" and "Unverifiable" in f.title for f in report.findings)


def test_g5_no_false_positive_on_cross_turn(deck):
    _, _, report = deck
    # probationary [1] resolves cross-turn to a doc retrieved in turn 1's broad
    # search, so it is verifiable -> no unverifiable finding
    assert not any("Unverifiable" in f.title for f in report.findings)


# --- view-models + exports -------------------------------------------------


def test_vm_scalars(deck):
    _, convo, report = deck
    vm = map_report(report, convo)
    assert vm.has_artifacts is True
    assert vm.artifact_count == 1
    assert vm.has_skill_gaps is True
    assert vm.has_grounding_pipeline is True
    assert vm.gp_snippet_mode_label
    assert vm.sandbox_authoring_label != "—"


def test_exports_include_gap_sections(deck):
    _, convo, report = deck
    md = render_markdown(report, convo)
    for header in ("## Generated outputs", "## Grounding pipeline"):
        assert header in md
    sections = build_sections(report, convo)
    assert sections["generated_artifacts"].strip()
    assert sections["grounding_pipeline"].strip()
