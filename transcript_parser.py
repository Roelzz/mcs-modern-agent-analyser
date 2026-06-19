"""Parse a modern Copilot Studio transcript into a `Conversation`.

The modern transcript is a flat JSON array of message objects::

    { "role": "bot"|"user", "id": "...", "text": "...",
      "toolCalls": [ { id, name, status, displayName, params, result } ],
      "thoughts":  [ { id, status, title, description } ] }

There are no timestamps, routing scores or plan trees. `KnowledgeSearch` tool
results are semi-structured text (``Title:`` / ``URL:`` / ``ReferenceId:`` blocks
with a ``[N results]`` header) which we parse best-effort.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from models import Conversation, FileAttachment, Message, RetrievedDoc, Thought, ToolCall, Turn

_RESULT_COUNT_RE = re.compile(r"\[\s*(\d+)\s+results?\s*\]", re.IGNORECASE)
_ZERO_RESULT_RE = re.compile(r"\b(no results|0 results|nothing found|no relevant)\b", re.IGNORECASE)


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


def _parse_tool_call(raw: dict) -> ToolCall:
    params = raw.get("params")
    result = raw.get("result")
    docs, count, zero = parse_knowledge_result(result if isinstance(result, str) else None)
    return ToolCall(
        id=raw.get("id"),
        name=raw.get("name"),
        status=raw.get("status"),
        display_name=raw.get("displayName"),
        params=params if isinstance(params, dict) else {},
        result=result if isinstance(result, str) else None,
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


def _extract_message_list(raw: object) -> list[dict]:
    """Accept a bare array or a wrapped object ({messages|activities|conversation})."""
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, dict):
        for key in ("messages", "activities", "conversation", "items"):
            value = raw.get(key)
            if isinstance(value, list):
                return [m for m in value if isinstance(m, dict)]
    raise ValueError("Unrecognised transcript shape (expected a JSON array of messages)")


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
