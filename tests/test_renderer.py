from pathlib import Path

import pytest

from agent_parser import parse_agent_yaml
from analysis import analyze
from renderer import render_markdown, render_sequence_diagram
from transcript_parser import parse_transcript

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def convo():
    return parse_transcript(FIX / "sample_transcript.json")


@pytest.fixture(scope="module")
def report(convo):
    return analyze(parse_agent_yaml(FIX / "sample_agent.yaml"), convo)


def test_markdown_has_core_sections(report, convo):
    md = render_markdown(report, convo)
    for heading in (
        "# Agent analysis — Knowledge agent",
        "## Findings",
        "## Agent profile",
        "## Conversation overview",
        "## Conversation flow",
        "## Tools",
        "## Knowledge",
        "## Reasoning",
        "## Quality & groundedness",
        "## Instruction compliance",
        "## Cross-reference",
    ):
        assert heading in md, f"missing: {heading}"


def test_markdown_has_mermaid(report, convo):
    md = render_markdown(report, convo)
    assert "```mermaid" in md
    assert "sequenceDiagram" in md


def test_sequence_diagram_participants(convo):
    diagram = render_sequence_diagram(convo)
    assert "participant U as User" in diagram
    assert "participant K as Knowledge" in diagram
    assert "A->>K: search" in diagram


def test_render_yaml_only():
    report = analyze(parse_agent_yaml(FIX / "sample_agent.yaml"), None)
    md = render_markdown(report, None)
    assert "## Agent profile" in md
    assert "## Conversation overview" not in md  # no transcript


def test_render_transcript_only(convo):
    report = analyze(None, convo)
    md = render_markdown(report, convo)
    assert "## Conversation overview" in md
    assert "## Agent profile" not in md  # no YAML
