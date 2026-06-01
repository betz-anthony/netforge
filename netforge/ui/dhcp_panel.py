"""DHCP tab — multi-server scope list + lease/reservation viewer."""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button, DataTable, Input, Label, ListItem, ListView, Select, Static,
)

from netforge.modules.dhcp import DHCPManager, DhcpLease, DhcpReservation, DhcpScope
from netforge.transport.winrm import WinRMSessionPool
from netforge.ui.reservation_modal import ReservationModal
from netforge.ui.dhcp_search_modal import DHCPSearchModal

log = logging.getLogger("netforge.dhcp_panel")

VIEW_LEASES       = "leases"
VIEW_RESERVATIONS = "reservations"


@dataclass
class ScopeEntry:
    server: str
    scope: DhcpScope

    @property
    def display_server(self) -> str:
        return self.server or "local"


class DHCPPanel(Vertical):
    """DHCP management — scope list + lease/reservation viewer."""

    BINDINGS = [
        Binding("n", "new_reservation",    "New Reservation"),
        Binding("e", "edit_reservation",   "Edit"),
        Binding("d", "delete_reservation", "Delete"),
        Binding("p", "promote_lease",      "Promote"),
        Binding("r", "refresh",            "Refresh"),
        Binding("s", "search_all",         "Search All Scopes"),
    ]

    DEFAULT_CSS = """
    DHCPPanel { layout: horizontal; }
    DHCPPanel #scope_pane {
        width: 36;
        border-right: solid $primary-darken-2;
    }
    DHCPPanel #scope_pane Label {
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }
    DHCPPanel #scope_list { height: 1fr; }
    DHCPPanel #main_pane  { width: 1fr; }
    DHCPPanel #view_bar {
        height: 3;
        align: left middle;
        padding: 0 1;
        border-bottom: solid $primary-darken-2;
    }
    DHCPPanel #view_bar Label { margin-right: 1; color: $text-muted; }
    DHCPPanel #filter_bar {
        height: 3;
        align: left middle;
        padding: 0 1;
        border-bottom: solid $primary-darken-2;
    }
    DHCPPanel #filter_bar Label { margin-right: 1; color: $text-muted; }
    DHCPPanel #filter_input { width: 24; }
    DHCPPanel #main_table { height: 1fr; }
    DHCPPanel #status_bar {
        height: 1; padding: 0 1;
        color: $text-muted; background: $surface-darken-1;
    }
    """

    def __init__(self, pool: WinRMSessionPool, dhcp_servers: list[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._pool = pool
        self._dhcp_servers: list[str] = dhcp_servers or [""]
        self._managers: dict[str, DHCPManager] = {
            s: DHCPManager(pool, server=s) for s in self._dhcp_servers
        }
        self._scope_entries:  list[ScopeEntry]       = []
        self._leases:         list[DhcpLease]         = []
        self._reservations:   list[DhcpReservation]   = []
        self._selected_entry: ScopeEntry | None       = None
        self._view         = VIEW_LEASES
        self._filter       = ""
        self._loading_data = False
        self._loaded       = False

    def compose(self) -> ComposeResult:
        with Vertical(id="scope_pane"):
            yield Label("DHCP Scopes")
            yield ListView(id="scope_list")
        with Vertical(id="main_pane"):
            with Horizontal(id="view_bar"):
                yield Label("View:")
                yield Select(
                    [("Leases", VIEW_LEASES), ("Reservations", VIEW_RESERVATIONS)],
                    id="view_select", value=VIEW_LEASES,
                )
                yield Button("N New",     id="btn_new",     variant="default")
                yield Button("E Edit",    id="btn_edit",    variant="default")
                yield Button("D Del",     id="btn_del",     variant="error")
                yield Button("P Promote", id="btn_promote", variant="default")
                yield Button("S Search",  id="btn_search",  variant="default")
                yield Button("R Refresh", id="btn_refresh", variant="default")
            with Horizontal(id="filter_bar"):
                yield Label("Filter:")
                yield Input(placeholder="IP, MAC, hostname…", id="filter_input")
            yield DataTable(id="main_table", cursor_type="row", zebra_stripes=True)
            yield Static("Select a scope to view leases", id="status_bar")

    def on_mount(self) -> None:
        self._setup_columns(VIEW_LEASES)

    def load(self) -> None:
        """Called when panel first becomes visible."""
        if self._parent is None:
            log.warning("DHCPPanel.load() called before mounted — skipping")
            return
        if not self._loaded:
            self._loaded = True
            self._load_all_scopes()

    def _setup_columns(self, view: str) -> None:
        table = self.query_one("#main_table", DataTable)
        table.clear(columns=True)
        if view == VIEW_LEASES:
            table.add_columns("IP Address", "MAC", "Hostname", "State", "Expires")
        else:
            table.add_columns("IP Address", "MAC", "Name", "Description", "Type")

    # ---- Scope loading ----

    @work(thread=True)
    def _load_all_scopes(self) -> None:
        app    = self.app
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update,
            f"Loading scopes from {len(self._dhcp_servers)} server(s)…")
        entries: list[ScopeEntry] = []
        for server in self._dhcp_servers:
            label = server or "local"
            try:
                log.debug("_load_all_scopes  server=%s", label)
                scopes = self._managers[server].list_scopes()
                log.debug("  got %d scopes from %s", len(scopes), label)
                for s in scopes:
                    entries.append(ScopeEntry(server=server, scope=s))
            except Exception as e:
                log.error("_load_all_scopes failed for %s: %s\n%s",
                          label, e, traceback.format_exc())
                app.call_from_thread(status.update,
                    f"[red]Error from {label}: {e}[/]")
        self._scope_entries = entries
        app.call_from_thread(self._populate_scopes)

    def _populate_scopes(self) -> None:
        try:
            lv = self.query_one("#scope_list", ListView)
            lv.clear()
            current_server = None
            for entry in self._scope_entries:
                if len(self._dhcp_servers) > 1 and entry.server != current_server:
                    current_server = entry.server
                    lv.append(ListItem(Label(f"── {entry.display_server} ──", markup=False)))
                state = "● " if entry.scope.active else "○ "
                name_part = f"  {entry.scope.name}" if entry.scope.name else ""
                lv.append(ListItem(Label(
                    f"{state}{entry.scope.scope_id}{name_part}", markup=False
                )))
            total = len(self._scope_entries)
            servers_str = (f"{len(self._dhcp_servers)} server(s)"
                           if len(self._dhcp_servers) > 1
                           else (self._dhcp_servers[0] or "local"))
            self.query_one("#status_bar", Static).update(
                f"{total} scopes  ·  {servers_str}"
            )
            if self._scope_entries:
                self._selected_entry = self._scope_entries[0]
                self._load_data()
        except Exception as e:
            log.error("_populate_scopes crashed: %s\n%s", e, traceback.format_exc())

    def _list_index_to_entry(self, lv_index: int) -> ScopeEntry | None:
        if len(self._dhcp_servers) <= 1:
            if 0 <= lv_index < len(self._scope_entries):
                return self._scope_entries[lv_index]
            return None
        entry_idx = 0
        current_server = None
        row = 0
        for entry in self._scope_entries:
            if entry.server != current_server:
                current_server = entry.server
                if row == lv_index:
                    return None
                row += 1
            if row == lv_index:
                return entry
            row += 1
            entry_idx += 1
        return None

    @on(ListView.Selected, "#scope_list")
    def _scope_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None:
            return
        entry = self._list_index_to_entry(idx)
        if entry:
            self._selected_entry = entry
            self._load_data()

    @on(Select.Changed, "#view_select")
    def _view_changed(self, event: Select.Changed) -> None:
        self._view = event.value
        self._setup_columns(self._view)
        # Reload from server for the current scope with the new view
        if self._selected_entry:
            self._loading_data = False   # reset guard so reload proceeds
            self._load_data()
        else:
            self._populate_table()

    @on(Input.Changed, "#filter_input")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._filter = event.value
        self._populate_table()

    def _load_data(self) -> None:
        if self._view == VIEW_LEASES:
            self._load_leases()
        else:
            self._load_reservations()

    def _manager_for_selected(self) -> DHCPManager | None:
        if self._selected_entry is None:
            return None
        return self._managers[self._selected_entry.server]

    # ---- Leases ----

    @work(thread=True)
    def _load_leases(self) -> None:
        app   = self.app
        entry = self._selected_entry
        if not entry:
            return
        if self._loading_data:
            log.debug("_load_leases skipped — already loading")
            return
        self._loading_data = True
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update,
            f"Loading leases for {entry.scope.scope_id}…")
        try:
            log.debug("_load_leases  scope=%s  server=%s",
                      entry.scope.scope_id, entry.display_server)
            self._leases = self._managers[entry.server].list_leases(entry.scope.scope_id)
            log.debug("  got %d leases", len(self._leases))
            app.call_from_thread(self._populate_table)
        except Exception as e:
            log.error("_load_leases failed: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(status.update, f"[red]Error: {e}[/]")
        finally:
            self._loading_data = False

    # ---- Reservations ----

    @work(thread=True)
    def _load_reservations(self) -> None:
        app   = self.app
        entry = self._selected_entry
        if not entry:
            return
        if self._loading_data:
            log.debug("_load_reservations skipped — already loading")
            return
        self._loading_data = True
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update,
            f"Loading reservations for {entry.scope.scope_id}…")
        try:
            log.debug("_load_reservations  scope=%s  server=%s",
                      entry.scope.scope_id, entry.display_server)
            self._reservations = self._managers[entry.server].list_reservations(
                entry.scope.scope_id
            )
            log.debug("  got %d reservations", len(self._reservations))
            app.call_from_thread(self._populate_table)
        except Exception as e:
            log.error("_load_reservations failed: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(status.update, f"[red]Error: {e}[/]")
        finally:
            self._loading_data = False

    def _populate_table(self) -> None:
        try:
            table = self.query_one("#main_table", DataTable)
            table.clear()
            entry        = self._selected_entry
            scope_label  = f" — {entry.scope.scope_id}"  if entry else ""
            server_label = f"  [{entry.display_server}]" if entry else ""
            filt = self._filter.lower()

            if self._view == VIEW_LEASES:
                state_styles = {
                    "Active": "green", "Expired": "red",
                    "ActiveReservation": "cyan", "InactiveReservation": "yellow",
                }
                shown = [
                    l for l in self._leases
                    if not filt or filt in l.ip_address.lower()
                    or filt in l.client_id.lower()
                    or filt in l.hostname.lower()
                    or filt in l.address_state.lower()
                ]
                for lease in shown:
                    style = state_styles.get(lease.address_state, "")
                    table.add_row(
                        Text(lease.ip_address),
                        Text(lease.client_id),
                        Text(lease.hostname),
                        Text(lease.address_state, style=style),
                        Text(lease.lease_expires[:19] if lease.lease_expires else "—"),
                    )
                self.query_one("#status_bar", Static).update(
                    f"{len(shown)} leases{scope_label}{server_label}"
                    + (f"  filter: {self._filter}" if self._filter else "")
                )
            else:
                shown = [
                    r for r in self._reservations
                    if not filt or filt in r.ip_address.lower()
                    or filt in r.client_id.lower()
                    or filt in r.name.lower()
                    or filt in (r.description or "").lower()
                ]
                for res in shown:
                    table.add_row(
                        Text(res.ip_address),
                        Text(res.client_id),
                        Text(res.name),
                        Text(res.description or "—"),
                        Text(res.reservation_type),
                    )
                self.query_one("#status_bar", Static).update(
                    f"{len(shown)} reservations{scope_label}{server_label}"
                    + (f"  filter: {self._filter}" if self._filter else "")
                )
        except Exception as e:
            log.error("_populate_table crashed: %s\n%s", e, traceback.format_exc())

    # ---- Actions ----

    def action_new_reservation(self) -> None:
        if not self._selected_entry:
            return
        self.app.push_screen(
            ReservationModal(self._selected_entry.scope.scope_id),
            self._handle_reservation_result,
        )

    def _filtered_reservations(self) -> list[DhcpReservation]:
        filt = self._filter.lower()
        if not filt:
            return self._reservations
        return [r for r in self._reservations
                if filt in r.ip_address.lower() or filt in r.client_id.lower()
                or filt in r.name.lower() or filt in (r.description or "").lower()]

    def action_edit_reservation(self) -> None:
        if self._view != VIEW_RESERVATIONS or not self._selected_entry:
            return
        table = self.query_one("#main_table", DataTable)
        row_idx = table.cursor_row
        shown = self._filtered_reservations()
        if row_idx is not None and 0 <= row_idx < len(shown):
            self.app.push_screen(
                ReservationModal(self._selected_entry.scope.scope_id, shown[row_idx]),
                self._handle_reservation_result,
            )

    def action_delete_reservation(self) -> None:
        if self._view != VIEW_RESERVATIONS or not self._selected_entry:
            return
        table = self.query_one("#main_table", DataTable)
        row_idx = table.cursor_row
        shown = self._filtered_reservations()
        if row_idx is not None and 0 <= row_idx < len(shown):
            self._do_delete_reservation(shown[row_idx])

    @work(thread=True)
    def _do_delete_reservation(self, res: DhcpReservation) -> None:
        app    = self.app
        mgr    = self._manager_for_selected()
        status = self.query_one("#status_bar", Static)
        if not mgr:
            return
        app.call_from_thread(status.update, f"Deleting {res.ip_address}…")
        try:
            mgr.delete_reservation(res.scope_id, res.ip_address)
            app.call_from_thread(self._load_reservations)
        except Exception as e:
            log.error("_do_delete_reservation: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(status.update, f"[red]Delete failed: {e}[/]")

    def action_promote_lease(self) -> None:
        if self._view != VIEW_LEASES or not self._selected_entry:
            return
        table = self.query_one("#main_table", DataTable)
        row_idx = table.cursor_row
        filt = self._filter.lower()
        shown = [l for l in self._leases
                 if not filt or filt in l.ip_address.lower()
                 or filt in l.client_id.lower() or filt in l.hostname.lower()]
        if row_idx is not None and 0 <= row_idx < len(shown):
            self._do_promote_lease(shown[row_idx])

    @work(thread=True)
    def _do_promote_lease(self, lease: DhcpLease) -> None:
        app    = self.app
        mgr    = self._manager_for_selected()
        status = self.query_one("#status_bar", Static)
        if not mgr:
            return
        app.call_from_thread(status.update, f"Promoting {lease.ip_address}…")
        try:
            mgr.convert_lease_to_reservation(lease.scope_id, lease.ip_address)
            app.call_from_thread(status.update, f"[green]{lease.ip_address} promoted[/]")
            app.call_from_thread(self._load_leases)
        except Exception as e:
            log.error("_do_promote_lease: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(status.update, f"[red]Promote failed: {e}[/]")

    def action_refresh(self) -> None:
        self._loaded       = False
        self._scope_entries = []
        self._leases       = []
        self._reservations = []
        self._selected_entry = None
        self._load_all_scopes()

    def action_search_all(self) -> None:
        """Open global search across all scopes."""
        if not self._scope_entries:
            self.query_one("#status_bar", Static).update(
                "[yellow]Load scopes first (R) before searching[/]"
            )
            return
        self.app.push_screen(
            DHCPSearchModal(self._managers, self._scope_entries),
            self._handle_search_result,
        )

    def _handle_search_result(self, result) -> None:
        """Navigate to the scope containing the selected search result."""
        if result is None:
            return
        target = next(
            (e for e in self._scope_entries
             if e.scope.scope_id == result.scope_id
             and (e.server or "local") == result.server),
            None,
        )
        if target is None:
            self.query_one("#status_bar", Static).update(
                f"[red]Scope {result.scope_id} not found[/]"
            )
            return
        self._selected_entry = target
        # Switch to the appropriate view for the result type
        if result.result_type == "reservation":
            self._view = "reservations"
            try:
                self.query_one("#view_select", Select).value = "reservations"
            except Exception:
                pass
            self._setup_columns("reservations")
            self._loading_data = False
            self._load_reservations()
        else:
            self._view = "leases"
            try:
                self.query_one("#view_select", Select).value = "leases"
            except Exception:
                pass
            self._setup_columns("leases")
            self._loading_data = False
            self._load_leases()
        # Highlight the scope in the list
        self._highlight_scope(target)

    def _highlight_scope(self, target_entry) -> None:
        """Scroll the scope list to the target scope."""
        try:
            lv = self.query_one("#scope_list", ListView)
            # Walk entries to find list index (accounting for server header rows)
            row = 0
            current_server = None
            for entry in self._scope_entries:
                if len(self._dhcp_servers) > 1 and entry.server != current_server:
                    current_server = entry.server
                    row += 1   # header row
                if entry is target_entry:
                    lv.index = row
                    return
                row += 1
        except Exception as e:
            log.debug("_highlight_scope: %s", e)

    @on(Button.Pressed, "#btn_new")
    def _btn_new(self) -> None:     self.action_new_reservation()
    @on(Button.Pressed, "#btn_edit")
    def _btn_edit(self) -> None:    self.action_edit_reservation()
    @on(Button.Pressed, "#btn_del")
    def _btn_del(self) -> None:     self.action_delete_reservation()
    @on(Button.Pressed, "#btn_promote")
    def _btn_promote(self) -> None: self.action_promote_lease()
    @on(Button.Pressed, "#btn_search")
    def _btn_search(self) -> None:  self.action_search_all()
    @on(Button.Pressed, "#btn_refresh")
    def _btn_refresh(self) -> None: self.action_refresh()

    def _handle_reservation_result(self, result: dict | None) -> None:
        if result:
            self._apply_reservation(result)

    @work(thread=True)
    def _apply_reservation(self, result: dict) -> None:
        app    = self.app
        mgr    = self._manager_for_selected()
        status = self.query_one("#status_bar", Static)
        if not mgr:
            return
        app.call_from_thread(status.update, f"Saving {result['ip']}…")
        try:
            if result["is_edit"]:
                mgr.update_reservation(
                    result["scope_id"], result["ip"],
                    result["name"], result["description"],
                )
            else:
                mgr.add_reservation(
                    result["scope_id"], result["ip"],     result["mac"],
                    result["name"],     result["description"], result["reservation_type"],
                )
            app.call_from_thread(status.update, f"[green]Saved {result['ip']}[/]")
            app.call_from_thread(self._load_reservations)
        except Exception as e:
            log.error("_apply_reservation: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(status.update, f"[red]Save failed: {e}[/]")
