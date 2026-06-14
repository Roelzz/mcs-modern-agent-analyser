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


def credit_rates() -> dict[str, float]:
    """Copilot Credit cost per billable event kind (env-overridable)."""
    return {
        "classic_answer": float(os.getenv("CREDIT_CLASSIC_ANSWER", "1")),
        "generative_answer": float(os.getenv("CREDIT_GENERATIVE_ANSWER", "2")),
        "agent_action": float(os.getenv("CREDIT_AGENT_ACTION", "5")),
    }
