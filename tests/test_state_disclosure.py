"""Tool-call disclosure defaults + expand/collapse-all on the app State.

Covers the surfacing behaviour the user asked for:
  * a FAILED call auto-opens itself and every detail section on load
    (What happened / Params / Response / Error / Diagnosis),
  * a SUCCESSFUL call stays fully collapsed,
  * Expand-all opens every flat call + section,
  * Collapse-all clears the flat list but leaves fail:: rows untouched.
"""

from pathlib import Path

import pytest

from analysis import analyze
from models import AgentProfile
from transcript_parser import parse_transcript_text
from web.state import State
from web.view_models import map_report

CONNECTOR_FAIL = (
    Path(__file__).parent.parent / "samples" / "sample_transcript_connector_fail.json"
)
_SECTIONS = ("what", "params", "resp", "err", "diag")


@pytest.fixture()
def seeded_state():
    # Reflex forbids bare State() — the internal flag is the supported test path.
    s = State(_reflex_internal_init=True)
    txt = CONNECTOR_FAIL.read_text(encoding="utf-8")
    convo = parse_transcript_text(txt)
    vm = map_report(analyze(AgentProfile(), convo), convo, raw_transcript=txt)
    s._apply_vm(vm)
    return s


def _payloadless(tc):
    return not (tc.params or tc.raw_result or tc.docs or tc.content_html or tc.content_text)


def _fail_ids(s):
    return [tc.call_id for tc in s.tool_calls_all if tc.failed]


def _success_ids(s):
    return [tc.call_id for tc in s.tool_calls_all if not tc.failed and tc.call_id]


def _plain_success_ids(s):
    # Successful AND carrying payload — these are the ones that stay collapsed.
    return [
        tc.call_id
        for tc in s.tool_calls_all
        if not tc.failed and tc.call_id and not _payloadless(tc)
    ]


def test_failed_calls_autoexpand_all_sections(seeded_state):
    s = seeded_state
    fails = _fail_ids(s)
    assert len(fails) == 3
    for cid in fails:
        assert cid in s.tool_open
        for sec in (*_SECTIONS, "ctx"):
            assert f"{cid}::{sec}" in s.tool_sections_open


def test_plain_successful_calls_stay_collapsed(seeded_state):
    s = seeded_state
    plain = _plain_success_ids(s)
    assert plain  # e.g. the Getitem read + retrieval searches
    for cid in plain:
        assert cid not in s.tool_open
        for sec in (*_SECTIONS, "ctx"):
            assert f"{cid}::{sec}" not in s.tool_sections_open


def test_payloadless_calls_autoopen_what_and_context(seeded_state):
    # A payload-less successful call (a bare skill load) has nothing in
    # params/resp/err — it must auto-open What happened + the derived Context
    # so it isn't a dead, empty card on load.
    s = seeded_state
    payloadless = [
        tc.call_id
        for tc in s.tool_calls_all
        if not tc.failed and tc.call_id and _payloadless(tc)
    ]
    assert payloadless  # the two skill loads in the sample
    for cid in payloadless:
        assert cid in s.tool_open
        assert f"{cid}::what" in s.tool_sections_open
        assert f"{cid}::ctx" in s.tool_sections_open
        # ...but NOT the payload sections that have nothing to show.
        for sec in ("params", "resp", "err", "diag"):
            assert f"{cid}::{sec}" not in s.tool_sections_open


def test_failure_rows_seed_fail_prefixed_keys(seeded_state):
    s = seeded_state
    for cid in _fail_ids(s):
        assert f"fail::{cid}" in s.tool_open


def test_expand_all_opens_every_flat_call_and_section(seeded_state):
    s = seeded_state
    s.expand_all_tools()
    for tc in s.tool_calls_all:
        assert tc.call_id in s.tool_open
        for sec in (*_SECTIONS, "ctx"):
            assert f"{tc.call_id}::{sec}" in s.tool_sections_open


def test_collapse_all_clears_flat_but_keeps_fail_rows(seeded_state):
    s = seeded_state
    s.expand_all_tools()
    s.collapse_all_tools()
    flat_ids = {tc.call_id for tc in s.tool_calls_all}
    # No flat call id (or its sections) remains open.
    assert not any(c in flat_ids for c in s.tool_open)
    assert not any(k.split("::")[0] in flat_ids for k in s.tool_sections_open)
    # fail:: rows are a distinct surface and must survive collapse-all.
    assert any(c.startswith("fail::") for c in s.tool_open)
