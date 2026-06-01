"""Diagnostic overlay — shows live connection progress and errors."""

from __future__ import annotations

import traceback

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Static


class DiagModal(ModalScreen):
    """
    Live diagnostic overlay shown only for Ctrl+D diagnostics.

    On success: show_close_button() is called. When user clicks Close,
    the modal calls app.on_diag_dismissed(pool, profile) to rebuild panels.
    On failure: stays open showing full error + traceback.
    """

    DEFAULT_CSS = """
    DiagModal { align: center middle; }
    DiagModal > Vertical {
        width: 90; height: 32;
        border: solid $warning;
        background: $surface;
        padding: 0 1;
    }
    DiagModal .title {
        text-align: center; text-style: bold;
        color: $warning; padding: 1 0 0 0;
    }
    DiagModal RichLog {
        height: 1fr;
        border: solid $primary-darken-2;
        background: $background;
        padding: 0 1; margin: 1 0;
    }
    DiagModal #btn_bar { height: 3; align: center middle; }
    DiagModal Button   { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "close_diag", "Close")]

    def __init__(self, host: str):
        super().__init__()
        self._host           = host
        self._result_pool    = None   # set by _diag_connect on success
        self._result_profile = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Connecting to {self._host}", classes="title")
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
            with Horizontal(id="btn_bar"):
                yield Button("Close", id="btn_close", variant="default")

    def on_mount(self) -> None:
        self.query_one("#btn_close", Button).display = False

    def log_step(self, msg: str) -> None:
        self.query_one(RichLog).write(f"[cyan]  →[/]  {msg}")

    def log_ok(self, msg: str) -> None:
        self.query_one(RichLog).write(f"[green]  ✓[/]  {msg}")

    def log_error(self, msg: str, exc: Exception | None = None) -> None:
        log = self.query_one(RichLog)
        log.write(f"[red]  ✗[/]  {msg}")
        if exc is not None:
            tb = traceback.format_exc()
            log.write("")
            log.write("[bold red]Traceback:[/]")
            for line in tb.splitlines():
                log.write(f"[red]{line}[/]")
            log.write(f"[bold red]Exception:[/] {type(exc).__name__}: {exc}")
        self.show_close_button()

    def show_close_button(self) -> None:
        self.query_one("#btn_close", Button).display = True

    @on(Button.Pressed, "#btn_close")
    def _close(self) -> None:
        self._dismiss()

    def action_close_diag(self) -> None:
        self._dismiss()

    def _dismiss(self) -> None:
        pool    = self._result_pool
        profile = self._result_profile
        self.dismiss()
        # Only notify app if connection succeeded
        if pool is not None and profile is not None:
            try:
                self.app.on_diag_dismissed(pool, profile)
            except Exception:
                pass
