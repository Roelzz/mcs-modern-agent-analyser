"""CLI entry point for the modern agent analyser.

Generates a Markdown report from a modern transcript JSON and/or a modern agent
build YAML. This is the developer/testing surface; the primary product is the
Reflex web app (`uv run reflex run`).
"""

from __future__ import annotations

from pathlib import Path

import typer
from loguru import logger

from agent_parser import parse_agent_yaml
from analysis import analyze
from config import setup_logging
from renderer import render_markdown
from transcript_parser import parse_transcript

setup_logging()

app = typer.Typer(help="Analyse modern Copilot Studio agents (transcript JSON + agent YAML) into a Markdown report.")


@app.command()
def analyse(
    transcript: Path | None = typer.Argument(None, help="Path to a modern transcript JSON file."),
    agent: Path | None = typer.Option(None, "--agent", "-g", help="Path to the modern agent build YAML."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output Markdown path (default: stdout)."),
) -> None:
    """Analyse a transcript and/or agent YAML and emit a Markdown report."""
    if transcript is None and agent is None:
        logger.error("Provide a transcript JSON (positional) and/or an agent YAML (--agent).")
        raise typer.Exit(2)

    profile = None
    convo = None

    if agent is not None:
        if not agent.exists():
            logger.error(f"Agent YAML not found: {agent}")
            raise typer.Exit(1)
        profile = parse_agent_yaml(agent)

    if transcript is not None:
        if not transcript.exists():
            logger.error(f"Transcript not found: {transcript}")
            raise typer.Exit(1)
        convo = parse_transcript(transcript)

    report = analyze(profile, convo)
    markdown = render_markdown(report, convo)

    if output is None:
        typer.echo(markdown)
    else:
        output.write_text(markdown, encoding="utf-8")
        logger.info(f"Report written to {output} ({len(report.findings)} finding(s))")


if __name__ == "__main__":
    app()
