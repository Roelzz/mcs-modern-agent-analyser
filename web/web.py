"""Reflex app entrypoint for the modern agent analyser."""

import reflex as rx

from config import setup_logging
from web.components import index
from web.state import State

setup_logging()

app = rx.App()
app.add_page(index, title="Agent Analyser — Modern", on_load=State.initialize_page)
