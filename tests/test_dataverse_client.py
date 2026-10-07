"""Dataverse connection and transcript-import regression tests."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

import httpx
import pytest
import reflex as rx

import web.state as state_module
from dataverse_client import (
    DataverseClient,
    acquire_device_flow_token,
    initiate_device_flow,
    normalise_org_url,
)
from rxconfig import config
from web.state import State, _summarise_dataverse_record

TENANT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "22222222-2222-2222-2222-222222222222"
BOT_ID = "33333333-3333-3333-3333-333333333333"
CONVERSATION_ID = "44444444-4444-4444-4444-444444444444"
ORG_URL = "https://contoso.crm4.dynamics.com"


def _new_state() -> State:
    return State(_reflex_internal_init=True)


def test_normalise_org_url_accepts_dataverse_origin():
    assert normalise_org_url(f"{ORG_URL}/") == ORG_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://contoso.crm4.dynamics.com",
        "https://localhost",
        "https://example.com",
        "https://contoso.crm4.dynamics.com/api/data/v9.2",
    ],
)
def test_normalise_org_url_rejects_non_dataverse_targets(url):
    with pytest.raises(ValueError):
        normalise_org_url(url)


def test_session_details_autofill():
    state = _new_state()
    state.dv_session_details_paste = f"Tenant ID: {TENANT_ID}\nInstance url: {ORG_URL}/\nCopilot Id: {BOT_ID}\n"
    state.dv_autofill_from_session_details()

    assert state.dv_tenant_id == TENANT_ID
    assert state.dv_org_url == ORG_URL
    assert state.dv_bot_identifier == BOT_ID
    assert state.dv_autofill_error == ""


def test_connection_fields_use_browser_local_storage():
    expected_keys = {
        "dv_org_url": "agent-analyser-dv-org-url",
        "dv_tenant_id": "agent-analyser-dv-tenant-id",
        "dv_client_id": "agent-analyser-dv-client-id",
        "dv_bot_identifier": "agent-analyser-dv-bot-id",
    }
    for field_name, storage_name in expected_keys.items():
        default = State.get_fields()[field_name].default
        assert isinstance(default, rx.LocalStorage)
        assert default.name == storage_name


def test_forget_connection_info_clears_cached_fields():
    state = _new_state()
    state.dv_org_url = ORG_URL
    state.dv_tenant_id = TENANT_ID
    state.dv_client_id = CLIENT_ID
    state.dv_bot_identifier = BOT_ID

    state.dv_forget_connection_info()

    assert state.dv_org_url == ""
    assert state.dv_tenant_id == ""
    assert state.dv_client_id == "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
    assert state.dv_bot_identifier == ""


def test_dataverse_auth_handler_runs_in_background():
    assert State.start_device_flow.is_background is True


def test_successful_auth_survives_initial_fetch_failure(monkeypatch):
    state = _new_state()
    state.dv_org_url = ORG_URL
    state.dv_tenant_id = TENANT_ID
    state.dv_client_id = CLIENT_ID
    state.dv_bot_identifier = BOT_ID
    state.dv_since_date = "2026-09-01"

    async def fake_flow(*_args, **_kwargs):
        return {
            "user_code": "ABCD-EFGH",
            "device_code": "device-code",
            "expires_in": 300,
            "interval": 1,
        }

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    async def failed_fetch(*_args, **_kwargs):
        raise RuntimeError("Copilot ID was not found")

    monkeypatch.setattr(state_module, "initiate_device_flow", fake_flow)
    monkeypatch.setattr(state_module, "acquire_device_flow_token", fake_token)
    monkeypatch.setattr(state_module, "_fetch_dataverse_records", failed_fetch)

    asyncio.run(State.start_device_flow.fn(state))

    assert state.dv_is_connected is True
    assert state._dv_token == "access-token"
    assert state.dv_auth_error == ""
    assert state.dv_fetch_error == "Copilot ID was not found"


def test_page_initialization_refreshes_since_date(monkeypatch):
    state = _new_state()
    state.dv_since_date = ""
    monkeypatch.setattr(state_module, "_fetch_community_count", lambda: 123)

    asyncio.run(state.initialize_page())

    assert state.dv_since_date
    assert state.analyses_count == 123


def test_reflex_state_manager_avoids_disk_storage():
    expected = rx.constants.StateManagerMode.REDIS if os.getenv("REDIS_URL") else rx.constants.StateManagerMode.MEMORY
    assert config.state_manager_mode == expected


def test_raw_transcript_is_backend_only():
    fields = State.get_fields()
    assert "transcript_text" not in fields
    assert "raw_transcript" not in fields
    assert "_transcript_text" in fields


def test_disconnect_discards_inflight_transcript_fetch(monkeypatch):
    state = _new_state()
    state.dv_is_connected = True
    state._dv_token = "access-token"
    state.dv_org_url = ORG_URL
    state.dv_bot_identifier = BOT_ID
    state.dv_since_date = "2026-09-01"
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_fetch(*_args, **_kwargs):
        started.set()
        await release.wait()
        return (
            [
                {
                    "id": CONVERSATION_ID,
                    "short_id": "44444444...",
                    "created_on": "2026-10-06",
                    "preview": "Sensitive conversation",
                    "activity_count": 2,
                }
            ],
            0,
        )

    monkeypatch.setattr(state_module, "_fetch_dataverse_records", delayed_fetch)

    async def run():
        pending = asyncio.create_task(State.dv_fetch_transcripts.fn(state))
        await started.wait()
        state.dv_disconnect()
        release.set()
        await pending

    asyncio.run(run())

    assert state.dv_is_connected is False
    assert state.dv_transcripts == []


def test_disconnect_clears_active_dataverse_transcript():
    state = _new_state()
    state.dv_is_connected = True
    state._dv_token = "access-token"
    state._transcript_text = '{"activities":[]}'
    state.transcript_name = "dataverse-44444444.json"
    state.has_report = True

    state.dv_disconnect()

    assert state._transcript_text == ""
    assert state.transcript_name == ""
    assert state.has_report is False


def test_clear_all_resets_dataverse_loading_flags():
    state = _new_state()
    state.dv_is_fetching = True
    state.dv_single_fetching = True
    state.dv_fetch_error = "error"
    state.dv_single_fetch_error = "error"

    state.clear_all()

    assert state.dv_is_fetching is False
    assert state.dv_single_fetching is False
    assert state.dv_fetch_error == ""
    assert state.dv_single_fetch_error == ""


def test_dataverse_record_summary_extracts_first_user_message():
    content = {
        "activities": [
            {"type": "message", "from": {"role": 0}, "text": "Hello"},
            {"type": "message", "from": {"role": 1}, "text": "Show my leave balance"},
        ]
    }
    summary, content_text = _summarise_dataverse_record(
        {
            "conversationtranscriptid": CONVERSATION_ID,
            "createdon": "2026-10-06T08:30:00Z",
            "content": json.dumps(content),
        }
    )

    assert summary["preview"] == "Show my leave balance"
    assert summary["activity_count"] == 2
    assert json.loads(content_text) == content


def test_dataverse_record_summary_supports_modern_flat_messages():
    summary, _content_text = _summarise_dataverse_record(
        {
            "conversationtranscriptid": CONVERSATION_ID,
            "createdon": "2026-10-06T08:30:00Z",
            "content": [
                {"role": "user", "text": "Analyse this modern conversation"},
                {"role": "bot", "text": "Sure"},
            ],
        }
    )

    assert summary["preview"] == "Analyse this modern conversation"
    assert summary["activity_count"] == 2


def test_selected_dataverse_transcript_uses_existing_analysis_pipeline(monkeypatch):
    state = _new_state()
    state.dv_is_connected = True
    state._dv_token = "access-token"
    state.dv_org_url = ORG_URL
    content = json.dumps(
        {
            "activities": [
                {"type": "message", "from": {"role": 1}, "text": "Hello"},
                {"type": "message", "from": {"role": 0}, "text": "Hi there"},
            ]
        }
    )

    async def fetch_transcript(*_args, **_kwargs):
        return (
            {
                "id": CONVERSATION_ID,
                "short_id": "44444444...",
                "created_on": "2026-10-06",
                "preview": "Hello",
                "activity_count": 2,
            },
            content,
        )

    monkeypatch.setattr(state_module, "_fetch_dataverse_transcript", fetch_transcript)
    asyncio.run(State.dv_analyse_transcript.fn(state, CONVERSATION_ID))

    assert state.error == ""
    assert state.has_report is True
    assert state.transcript_name == "dataverse-44444444.json"
    assert state.m_user == 1
    assert state.m_bot == 1


def test_direct_lookup_rejects_non_uuid_before_network_call():
    state = _new_state()
    state.dv_is_connected = True
    state._dv_token = "test-token"
    state.dv_conversation_id = "not-a-uuid"

    asyncio.run(State.dv_fetch_and_analyse_by_id.fn(state))
    assert state.dv_single_fetch_error == "Conversation ID must be a UUID."


def test_device_flow_and_token_polling():
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "expires_in": 900,
                    "interval": 1,
                },
            )
        calls["token"] += 1
        return httpx.Response(200, json={"access_token": "access-token"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            flow = await initiate_device_flow(TENANT_ID, CLIENT_ID, ORG_URL, client=client)
            token = await acquire_device_flow_token(
                TENANT_ID,
                CLIENT_ID,
                flow["device_code"],
                expires_in=flow["expires_in"],
                interval=flow["interval"],
                client=client,
            )
        return flow, token

    flow, token = asyncio.run(run())
    assert flow["user_code"] == "ABCD-EFGH"
    assert token == "access-token"
    assert calls["token"] == 1


def test_device_flow_polling_can_be_cancelled():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "authorization_pending"})

    async def cancelled():
        return True

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await acquire_device_flow_token(
                TENANT_ID,
                CLIENT_ID,
                "device-code",
                expires_in=30,
                interval=1,
                client=client,
                is_cancelled=cancelled,
            )

    assert asyncio.run(run()) is None
    assert calls == 0


def test_fetch_transcripts_sends_bearer_token_and_filter():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["filter"] = request.url.params["$filter"]
        seen["select"] = request.url.params["$select"]
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "conversationtranscriptid": CONVERSATION_ID,
                        "createdon": "2026-10-06T08:30:00Z",
                        "content": '{"activities":[]}',
                    }
                ]
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            async with DataverseClient(ORG_URL, "test-token", client=http_client) as client:
                return await client.fetch_transcripts(BOT_ID, datetime(2026, 10, 1, tzinfo=UTC))

    records = asyncio.run(run())
    assert len(records) == 1
    assert seen["authorization"] == "Bearer test-token"
    assert seen["filter"] == (f"_bot_conversationtranscriptid_value eq {BOT_ID} and createdon gt 2026-10-01T00:00:00Z")
    assert seen["select"] == "conversationtranscriptid,createdon,name"


def test_dataverse_error_body_is_preserved():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid OData filter."}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            async with DataverseClient(ORG_URL, "test-token", client=http_client) as client:
                return await client.fetch_transcripts(BOT_ID, datetime(2026, 10, 1, tzinfo=UTC))

    with pytest.raises(RuntimeError, match="Dataverse rejected the request: Invalid OData filter"):
        asyncio.run(run())
