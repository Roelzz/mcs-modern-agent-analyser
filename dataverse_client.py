"""Dataverse OAuth device flow and conversation transcript client."""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

import httpx
from loguru import logger

DEFAULT_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SCHEMA_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
_DEFAULT_ALLOWED_HOST_SUFFIXES = (
    ".dynamics.com",
    ".microsoftdynamics.de",
    ".dynamics.cn",
    ".crm.appsplatform.us",
)
_ODATA_HEADERS = {
    "Accept": "application/json",
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
}


def _validate_uuid(value: str, label: str) -> str:
    value = value.strip()
    if not _UUID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a UUID.")
    return value


def normalise_org_url(value: str) -> str:
    """Validate a Dataverse environment URL and return it without a trailing slash."""
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Environment URL must be an HTTPS Dataverse URL.")
    if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise ValueError("Environment URL must contain only the Dataverse HTTPS origin.")
    if parsed.path not in ("", "/"):
        raise ValueError("Environment URL must not include an API path.")

    configured = os.getenv("DATAVERSE_ALLOWED_HOST_SUFFIXES", "")
    suffixes = tuple(s.strip().lower() for s in configured.split(",") if s.strip())
    suffixes = suffixes or _DEFAULT_ALLOWED_HOST_SUFFIXES
    host = parsed.hostname.lower()
    if not any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in suffixes):
        raise ValueError(
            "Environment URL is not a recognised Dataverse host. "
            "Set DATAVERSE_ALLOWED_HOST_SUFFIXES if your sovereign cloud uses another domain."
        )
    return f"https://{host}"


def validate_auth_config(tenant_id: str, client_id: str, org_url: str) -> tuple[str, str, str]:
    return (
        _validate_uuid(tenant_id, "Tenant ID"),
        _validate_uuid(client_id or DEFAULT_CLIENT_ID, "Client ID"),
        normalise_org_url(org_url),
    )


async def initiate_device_flow(
    tenant_id: str,
    client_id: str,
    org_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Start the Microsoft identity platform OAuth device-code flow."""
    tenant_id, client_id, org_url = validate_auth_config(tenant_id, client_id, org_url)
    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode",
            data={"client_id": client_id, "scope": f"{org_url}/.default offline_access openid profile"},
            timeout=30,
        )
        response.raise_for_status()
        flow = response.json()
        if not flow.get("device_code") or not flow.get("user_code"):
            raise RuntimeError("Microsoft identity did not return a device code.")
        return flow
    finally:
        if owns_client:
            await client.aclose()


async def acquire_device_flow_token(
    tenant_id: str,
    client_id: str,
    device_code: str,
    *,
    expires_in: int,
    interval: int = 5,
    client: httpx.AsyncClient | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> str | None:
    """Poll the token endpoint until the user completes or rejects sign-in."""
    tenant_id = _validate_uuid(tenant_id, "Tenant ID")
    client_id = _validate_uuid(client_id or DEFAULT_CLIENT_ID, "Client ID")
    deadline = time.monotonic() + max(1, expires_in)
    delay = max(1, interval)
    owns_client = client is None
    client = client or httpx.AsyncClient()

    try:
        while time.monotonic() < deadline:
            if is_cancelled is not None and await is_cancelled():
                return None
            response = await client.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                },
                timeout=30,
            )
            payload = response.json()
            if response.is_success and payload.get("access_token"):
                return str(payload["access_token"])

            error = str(payload.get("error") or "")
            if error == "authorization_pending":
                await asyncio.sleep(delay)
                continue
            if error == "slow_down":
                delay += 5
                await asyncio.sleep(delay)
                continue
            description = payload.get("error_description") or error or response.text
            raise RuntimeError(f"Authentication failed: {description}")
    finally:
        if owns_client:
            await client.aclose()

    raise RuntimeError("Authentication timed out before the device code was completed.")


class DataverseClient:
    """Small authenticated Dataverse Web API client."""

    def __init__(
        self,
        org_url: str,
        access_token: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.org_url = normalise_org_url(org_url)
        if not access_token:
            raise ValueError("An access token is required.")
        self._access_token = access_token
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def __aenter__(self) -> DataverseClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}", **_ODATA_HEADERS}

    @staticmethod
    def _raise_for_table_access(response: httpx.Response, table: str) -> None:
        if response.status_code == 401:
            raise RuntimeError("Dataverse session expired. Disconnect and sign in again.")
        if response.status_code == 403:
            raise RuntimeError(f"Access denied. Your account needs Read access to the {table} table.")
        if response.is_error:
            try:
                message = response.json().get("error", {}).get("message")
            except (TypeError, ValueError):
                message = None
            if message:
                raise RuntimeError(f"Dataverse rejected the request: {message}")
        response.raise_for_status()

    async def resolve_bot_guid(self, bot_identifier: str) -> str:
        identifier = bot_identifier.strip()
        if _UUID_RE.fullmatch(identifier):
            return identifier
        if not _SCHEMA_NAME_RE.fullmatch(identifier):
            raise ValueError("Copilot ID must be a UUID or a valid Dataverse bot schema name.")

        response = await self._client.get(
            f"{self.org_url}/api/data/v9.2/bots",
            headers=self.headers,
            params={
                "$filter": f"schemaname eq '{identifier}'",
                "$select": "botid,name,schemaname",
                "$top": "2",
            },
            timeout=30,
        )
        self._raise_for_table_access(response, "Bot")
        records = response.json().get("value", [])
        if not records:
            raise RuntimeError(f"No bot found with schema name '{identifier}'.")
        if len(records) > 1:
            raise RuntimeError(f"Multiple bots matched schema name '{identifier}'. Use the Copilot ID UUID.")
        return str(records[0]["botid"])

    async def fetch_transcripts(self, bot_identifier: str, since: datetime, top: int = 50) -> list[dict]:
        bot_guid = await self.resolve_bot_guid(bot_identifier)
        since_utc = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = await self._client.get(
            f"{self.org_url}/api/data/v9.2/conversationtranscripts",
            headers=self.headers,
            params={
                "$filter": (f"_bot_conversationtranscriptid_value eq {bot_guid} and createdon gt {since_utc}"),
                "$select": "conversationtranscriptid,createdon,name",
                "$top": str(max(1, min(top, 250))),
                "$orderby": "createdon desc",
            },
            timeout=60,
        )
        self._raise_for_table_access(response, "ConversationTranscript")
        records = response.json().get("value", [])
        logger.info(f"Fetched {len(records)} transcript(s) from Dataverse")
        return records

    async def fetch_transcript_by_id(self, conversation_id: str) -> dict:
        conversation_id = _validate_uuid(conversation_id, "Conversation ID")
        response = await self._client.get(
            f"{self.org_url}/api/data/v9.2/conversationtranscripts({conversation_id})",
            headers=self.headers,
            params={"$select": "conversationtranscriptid,createdon,content"},
            timeout=60,
        )
        if response.status_code == 404:
            raise RuntimeError(f"No transcript found with ID '{conversation_id}'.")
        self._raise_for_table_access(response, "ConversationTranscript")
        return response.json()
