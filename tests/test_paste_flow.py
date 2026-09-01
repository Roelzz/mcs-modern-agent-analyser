"""Regression: the Paste-JSON tab must accept every real transcript shape.

``analyse_pasted`` runs the analysis inline (same reason as ``handle_upload``:
a chained websocket event races the state write). These tests drive the handler
directly and assert both the accept and the reject paths, because routing used
to pass a fake ``pasted.json`` name that short-circuited content sniffing and
happily accepted any JSON object.
"""

import json
from pathlib import Path

from web.state import State

SAMPLES = Path(__file__).parent.parent / "samples"
ACTIVITIES = SAMPLES / "sample_transcript_activities.json"
FLAT = SAMPLES / "sample_transcript.json"


def _new_state() -> State:
    return State(_reflex_internal_init=True)


def _analyse(text: str) -> State:
    s = _new_state()
    s.paste_text = text
    s.analyse_pasted()
    return s


def test_paste_activities_envelope_produces_report():
    s = _analyse(ACTIVITIES.read_text(encoding="utf-8"))
    assert s.error == ""
    assert s.has_report is True
    assert s.turns
    assert s.transcript_name == "pasted transcript"


def test_paste_bare_activity_array_produces_report():
    activities = json.loads(ACTIVITIES.read_text(encoding="utf-8"))["activities"]
    s = _analyse(json.dumps(activities))
    assert s.error == ""
    assert s.has_report is True


def test_paste_flat_modern_array_still_works():
    s = _analyse(FLAT.read_text(encoding="utf-8"))
    assert s.error == ""
    assert s.has_report is True


def test_paste_dataverse_row_wrapper_produces_report():
    """A copied Dataverse row: {"value": [{"content": "<json string>"}]}."""
    raw = ACTIVITIES.read_text(encoding="utf-8")
    for key in ("value", "records"):
        s = _analyse(json.dumps({key: [{"content": raw}]}))
        assert s.error == "", key
        assert s.has_report is True, key


def test_paste_rejects_unrelated_json_object():
    s = _analyse('{"nothing": "useful"}')
    assert s.has_report is False
    assert "does not look like a transcript" in s.error


def test_paste_rejects_malformed_json():
    s = _analyse("{not json")
    assert s.has_report is False
    assert s.error


def test_paste_rejects_empty_input():
    s = _analyse("   ")
    assert s.has_report is False
    assert "Paste a transcript" in s.error


def test_paste_preview_and_validity_track_input():
    s = _new_state()
    assert s.paste_preview == ""
    assert s.paste_is_valid is False

    s.set_paste_text('{"activities": [{"type": "message"}]}')
    assert s.paste_is_valid is True
    assert "activities" in s.paste_preview

    s.set_paste_text("{broken")
    assert s.paste_is_valid is False
    assert "Not valid JSON" in s.paste_preview

    s.set_paste_text('{"nothing": "useful"}')
    assert s.paste_is_valid is False
    assert "no transcript array" in s.paste_preview


def test_clear_paste_resets_box():
    s = _new_state()
    s.set_paste_text('{"activities": []}')
    s.clear_paste()
    assert s.paste_text == ""
    assert s.error == ""


def test_clear_all_resets_paste_text():
    s = _new_state()
    s.set_paste_text('{"activities": []}')
    s.clear_all()
    assert s.paste_text == ""
