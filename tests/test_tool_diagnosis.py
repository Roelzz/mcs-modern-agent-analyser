"""Tests for the generic, tiered tool-diagnosis engine.

These prove the engine is *generic-first*: it narrates every call (success and
failure) and always produces a non-empty diagnosis for any failed call from any
tool — named YAML rules only sharpen wording, they are never a precondition.
"""

from pathlib import Path

import pytest

from models import ToolCall
from tool_diagnosis import CATEGORIES, describe, diagnose, is_failure
from transcript_parser import parse_transcript

SAMPLE = Path(__file__).parent.parent / "samples" / "sample_transcript_connector_fail.json"


def mk(name, *, error=None, result=None, status="failed", params=None):
    return ToolCall(name=name, status=status, error=error, result=result, params=params or {})


@pytest.fixture(scope="module")
def fixture_calls():
    return parse_transcript(SAMPLE).tool_calls


# --------------------------------------------------------------------------- #
# describe() — deep-dive narration for EVERY call, success included            #
# --------------------------------------------------------------------------- #


def test_describe_success_read(fixture_calls):
    # call[4] = Getitem id=11, a successful read.
    text = describe(fixture_calls[4])
    assert "Read" in text
    assert "id=11" in text
    assert "record" in text.lower()
    assert "diagnosis" not in text.lower()  # no failure hint on a success


def test_describe_success_knowledge_search(fixture_calls):
    # call[2] = KnowledgeSearch that returned a body.
    text = describe(fixture_calls[2])
    assert "knowledge" in text.lower()
    assert "query=" in text
    assert "returned" in text.lower()


def test_describe_zero_result_search(fixture_calls):
    # call[1] = KnowledgeSearch with no results.
    text = describe(fixture_calls[1])
    assert "no results" in text.lower()


def test_describe_failure_points_to_diagnosis(fixture_calls):
    # call[5] = failed Updateitem.
    text = describe(fixture_calls[5])
    assert "failed" in text.lower()
    assert "diagnosis" in text.lower()


def test_describe_synthetic_write_verb():
    text = describe(mk("CreateWidget", status="completed", result="{}", params={"name": "x"}))
    # A create/write verb is inferred generically from the tool name.
    assert "Create" in text or "created" in text.lower()


# --------------------------------------------------------------------------- #
# is_failure() — outcome detection across shapes                              #
# --------------------------------------------------------------------------- #


def test_is_failure_by_status():
    assert is_failure(mk("X", error="boom", status="failed")) is True


def test_is_failure_by_error_only():
    # status stays "completed" but an error string is present.
    assert is_failure(mk("X", error="something broke", status="completed")) is True


def test_is_failure_embedded_in_result():
    tc = mk("X", status="completed", result="error executing tool: could not be found")
    assert is_failure(tc) is True


def test_is_failure_false_on_success():
    assert is_failure(mk("Getitem", status="completed", result="ok")) is False


# --------------------------------------------------------------------------- #
# diagnose() generic tiers — bare HTTP status / signal words, NO named rule    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "error,expected",
    [
        ("Server responded with status 403", "permission"),
        ("HTTP 429 slow down", "rate-limit"),
        ("the operation timed out", "timeout"),
        ("returned 404 resource missing", "not-found"),
        ("code 500 internal boom", "server-error"),
        ("status 401 token expired", "authentication"),
        ("returned 409 already exists", "conflict"),
    ],
)
def test_generic_http_and_signal_categories(error, expected):
    tc = mk("SomeArbitraryTool", error=error)
    d = diagnose(tc)
    assert d.category == expected
    assert d.matched_rule is None  # generic tiers only, no YAML rule
    assert d.cause and d.fix  # never empty on a failure
    assert d.category in CATEGORIES


def test_unknown_novel_tool_still_diagnosed():
    # A tool we have never seen, with an error containing no status or signal word.
    tc = mk("ZorpFluxCapacitor", error="The flux capacitor overloaded unexpectedly")
    d = diagnose(tc)
    assert is_failure(tc) is True
    assert d.category in CATEGORIES  # falls back to a valid category (unknown)
    assert d.cause.strip()  # plain-language cause is present
    assert d.fix.strip()  # actionable fix is present
    assert isinstance(d.evidence, list)
    assert d.matched_rule is None


def test_diagnose_never_empty_on_any_failure():
    for err in ["", "weird", "boom 300", "\u2603", "null null null"]:
        tc = mk("Mystery", error=err or "unspecified failure")
        d = diagnose(tc)
        assert d.category in CATEGORIES
        assert d.cause and d.fix


# --------------------------------------------------------------------------- #
# diagnose() sentinel — only for non-failures                                 #
# --------------------------------------------------------------------------- #


def test_sentinel_for_non_failure():
    d = diagnose(mk("Getitem", status="completed", result="ok"))
    assert d.category == "none"  # sentinel, deliberately NOT in CATEGORIES
    assert d.category not in CATEGORIES


# --------------------------------------------------------------------------- #
# diagnose() named rules — sharpen wording for known signatures               #
# --------------------------------------------------------------------------- #


def test_fixture_placeholder_resolves_configuration(fixture_calls):
    d = diagnose(fixture_calls[5], siblings=fixture_calls)
    assert d.category == "configuration"
    assert d.matched_rule == "sharepoint-site-address-placeholder"
    assert d.cause and d.fix
    assert d.evidence  # auditable signals recorded


def test_fixture_passed_in_field_resolves_parameter_schema(fixture_calls):
    d = diagnose(fixture_calls[6], siblings=fixture_calls)
    assert d.category == "parameter-schema"
    assert d.matched_rule == "passed-in-field-not-found"
    assert d.category in CATEGORIES


def test_all_fixture_failures_diagnosed(fixture_calls):
    for i in (5, 6, 7):
        d = diagnose(fixture_calls[i], siblings=fixture_calls)
        assert d.category in CATEGORIES
        assert d.cause and d.fix
        assert d.evidence
