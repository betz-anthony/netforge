"""DHCP global search — find any lease or reservation across all scopes and servers."""

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
from textual.widgets import Button, DataTable, Input, Label, Select, Static

log = logging.getLogger("netforge.dhcp_search")

SEARCH_ALL          = "all"
SEARCH_LEASES       = "leases"
SEARCH_RESERVATIONS = "reservations"


@dataclass
class DhcpSearchResult:
    scope_id:    str
    server:      str
    result_type: str   # "lease" or "reservation"
    ip_address:  str
    client_id:   str   # MAC
    hostname:    str
    state:       str   # lease state or reservation type
    description: str = ""


class DHCPSearchModal(ModalScreen[DhcpSearchResult | None]):
    """
    Search all scopes across all DHCP servers for leases or reservations
    matching an IP, MAC address, or hostname.

    Returns the selected DhcpSearchResult so the caller can navigate to
    that scope, or None if the user cancels.
    """

    DEFAULT_CSS = """
    DHCPSearchModal { align: center middle; }
    DHCPSearchModal > Vertical {
        width: 100;
        height: 38;
        border: solid $primary;
        background: $surface;
        padding: 0 2;
    }
    DHCPSearchModal .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding: 1 0 0 0;
    }
    DHCPSearchModal #search_bar {
        height: 3;
        align: left middle;
        margin: 1 0 0 0;
    }
    DHCPSearchModal #search_bar Label  { margin-right: 1; color: $text-muted; }
    DHCPSearchModal #search_input      { width: 1fr; }
    DHCPSearchModal #type_select       { width: 22; margin-left: 1; }
    DHCPSearchModal #btn_search        { min-width: 12; margin-left: 1; }
    DHCPSearchModal #result_table      { height: 1fr; }
    DHCPSearchModal #status_bar {
        height: 1; padding: 0 1;
        color: $text-muted; background: $surface-darken-1;
        margin: 1 0 0 0;
    }
    DHCPSearchModal #btn_bar { height: 3; align: center middle; }
    DHCPSearchModal Button   { margin: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel",        "Cancel"),
        Binding("ctrl+g", "goto_selected", "Go to Scope", show=False),
    ]

    def __init__(self, managers: dict, scope_entries: list):
        """
        managers:     dict[server_str, DHCPManager]
        scope_entries: list[ScopeEntry] — already-loaded scopes to search across
        """
        super().__init__()
        self._managers     = managers
        self._scope_entries = scope_entries
        self._results: list[DhcpSearchResult] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Search All DHCP Scopes", classes="title")

            with Horizontal(id="search_bar"):
                yield Label("Search:")
                yield Input(
                    placeholder="IP address, MAC, or hostname…",
                    id="search_input",
                )
                yield Select(
                    [
                        ("Leases + Reservations", SEARCH_ALL),
                        ("Leases only",           SEARCH_LEASES),
                        ("Reservations only",     SEARCH_RESERVATIONS),
                    ],
                    id="type_select",
                    value=SEARCH_ALL,
                )
                yield Button("Search", id="btn_search", variant="primary")

            yield Static(
                "Enter an IP, MAC address (any format), or hostname fragment",
                id="status_bar",
            )
            yield DataTable(id="result_table", cursor_type="row", zebra_stripes=True)

            with Horizontal(id="btn_bar"):
                yield Button("Go to Scope", id="btn_goto",   variant="primary")
                yield Button("Cancel",      id="btn_cancel", variant="default")

    def on_mount(self) -> None:
        table = self.query_one("#result_table", DataTable)
        table.add_columns("IP Address", "MAC", "Hostname", "Type", "State/Type", "Scope", "Server")
        self.query_one("#search_input", Input).focus()

    # ---- Search ----

    @on(Button.Pressed, "#btn_search")
    def _btn_search(self) -> None:
        self._do_search()

    @on(Input.Submitted, "#search_input")
    def _input_submitted(self) -> None:
        self._do_search()

    def _do_search(self) -> None:
        query = self.query_one("#search_input", Input).value.strip()
        if not query:
            return
        search_type = self.query_one("#type_select", Select).value
        self._run_search(query, search_type)

    @work(thread=True)
    def _run_search(self, query: str, search_type: str) -> None:
        app    = self.app
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update,
            f"Searching {len(self._scope_entries)} scopes for '{query}'…")

        # Normalise MAC query — strip separators for flexible matching
        q_lower   = query.lower()
        q_mac_raw = q_lower.replace(":", "").replace("-", "").replace(".", "")

        results: list[DhcpSearchResult] = []
        scopes_searched = 0
        scopes_total    = len(self._scope_entries)

        for entry in self._scope_entries:
            mgr = self._managers[entry.server]
            scopes_searched += 1

            # Search leases
            if search_type in (SEARCH_ALL, SEARCH_LEASES):
                try:
                    leases = mgr.list_leases(entry.scope.scope_id)
                    for lease in leases:
                        mac_raw = lease.client_id.lower().replace(":", "").replace("-", "")
                        if (q_lower in lease.ip_address.lower()
                                or q_lower in lease.hostname.lower()
                                or q_lower in lease.client_id.lower()
                                or (q_mac_raw and q_mac_raw in mac_raw)):
                            results.append(DhcpSearchResult(
                                scope_id=entry.scope.scope_id,
                                server=entry.server or "local",
                                result_type="lease",
                                ip_address=lease.ip_address,
                                client_id=lease.client_id,
                                hostname=lease.hostname,
                                state=lease.address_state,
                            ))
                except Exception as e:
                    log.warning("Search leases skipped scope %s: %s",
                                entry.scope.scope_id, e)

            # Search reservations
            if search_type in (SEARCH_ALL, SEARCH_RESERVATIONS):
                try:
                    reservations = mgr.list_reservations(entry.scope.scope_id)
                    for res in reservations:
                        mac_raw = res.client_id.lower().replace(":", "").replace("-", "")
                        if (q_lower in res.ip_address.lower()
                                or q_lower in res.name.lower()
                                or q_lower in res.client_id.lower()
                                or q_lower in (res.description or "").lower()
                                or (q_mac_raw and q_mac_raw in mac_raw)):
                            results.append(DhcpSearchResult(
                                scope_id=entry.scope.scope_id,
                                server=entry.server or "local",
                                result_type="reservation",
                                ip_address=res.ip_address,
                                client_id=res.client_id,
                                hostname=res.name,
                                state=res.reservation_type,
                                description=res.description or "",
                            ))
                except Exception as e:
                    log.warning("Search reservations skipped scope %s: %s",
                                entry.scope.scope_id, e)

            if scopes_searched % 3 == 0:
                app.call_from_thread(status.update,
                    f"Searching… {scopes_searched}/{scopes_total} scopes  "
                    f"({len(results)} matches so far)")

        self._results = results
        app.call_from_thread(self._show_results, query, scopes_searched)

    def _show_results(self, query: str, scopes_searched: int) -> None:
        table  = self.query_one("#result_table", DataTable)
        status = self.query_one("#status_bar",   Static)
        table.clear()

        lease_state_styles = {
            "Active":              "green",
            "Expired":             "red",
            "ActiveReservation":   "cyan",
            "InactiveReservation": "yellow",
        }

        for r in self._results:
            type_style  = "cyan" if r.result_type == "reservation" else "green"
            state_style = lease_state_styles.get(r.state, "")
            table.add_row(
                Text(r.ip_address),
                Text(r.client_id),
                Text(r.hostname),
                Text(r.result_type.capitalize(), style=type_style),
                Text(r.state, style=state_style),
                Text(r.scope_id),
                Text(r.server),
            )

        if self._results:
            leases_cnt = sum(1 for r in self._results if r.result_type == "lease")
            res_cnt    = sum(1 for r in self._results if r.result_type == "reservation")
            parts = []
            if leases_cnt:
                parts.append(f"{leases_cnt} lease(s)")
            if res_cnt:
                parts.append(f"{res_cnt} reservation(s)")
            status.update(
                f"{' + '.join(parts)} found in {scopes_searched} scopes  ·  "
                f"Enter or 'Go to Scope' to navigate"
            )
        else:
            status.update(
                f"No results for '{query}' in {scopes_searched} scopes"
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
