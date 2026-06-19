"""Logging + settings bootstrap. Import `setup_logging()` once at entry points."""

import os
import sys

from loguru import logger


def setup_logging() -> None:
    """Configure loguru from LOG_LEVEL (default INFO)."""
    logger.remove()
    logger.add(
        sink=sys.stderr,
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="{time:DD-MM-YYYY at HH:mm:ss} | {level: <8} | {message}",
    )


# --- MCS Copilot Credit billing rates (heuristic credit estimate) -----------
# Source: Microsoft Learn, "Billing rates and management" (Copilot Credits
# billing rates table). Rates are env-overridable for when Microsoft revises
# the model. These are an ESTIMATE only — see estimate_credits() for the
# event-to-rate mapping and its assumptions.
CREDIT_SOURCE_URL = (
    "https://learn.microsoft.com/microsoft-copilot-studio/"
    "requirements-messages-management#copilot-credits-billing-rates"
)
# Official interactive estimator (surfaced alongside our heuristic estimate).
CREDIT_ESTIMATOR_URL = "https://microsoft.github.io/copilot-studio-estimator/"


def credit_rates() -> dict[str, float]:
    """Copilot Credit cost per billable event kind (env-overridable).

    Source: Microsoft Learn "Billing rates and management". Modern agents that
    use a reasoning-capable model also bill the *premium AI-tools* token meter on
    top of every feature event, and document/content analysis bills per page —
    see estimate_credits()."""
    return {
        "classic_answer": float(os.getenv("CREDIT_CLASSIC_ANSWER", "1")),
        "generative_answer": float(os.getenv("CREDIT_GENERATIVE_ANSWER", "2")),
        "agent_action": float(os.getenv("CREDIT_AGENT_ACTION", "5")),
        "tenant_graph": float(os.getenv("CREDIT_TENANT_GRAPH", "10")),
        # Text & generative AI tools (premium) — reasoning-model surcharge, per 1K tokens.
        "premium_per_1k": float(os.getenv("CREDIT_PREMIUM_PER_1K", "10")),
        # Content-processing tools, per page (document/image analysis in the sandbox).
        "content_processing_page": float(os.getenv("CREDIT_CONTENT_PROCESSING_PAGE", "8")),
        # Agent flow actions, per 100 actions.
        "agent_flow_per_100": float(os.getenv("CREDIT_AGENT_FLOW_PER_100", "13")),
    }


# Token estimate: chars-per-token divisor (≈4 for English). Used to turn answer
# text length into a heuristic token count for the premium reasoning meter.
def chars_per_token() -> float:
    return float(os.getenv("CREDIT_CHARS_PER_TOKEN", "4"))


# Model series (lower-cased substrings) that are reasoning-capable and therefore
# incur the premium AI-tools token surcharge. Env-overridable comma list.
def reasoning_model_series() -> set[str]:
    raw = os.getenv("REASONING_MODEL_SERIES", "sonnet,opus,haiku,o1,o3,o4,gpt-5,gpt5,reason,phi-4-reason")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


# Tokens that, when found in reasoning thoughts or a tool-result preamble, signal
# the agent used the sandbox / code interpreter. Heuristic and non-exhaustive.
def code_interpreter_keywords() -> dict[str, list[str]]:
    return {
        "authoring": [
            "python-pptx",
            "python-docx",
            "openpyxl",
            "reportlab",
            "matplotlib",
            "savefig",
            "create a powerpoint",
            "create a pptx",
            "create a deck",
            "build the deck",
            "generate a file",
            "generate the file",
            "write the file",
            "presentation(",
            ".save(",
        ],
        "shell": ["bash", "shell", "command line", "terminal", " sh ", "run the command"],
        "tools": ["grep", "view ", "cat ", "head ", "tail ", "sed ", "awk", "ls "],
        "code": ["python", "node ", "script", "preprocess", "parser", "convert", "pandas"],
        "fs": ["/app/uploads", "_artifacts", "sandbox", "tmp", "directory", "file system", "filesystem"],
        "perms": ["permission", "sudo", "chmod", "drwx", "root can write", "read-only", "denied"],
    }


# --- Heuristic thresholds for the text-based analysers ----------------------
# These tune the fuzzy signals (repetition, per-answer groundedness). They are
# approximate by nature — env-overridable so they can be calibrated without code
# changes. Surfaced as *signals*, never hard verdicts.
def heuristic_thresholds() -> dict[str, float]:
    return {
        # Token Jaccard at/above which two texts count as a near-duplicate (#5).
        "repetition_jaccard": float(os.getenv("HEURISTIC_REPETITION_JACCARD", "0.8")),
        # Minimum shared tokens before a near-duplicate is worth reporting (#5).
        "min_repeat_tokens": float(os.getenv("HEURISTIC_MIN_REPEAT_TOKENS", "6")),
        # Chars below which an answer is too short to judge for grounding (#2).
        "substantive_min_chars": float(os.getenv("HEURISTIC_SUBSTANTIVE_MIN_CHARS", "120")),
    }
