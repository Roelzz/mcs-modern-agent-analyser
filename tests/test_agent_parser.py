from pathlib import Path

import pytest

from agent_parser import friendly_model, parse_agent_yaml

FIXTURE = Path(__file__).parent / "fixtures" / "sample_agent.yaml"


@pytest.fixture(scope="module")
def profile():
    return parse_agent_yaml(FIXTURE)


def test_basic_identity(profile):
    assert profile.display_name == "Knowledge agent"
    assert profile.template == "cliagent-1.0.0"
    assert profile.recognizer_kind == "CLICopilotRecognizer"
    assert profile.is_modern is True


def test_model_extraction(profile):
    assert profile.model_series == "Sonnet46"
    assert profile.model_label == "Claude Sonnet 4.6"


def test_instructions_and_memory(profile):
    assert profile.enable_memory is True
    assert "intermediate chain of thought" in profile.instructions.lower()
    assert len(profile.instruction_segments) >= 1


def test_knowledge_sources(profile):
    assert len(profile.knowledge_sources) == 1
    ks = profile.knowledge_sources[0]
    assert ks.display_name == "HR-Policies"
    assert ks.source_kind == "SharePointKnowledgeSource"
    assert ks.source_site is not None and "HR-Policies" in ks.source_site
    # siteUrl should be URL-decoded (no %20)
    assert "%20" not in ks.source_site


def test_environment_variables(profile):
    assert len(profile.environment_variables) >= 1
    names = {ev.display_name for ev in profile.environment_variables}
    assert "Should the Peek Button Be Showed" in names


def test_auth(profile):
    assert profile.authentication_mode == "Integrated"


def test_friendly_model_fallback():
    assert friendly_model("Sonnet46") == "Claude Sonnet 4.6"
    assert friendly_model("MysteryModel7") == "MysteryModel7"
    assert friendly_model(None) is None
