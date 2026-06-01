"""DNS global search — find any record by hostname or IP across all zones."""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Static

log = logging.getLogger("netforge.dns_search")


@dataclass
class SearchResult:
    zone:        str
    server:      str
    name:        str
    record_type: str
    ttl:         int
    data:        str

    @property
    def fqdn(self) -> str:
        if self.name in ("@", ""):
            return self.zone
        return f"{self.name}.{self.zone}"


class DNSSearchModal(ModalScreen[SearchResult | None]):
    """
    Search all zones on all DNS servers for records matching a hostname or IP.

    Returns the selected SearchResult so the caller can navigate to that zone,
    or None if the user cancels.
    """

    DEFAULT_CSS = """
    DNSSearchModal { align: center middle; }
    DNSSearchModal > Vertical {
        width: 100;
        height: 38;
        border: solid $primary;
        background: $surface;
        padding: 0 2;
    }
    DNSSearchModal .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding: 1 0 0 0;
    }
    DNSSearchModal #search_bar {
        height: 3;
        align: left middle;
        margin: 1 0;
    }
    DNSSearchModal #search_bar Label { margin-right: 1; color: $text-muted; }
    DNSSearchModal #search_input   { width: 1fr; }
    DNSSearchModal #btn_search     { min-width: 12; margin-left: 1; }
    DNSSearchModal #result_table   { height: 1fr; }
    DNSSearchModal #status_bar {
        height: 1; padding: 0 1;
        color: $text-muted; background: $surface-darken-1;
        margin: 1 0 0 0;
    }
    DNSSearchModal #btn_bar { height: 3; align: center middle; }
    DNSSearchModal Button  { margin: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel",        "Cancel"),
        Binding("enter",  "do_search",     "Search",   show=False),
        Binding("ctrl+g", "goto_selected", "Go to Zone", show=False),
    ]

    def __init__(self, managers: dict, zone_entries: list):
        """
        managers:     dict[server_str, DNSManager]
        zone_entries: list[ZoneEntry]  — already-loaded zones to search across
        """
        super().__init__()
        self._managers    = managers
        self._zone_entries = zone_entries
        self._results: list[SearchResult] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Search All DNS Zones", classes="title")

            with Horizontal(id="search_bar"):
                yield Label("Search:")
                yield Input(
                    placeholder="hostname, FQDN, or IP address…",
                    id="search_input",
                )
                yield Button("Search", id="btn_search", variant="primary")

            yield Static(
                "Enter a hostname (partial or full) or IP address — searches all "
                "zones across all DNS servers",
                id="status_bar",
            )
            yield DataTable(id="result_table", cursor_type="row", zebra_stripes=True)

            with Horizontal(id="btn_bar"):
                yield Button("Go to Zone", id="btn_goto",   variant="primary")
                yield Button("Cancel",     id="btn_cancel", variant="default")

    def on_mount(self) -> None:
        table = self.query_one("#result_table", DataTable)
        table.add_columns("FQDN", "Type", "Data", "Zone", "Server")
        # Focus the search input immediately
        self.query_one("#search_input", Input).focus()

    # ---- Search ----

    @on(Button.Pressed, "#btn_search")
    def _btn_search(self) -> None:
        self.action_do_search()

    @on(Input.Submitted, "#search_input")
    def _input_submitted(self) -> None:
        self.action_do_search()

    def action_do_search(self) -> None:
        query = self.query_one("#search_input", Input).value.strip()
        if not query:
            return
        self._run_search(query)

    @work(thread=True)
    def _run_search(self, query: str) -> None:
        app    = self.app
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update,
            f"Searching {len(self._zone_entries)} zones for '{query}'…")

        results: list[SearchResult] = []
        q_lower = query.lower()
        zones_searched = 0
        zones_total    = len(self._zone_entries)

        for entry in self._zone_entries:
            try:
                records = self._managers[entry.server].list_records(entry.zone.name)
                zones_searched += 1
                for r in records:
                    if (q_lower in r.name.lower()
                            or q_lower in r.data.lower()
                            or q_lower in f"{r.name}.{entry.zone.name}".lower()):
                        results.append(SearchResult(
                            zone=entry.zone.name,
                            server=entry.server or "local",
                            name=r.name,
                            record_type=r.record_type,
                            ttl=r.ttl,
                            data=r.data,
                        ))
                # Update progress every 5 zones
                if zones_searched % 5 == 0:
                    app.call_from_thread(status.update,
                        f"Searching… {zones_searched}/{zones_total} zones  "
                        f"({len(results)} matches so far)")
            except Exception as e:
                log.warning("Search skipped zone %s: %s", entry.zone.name, e)

        self._results = results
        app.call_from_thread(self._show_results, query, zones_searched)

    def _show_results(self, query: str, zones_searched: int) -> None:
        table  = self.query_one("#result_table", DataTable)
        status = self.query_one("#status_bar",   Static)
        table.clear()

        type_styles = {
            "A": "green", "AAAA": "cyan",
            "CNAME": "magenta", "PTR": "yellow",
        }
        for r in self._results:
            table.add_row(
                Text(r.fqdn),
                Text(r.record_type, style=type_styles.get(r.record_type, "")),
                Text(r.data),
                Text(r.zone),
                Text(r.server),
            )

        if self._results:
            status.update(
                f"{len(self._results)} record(s) found in "
                f"{zones_searched} zones  ·  "
                f"Enter or 'Go to Zone' to navigate"
            )
        else:
            status.update(
                f"No records found for '{query}' in {zones_searched} zones"
            )

    # ---- Selection / navigation ----

    @on(DataTable.RowSelected, "#result_table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_goto_selected()

    @on(Button.Pressed, "#btn_goto")
    def _btn_goto(self) -> None:
        self.action_goto_selected()

    def action_goto_selected(self) -> None:
        table = self.query_one("#result_table", DataTable)
        idx   = table.cursor_row
        if idx is not None and 0 <= idx < len(self._results):
            self.dismiss(self._results[idx])

    # ---- Cancel ----

    @on(Button.Pressed, "#btn_cancel")
    def _btn_cancel(self) -> None:
        self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)
