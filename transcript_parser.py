"""Parse a Copilot Studio transcript into a `Conversation`.

Two on-the-wire shapes are supported and auto-detected:

1. **Modern flat array** — what the Copilot Studio test pane exports::

       [ { "role": "bot"|"user", "id": "...", "text": "...",
           "toolCalls": [ { id, name, status, displayName, params, result } ],
           "thoughts":  [ { id, status, title, description } ] } ]

2. **Dataverse `conversationtranscript` envelope** — the Bot Framework activity
   log you get when copying the `content` column out of Dataverse::

       { "activities": [ { "type": "message"|"trace"|"event",
                           "from": { "id": "...", "role": 0|1 },
                           "text": "...", "timestampMs": 1700000000000 } ] }

   Activities are normalised into the modern shape before parsing, so everything
   downstream (turn grouping, analysis, rendering) only ever sees one format.

`KnowledgeSearch` tool results are semi-structured text (``Title:`` / ``URL:`` /
``ReferenceId:`` blocks with a ``[N results]`` header) which we parse best-effort.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from models import Conversation, FileAttachment, Message, RetrievedDoc, Thought, ToolCall, Turn

_RESULT_COUNT_RE = re.compile(r"\[\s*(\d+)\s+results?\s*\]", re.IGNORECASE)
_ZERO_RESULT_RE = re.compile(r"\b(no results|0 results|nothing found|no relevant)\b", re.IGNORECASE)

# Keys that mark a JSON object as a transcript envelope rather than a bare array.
TRANSCRIPT_ENVELOPE_KEYS = ("activities", "records", "value", "messages", "conversation", "items")

# Bot Framework activity types we recognise.
_ACTIVITY_TYPES = {"message", "trace", "event", "typing", "conversationupdate", "endofconversation"}

# Trace valueTypes that carry conversation metadata, not agent work.
_METADATA_VALUE_TYPES = {"conversationinfo", "sessioninfo", "channeldata"}

# Trace valueTypes that clearly describe agent reasoning rather than a tool call.
_THOUGHT_VALUE_HINTS = ("thought", "reason", "plan", "chainofthought", "deliberat")


def parse_knowledge_result(text: str | None) -> tuple[list[RetrievedDoc], int | None, bool]:
    """Parse a KnowledgeSearch-style result blob.

    Returns ``(docs, result_count, zero_result)``. Safe to call on any tool
    result text — returns empty/neutral values when there is nothing to parse.
    """
    if not text:
        return [], None, False

    count: int | None = None
    m = _RESULT_COUNT_RE.search(text)
    if m:
        count = int(m.group(1))

    docs: list[RetrievedDoc] = []
    current: dict[str, str | None] = {}
    snippet_lines: list[str] = []

    def _flush() -> None:
        if current.get("title") or current.get("url") or current.get("reference_id"):
            snippet = " ".join(snippet_lines).strip() or None
            docs.append(
                RetrievedDoc(
                    title=current.get("title"),
                    url=current.get("url"),
                    reference_id=current.get("reference_id"),
                    snippet=snippet,
                )
            )
        snippet_lines.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Title:"):
            _flush()
            current = {"title": stripped[len("Title:") :].strip() or None}
        elif stripped.startswith("URL:"):
            current["url"] = stripped[len("URL:") :].strip() or None
        elif stripped.startswith("ReferenceId:"):
            current["reference_id"] = stripped[len("ReferenceId:") :].strip() or None
        elif current and stripped and stripped != "---":
            # Body text that follows a doc's structural lines = its snippet summary.
            snippet_lines.append(stripped)
    _flush()

    zero = False
    if count is not None:
        zero = count == 0
    elif not docs and _ZERO_RESULT_RE.search(text):
        zero = True

    return docs, count, zero


def _coerce_payload_text(value: object) -> str | None:
    """A tool-call ``result``/``error`` may arrive as a raw string or as an
    already-decoded JSON object. Preserve strings; JSON-encode structured payloads
    so the full body survives to the UI untruncated."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _parse_tool_call(raw: dict) -> ToolCall:
    params = raw.get("params")
    result = _coerce_payload_text(raw.get("result"))
    error = _coerce_payload_text(raw.get("error"))
    docs, count, zero = parse_knowledge_result(result)
    return ToolCall(
        id=raw.get("id"),
        name=raw.get("name"),
        status=raw.get("status"),
        display_name=raw.get("displayName"),
        params=params if isinstance(params, dict) else {},
        result=result,
        error=error,
        retrieved_docs=docs,
        result_count=count,
        zero_result=zero,
    )


def _parse_thought(raw: dict) -> Thought:
    return Thought(
        id=raw.get("id"),
        status=raw.get("status"),
        title=raw.get("title"),
        description=raw.get("description"),
    )


def _parse_message(raw: dict) -> Message:
    tool_calls = [_parse_tool_call(tc) for tc in (raw.get("toolCalls") or []) if isinstance(tc, dict)]
    thoughts = [_parse_thought(t) for t in (raw.get("thoughts") or []) if isinstance(t, dict)]
    attachments = [
        FileAttachment(
            name=str(a.get("name") or ""),
            file_type=str(a.get("fileType") or ""),
            content_type=str(a.get("contentType") or ""),
        )
        for a in (raw.get("fileAttachments") or [])
        if isinstance(a, dict)
    ]
    role = str(raw.get("role") or "bot")
    return Message(
        role=role,
        id=raw.get("id"),
        text=str(raw.get("text") or ""),
        tool_calls=tool_calls,
        thoughts=thoughts,
        file_attachments=attachments,
        occurred_at=raw.get("timestamp") or raw.get("occurredAt"),
    )


def _group_turns(messages: list[Message]) -> list[Turn]:
    """Group messages into turns. A turn starts at a user message and includes
    the bot messages that follow it. Leading bot messages (a greeting before any
    user input) form a turn with ``user_message=None``."""
    turns: list[Turn] = []
    current: Turn | None = None
    idx = 0

    for msg in messages:
        if msg.is_user:
            if current is not None:
                turns.append(current)
            current = Turn(index=idx, user_message=msg)
            idx += 1
        else:  # bot (or any non-user)
            if current is None:
                current = Turn(index=idx, user_message=None)
                idx += 1
            current.bot_messages.append(msg)

    if current is not None:
        turns.append(current)
    return turns


def _looks_like_activities(items: list[dict]) -> bool:
    """True when a list holds Bot Framework activities rather than modern messages.

    Activities always carry a ``type`` discriminator and a ``from`` participant;
    modern messages carry a top-level ``role`` and never a ``type``."""
    for item in items:
        if "role" in item and "type" not in item:
            return False
        if str(item.get("type", "")).lower() in _ACTIVITY_TYPES:
            return True
    return False


def _activity_role(raw: dict) -> str:
    """Map a Bot Framework participant onto ``user`` / ``bot``.

    Dataverse serialises the role as a number (0 = bot, 1 = user); the public
    Bot Framework schema uses the strings ``"bot"`` / ``"user"``."""
    sender = raw.get("from")
    role = sender.get("role") if isinstance(sender, dict) else None

    if isinstance(role, str):
        low = role.strip().lower()
        if low in ("user", "bot"):
            return low
        if low.isdigit():
            role = int(low)
    if isinstance(role, bool):  # bool is an int subclass — treat as unknown
        role = None
    if isinstance(role, int):
        return "user" if role == 1 else "bot"
    return "bot"


def _activity_timestamp(raw: dict) -> str | None:
    """Normalise ``timestampMs`` / ``timestamp`` to an ISO-8601 UTC string."""
    millis = raw.get("timestampMs")
    if isinstance(millis, (int, float)) and not isinstance(millis, bool) and millis > 0:
        return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat().replace("+00:00", "Z")

    stamp = raw.get("timestamp")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and stamp > 0:
        return datetime.fromtimestamp(stamp, tz=UTC).isoformat().replace("+00:00", "Z")
    if isinstance(stamp, str) and stamp.strip():
        return stamp.strip()
    return None


def _activity_attachments(raw: dict) -> list[dict]:
    """Keep real file attachments; drop adaptive cards and other inline payloads."""
    out: list[dict] = []
    for att in raw.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        name = str(att.get("name") or "").strip()
        if not name:
            continue  # cards and inline content have no filename
        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        out.append(
            {
                "name": name,
                "fileType": str(att.get("fileType") or suffix),
                "contentType": str(att.get("contentType") or ""),
            }
        )
    return out


def _trace_to_tool_call(raw: dict) -> dict | None:
    """Best-effort: turn a tool-execution trace activity into a modern toolCall.

    Copilot Studio does not publish a stable schema for these traces, so we only
    accept a value that both names something and shows an execution outcome."""
    value = raw.get("value")
    if not isinstance(value, dict):
        return None

    name = value.get("name") or value.get("toolName") or value.get("actionName")
    if not name:
        return None
    if not any(k in value for k in ("result", "output", "status", "params", "parameters", "arguments", "error")):
        return None

    return {
        "id": value.get("id") or raw.get("id"),
        "name": str(name),
        "status": value.get("status"),
        "displayName": value.get("displayName") or value.get("title"),
        "params": value.get("params") or value.get("parameters") or value.get("arguments"),
        "result": value.get("result") if "result" in value else value.get("output"),
        "error": value.get("error"),
    }


def _trace_to_thought(raw: dict, value_type: str) -> dict | None:
    """Best-effort: turn a reasoning trace activity into a modern thought."""
    value = raw.get("value")
    if not isinstance(value, dict):
        return None
    if not any(hint in value_type for hint in _THOUGHT_VALUE_HINTS):
        return None

    title = value.get("title") or value.get("name")
    description = value.get("description") or value.get("text") or value.get("content")
    if not (title or description):
        return None

    return {
        "id": value.get("id") or raw.get("id"),
        "status": value.get("status"),
        "title": title,
        "description": description,
    }


def _normalise_activities(activities: list[dict]) -> list[dict]:
    """Flatten Bot Framework activities into the modern flat-message shape.

    Tool calls and thoughts can arrive either inline on the message
    (``channelData``) or as separate ``trace`` activities that precede the bot
    reply; buffered traces are attached to the next bot message."""
    messages: list[dict] = []
    pending_tools: list[dict] = []
    pending_thoughts: list[dict] = []
    unknown_traces: set[str] = set()

    for act in activities:
        kind = str(act.get("type") or "").lower()

        if kind == "trace":
            value_type = str(act.get("valueType") or act.get("name") or "").lower()
            if value_type in _METADATA_VALUE_TYPES:
                continue
            thought = _trace_to_thought(act, value_type)
            if thought is not None:
                pending_thoughts.append(thought)
                continue
            tool = _trace_to_tool_call(act)
            if tool is not None:
                pending_tools.append(tool)
                continue
            if value_type:
                unknown_traces.add(value_type)
            continue

        if kind != "message":
            continue  # event / typing / conversationUpdate carry no transcript content

        role = _activity_role(act)
        channel = act.get("channelData") if isinstance(act.get("channelData"), dict) else {}
        tool_calls = [tc for tc in (channel.get("toolCalls") or []) if isinstance(tc, dict)]
        thoughts = [t for t in (channel.get("thoughts") or []) if isinstance(t, dict)]

        if role == "bot":
            tool_calls = pending_tools + tool_calls
            thoughts = pending_thoughts + thoughts
            pending_tools, pending_thoughts = [], []

        messages.append(
            {
                "role": role,
                "id": act.get("id"),
                "text": act.get("text") or "",
                "toolCalls": tool_calls,
                "thoughts": thoughts,
                "fileAttachments": _activity_attachments(act),
                "timestamp": _activity_timestamp(act),
            }
        )

    # Traces that never found a following bot message still belong to the run.
    if pending_tools or pending_thoughts:
        last_bot = next((m for m in reversed(messages) if m["role"] == "bot"), None)
        if last_bot is not None:
            last_bot["toolCalls"] = list(last_bot["toolCalls"]) + pending_tools
            last_bot["thoughts"] = list(last_bot["thoughts"]) + pending_thoughts

    if unknown_traces:
        logger.debug(f"Ignored unrecognised trace valueType(s): {', '.join(sorted(unknown_traces))}")

    return messages


def _unwrap_records(records: list) -> list[dict]:
    """Dataverse row exports nest the activity JSON inside a `content` column."""
    activities: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        content = record.get("content") or record.get("Content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                logger.warning("Skipping conversationtranscript record with unparsable `content`")
                continue
        if isinstance(content, dict):
            content = content.get("activities")
        if isinstance(content, list):
            activities.extend(a for a in content if isinstance(a, dict))
    return activities


def _extract_message_list(raw: object) -> list[dict]:
    """Return modern flat messages from any supported transcript shape."""
    if isinstance(raw, list):
        items = [m for m in raw if isinstance(m, dict)]
        return _normalise_activities(items) if _looks_like_activities(items) else items

    if isinstance(raw, dict):
        records = raw.get("records") or raw.get("value")
        if isinstance(records, list) and records and isinstance(records[0], dict) and "type" not in records[0]:
            activities = _unwrap_records(records)
            if activities:
                return _normalise_activities(activities)

        for key in TRANSCRIPT_ENVELOPE_KEYS:
            value = raw.get(key)
            if isinstance(value, list):
                items = [m for m in value if isinstance(m, dict)]
                return _normalise_activities(items) if _looks_like_activities(items) else items

    raise ValueError("Unrecognised transcript shape (expected a JSON array of messages or activities)")


def parse_transcript(path: str | Path) -> Conversation:
    """Parse a modern transcript JSON file into a `Conversation`."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw_messages = _extract_message_list(raw)

    messages = [_parse_message(m) for m in raw_messages]
    turns = _group_turns(messages)

    convo = Conversation(messages=messages, turns=turns)
    logger.info(
        f"Transcript: {len(messages)} message(s), {len(turns)} turn(s), "
        f"{len(convo.tool_calls)} tool call(s), {len(convo.thoughts)} thought(s) from {path.name}"
    )
    return convo


def parse_transcript_text(text: str) -> Conversation:
    """Parse transcript JSON already loaded as a string (used by the web upload)."""
    raw = json.loads(text)
    raw_messages = _extract_message_list(raw)
    messages = [_parse_message(m) for m in raw_messages]
    return Conversation(messages=messages, turns=_group_turns(messages))
