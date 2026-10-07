"""Regression: uploading files must produce a report on a SINGLE Analyse click.

The bug: the Analyse button chained ``handle_upload`` (a file upload over a
separate HTTP POST channel) with a standalone ``run_analysis`` websocket event.
They raced, so on the first click ``run_analysis`` read empty state and bailed —
the user had to click twice. The fix runs analysis *inside* ``handle_upload`` so
a single upload handler yields a report. These tests exercise that handler
directly (no second call).
"""

import asyncio
from pathlib import Path

from web.state import State

CONNECTOR_FAIL = Path(__file__).parent.parent / "samples" / "sample_transcript_connector_fail.json"
AGENT_YAML = Path(__file__).parent.parent / "samples" / "sample_agent.yaml"


class _FakeUpload:
    """Minimal stand-in for rx.UploadFile: async read() + a .name."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _new_state() -> State:
    # Reflex forbids bare State(); the internal flag is the supported test path.
    return State(_reflex_internal_init=True)


def test_single_upload_click_produces_report():
    """One handle_upload call (a single Analyse click) yields a full report."""
    s = _new_state()
    data = CONNECTOR_FAIL.read_bytes()
    asyncio.run(s.handle_upload([_FakeUpload("sample_transcript_connector_fail.json", data)]))

    assert s._transcript_text  # upload landed
    assert s.error == ""
    assert s.has_report is True  # analysis ran in the SAME handler
    assert s._tool_calls_all  # report actually populated


def test_single_upload_click_agent_yaml_produces_report():
    """An agent-YAML-only upload also renders on a single click."""
    s = _new_state()
    data = AGENT_YAML.read_bytes()
    asyncio.run(s.handle_upload([_FakeUpload("sample_agent.yaml", data)]))

    assert s.agent_text
    assert s.error == ""
    assert s.has_report is True


def test_unroutable_upload_preserves_error_and_skips_analysis():
    """A file that can't be routed sets an error and does NOT fake a report."""
    s = _new_state()
    blob = b"plain text with no colon and no json"
    asyncio.run(s.handle_upload([_FakeUpload("notes.txt", blob)]))

    assert s.error  # routing error surfaced
    assert not s._transcript_text and not s.agent_text
    assert s.has_report is False  # guard held: no analysis on empty state
