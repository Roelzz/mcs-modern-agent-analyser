"""Parse the modern Copilot Studio agent build YAML (`BotDefinition`) into an
`AgentProfile`.

Modern agents (`template: cliagent-1.0.0`, `CLICopilotRecognizer`) keep their
config under `entity.configuration.agentSettings` (a single `model`, free-text
`instructions` segments, `enableMemory`, `conversationStarters`) plus top-level
`components` (knowledge sources) and `environmentVariables`.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import yaml
from loguru import logger

from models import AgentProfile, EnvVar, KnowledgeSource, ToolComponent, ToolOperation, ToolProvider

# Best-effort friendly labels for the `model.series` value. Falls back to raw.
_MODEL_LABELS: dict[str, str] = {
    "sonnet46": "Claude Sonnet 4.6",
    "sonnet45": "Claude Sonnet 4.5",
    "sonnet4": "Claude Sonnet 4",
    "sonnet37": "Claude Sonnet 3.7",
    "haiku45": "Claude Haiku 4.5",
    "haiku4": "Claude Haiku 4",
    "opus41": "Claude Opus 4.1",
    "opus4": "Claude Opus 4",
    "gpt4o": "GPT-4o",
    "gpt4omini": "GPT-4o mini",
    "gpt41": "GPT-4.1",
    "gpt41mini": "GPT-4.1 mini",
    "gpt5": "GPT-5",
}

# Top-level YAML keys that hold tool-like / agent-like definitions. Usually
# empty for a modern knowledge agent, but captured for cross-reference.
_TOOL_LIST_KEYS = (
    "flows",
    "connectorDefinitions",
    "connectionReferences",
    "aIPluginOperations",
    "aIModelDefinitions",
    "connectedAgentDefinitions",
    "connectedBots",
    "componentCollections",
)

# Component kinds (in `components`) that are NOT plain knowledge sources.
_TOOL_COMPONENT_KINDS = {"DialogComponent", "TaskDialog", "AgentDialog"}

# Top-level list key -> tool-provider kind. Used to build the Provider→Operation
# hierarchy. MCP servers are detected separately by `_looks_mcp` because they are
# usually exported as a connector-flavoured definition.
_PROVIDER_KIND_BY_KEY = {
    "flows": "flow",
    "connectorDefinitions": "connector",
    "connectionReferences": "connector",
    "aIPluginOperations": "connector",
    "connectedAgentDefinitions": "connectedAgent",
    "connectedBots": "connectedAgent",
}

# Sub-keys under a provider definition that may hold its operations.
_OPERATION_SUBKEYS = ("operations", "actions", "tools", "aIPluginOperations", "methods", "functions")
# Human-facing label precedence (friendly name beats machine slug).
_LABEL_KEYS = ("displayName", "name", "title", "schemaName", "id")
# Operation machine-name precedence (the stable id beats the friendly label).
_OPNAME_KEYS = ("name", "schemaName", "id", "displayName", "title")


def _first_str(item: dict, *keys: str) -> str | None:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _looks_mcp(item: dict) -> bool:
    """Best-effort: a connector definition is an MCP server when 'mcp' shows up
    in any of its identifying fields. Modern exports flag MCP tools this way."""
    blob = " ".join(
        str(item.get(k, "")) for k in ("kind", "name", "schemaName", "displayName", "type", "connectorType")
    ).lower()
    return "mcp" in blob


def _extract_operations(item: dict) -> list[ToolOperation]:
    ops: list[ToolOperation] = []
    seen: set[str] = set()
    for sk in _OPERATION_SUBKEYS:
        raw = item.get(sk)
        entries: list = raw if isinstance(raw, list) else (list(raw.values()) if isinstance(raw, dict) else [])
        for op in entries:
            if not isinstance(op, dict):
                if isinstance(op, str) and op.strip() and op.lower() not in seen:
                    seen.add(op.lower())
                    ops.append(ToolOperation(name=op.strip()))
                continue
            name = _first_str(op, *_OPNAME_KEYS)
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            ops.append(
                ToolOperation(
                    name=name,
                    display_name=_first_str(op, "displayName", "title"),
                    description=op.get("description") if isinstance(op.get("description"), str) else None,
                )
            )
    return ops


def _parse_tool_providers(raw: dict) -> list[ToolProvider]:
    """Defensive extraction of the Provider→Operation hierarchy from the modern
    BotDefinition. Knowledge agents have empty tool arrays, so this returns []
    for them; it only lights up when an export actually declares MCP servers,
    connectors, connected agents or flows."""
    providers: list[ToolProvider] = []
    for key, base_kind in _PROVIDER_KIND_BY_KEY.items():
        for item in raw.get(key) or []:
            if not isinstance(item, dict):
                continue
            kind = "mcpServer" if (base_kind == "connector" and _looks_mcp(item)) else base_kind
            name = _first_str(item, *_LABEL_KEYS) or kind
            providers.append(
                ToolProvider(
                    kind=kind,
                    display_name=name,
                    schema_name=_first_str(item, "schemaName", "name"),
                    description=item.get("description") if isinstance(item.get("description"), str) else None,
                    source=_first_str(item, "url", "endpoint", "connectionUrl", "host", "cdsBotId", "botId"),
                    configured=True,
                    operations=_extract_operations(item),
                )
            )
    if providers:
        logger.info(f"Parsed {len(providers)} tool provider(s) from agent definition.")
    return providers



def friendly_model(series: str | None) -> str | None:
    """Map a raw `model.series` value to a human label, best-effort."""
    if not series:
        return None
    return _MODEL_LABELS.get(series.lower(), series)


def _str_or_none(value: object) -> str | None:
    """Coerce a value to a string. PyYAML auto-parses ISO timestamps into
    `datetime` objects, so timestamp fields need normalising back to text."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()  # datetime / date
    return str(value)


def _as_str_list(value: object) -> list[str]:
    """Coerce conversationStarters-style values into a list of strings."""
    out: list[str] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(str(item.get("text") or item.get("title") or item.get("value") or item))
    return out


def _parse_knowledge_source(comp: dict) -> KnowledgeSource:
    config = comp.get("configuration") or {}
    source = config.get("source") or {}
    site = source.get("siteUrl")
    audit = comp.get("auditInfo") or {}
    return KnowledgeSource(
        display_name=str(comp.get("displayName") or comp.get("schemaName") or "Knowledge source"),
        schema_name=comp.get("schemaName"),
        description=comp.get("description"),
        source_kind=source.get("kind"),
        source_site=unquote(site) if isinstance(site, str) else site,
        state=comp.get("state"),
        status=comp.get("status"),
        modified_at=_str_or_none(audit.get("modifiedTimeUtc")),
    )


def _parse_env_var(ev: dict) -> EnvVar:
    return EnvVar(
        display_name=str(ev.get("displayName") or ev.get("schemaName") or "Variable"),
        schema_name=ev.get("schemaName"),
        type=ev.get("type"),
        default_value=None if ev.get("defaultValue") is None else str(ev.get("defaultValue")),
    )


def _parse_instructions(agent_settings: dict) -> tuple[str, list[str]]:
    """Join instruction segments into one string + keep the raw segment list."""
    instructions = agent_settings.get("instructions") or {}
    segments = instructions.get("segments")
    # `instructions` can also be a bare string in some exports.
    if segments is None and isinstance(instructions, str):
        return instructions, [instructions]
    seg_values: list[str] = []
    for seg in segments or []:
        if isinstance(seg, dict):
            val = seg.get("value")
            if isinstance(val, str) and val.strip():
                seg_values.append(val)
        elif isinstance(seg, str) and seg.strip():
            seg_values.append(seg)
    return "\n".join(seg_values), seg_values


def parse_agent_yaml(path: str | Path) -> AgentProfile:
    """Parse a modern `BotDefinition` YAML file into an `AgentProfile`."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_agent_obj(raw, source=path.name)


def parse_agent_yaml_text(text: str) -> AgentProfile:
    """Parse modern `BotDefinition` YAML already loaded as a string (web upload)."""
    raw = yaml.safe_load(text) or {}
    return parse_agent_obj(raw, source="<upload>")


def parse_agent_obj(raw: dict, source: str = "<obj>") -> AgentProfile:
    """Build an `AgentProfile` from an already-parsed YAML mapping."""
    if not isinstance(raw, dict):
        raise ValueError(f"Unexpected YAML root (expected mapping) in {source}")

    components = raw.get("components") or []
    knowledge_sources: list[KnowledgeSource] = []
    tool_components: list[ToolComponent] = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        kind = comp.get("kind", "")
        if kind == "KnowledgeSourceComponent":
            knowledge_sources.append(_parse_knowledge_source(comp))
        elif kind in _TOOL_COMPONENT_KINDS:
            tool_components.append(
                ToolComponent(
                    kind=kind,
                    display_name=str(comp.get("displayName") or comp.get("schemaName") or kind),
                    description=comp.get("description"),
                )
            )

    # Top-level tool/agent lists (usually empty for knowledge agents).
    for key in _TOOL_LIST_KEYS:
        for item in raw.get(key) or []:
            if isinstance(item, dict):
                tool_components.append(
                    ToolComponent(
                        kind=item.get("kind", key),
                        display_name=str(item.get("displayName") or item.get("schemaName") or item.get("name") or key),
                        description=item.get("description"),
                    )
                )

    env_vars = [_parse_env_var(ev) for ev in (raw.get("environmentVariables") or []) if isinstance(ev, dict)]
    tool_providers = _parse_tool_providers(raw)

    entity = raw.get("entity") or {}
    audit = entity.get("auditInfo") or {}
    config = entity.get("configuration") or {}
    recognizer = config.get("recognizer") or {}
    agent_settings = config.get("agentSettings") or {}
    model = agent_settings.get("model") or {}
    model_series = model.get("series") if isinstance(model, dict) else (model if isinstance(model, str) else None)

    instructions, segments = _parse_instructions(agent_settings)

    profile = AgentProfile(
        display_name=str(entity.get("displayName") or "Unknown agent"),
        schema_name=entity.get("schemaName"),
        cds_bot_id=entity.get("cdsBotId"),
        template=entity.get("template"),
        recognizer_kind=recognizer.get("kind"),
        authentication_mode=entity.get("authenticationMode"),
        authentication_trigger=entity.get("authenticationTrigger"),
        access_control_policy=entity.get("accessControlPolicy"),
        runtime_provider=entity.get("runtimeProvider"),
        model_series=model_series,
        model_label=friendly_model(model_series),
        instructions=instructions,
        instruction_segments=segments,
        enable_memory=bool(agent_settings.get("enableMemory", False)),
        conversation_starters=_as_str_list(agent_settings.get("conversationStarters")),
        knowledge_sources=knowledge_sources,
        environment_variables=env_vars,
        tool_components=tool_components,
        tool_providers=tool_providers,
        created_at=_str_or_none(audit.get("createdTimeUtc")),
        modified_at=_str_or_none(audit.get("modifiedTimeUtc")),
    )
    logger.info(
        f"Agent: {profile.display_name} | model={profile.model_label or '?'} | "
        f"{len(knowledge_sources)} knowledge source(s), {len(tool_components)} tool component(s)"
    )
    return profile
