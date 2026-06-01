"""DNS tab — sorted grouped zone tree + record table."""

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
    Button, DataTable, Input, Label, ListItem, ListView, Static,
)

from netforge.modules.dns import DNSManager, DnsRecord, DnsZone
from netforge.transport.winrm import WinRMSessionPool
from netforge.ui.record_modal import RecordModal
from netforge.ui.dns_search_modal import DNSSearchModal

log = logging.getLogger("netforge.dns_panel")

# Internal AD zones to hide by default
_HIDDEN_PREFIXES = (
    "_msdcs.", "_sites.", "_tcp.", "_udp.",
    "trustanchors", "..tld",
)


@dataclass
class ZoneEntry:
    server: str
    zone: DnsZone

    @property
    def display_server(self) -> str:
        return self.server or "local"

    @property
    def is_hidden(self) -> bool:
        return any(self.zone.name.lower().startswith(p) for p in _HIDDEN_PREFIXES)


class DNSPanel(Vertical):
    """DNS management — grouped sorted zone tree, single server load at a time."""

    BINDINGS = [
        Binding("n", "new_record",    "New"),
        Binding("e", "edit_record",   "Edit"),
        Binding("d", "delete_record", "Delete"),
        Binding("r", "refresh",       "Refresh"),
        Binding("h", "toggle_hidden", "Show/Hide Internal"),
        Binding("s", "search_all",    "Search All Zones"),
    ]

    DEFAULT_CSS = """
    DNSPanel { layout: horizontal; }
    DNSPanel #zone_pane {
        width: 36;
        border-right: solid $primary-darken-2;
    }
    DNSPanel #zone_pane Label {
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }
    DNSPanel #zone_list { height: 1fr; }
    DNSPanel #record_pane { width: 1fr; }
    DNSPanel #filter_bar {
        height: 3;
        align: left middle;
        padding: 0 1;
        border-bottom: solid $primary-darken-2;
    }
    DNSPanel #filter_bar Label { margin-right: 1; color: $text-muted; }
    DNSPanel #filter_input { width: 22; }
    DNSPanel #record_table { height: 1fr; }
    DNSPanel #status_bar {
        height: 1; padding: 0 1;
        color: $text-muted; background: $surface-darken-1;
    }
    """

    def __init__(self, pool: WinRMSessionPool, dns_servers: list[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._pool = pool
        self._dns_servers: list[str] = dns_servers or [""]
        self._managers: dict[str, DNSManager] = {
            s: DNSManager(pool, server=s) for s in self._dns_servers
        }
        self._zone_entries:    list[ZoneEntry] = []
        self._visible_entries: list[ZoneEntry] = []   # after filtering/hiding
        self._records:         list[DnsRecord] = []
        self._selected_entry:  ZoneEntry | None = None
        self._filter         = ""
        self._show_hidden    = False
        self._loaded         = False
        self._loading_records = False

    def compose(self) -> ComposeResult:
        with Vertical(id="zone_pane"):
            yield Label("DNS Zones")
            yield ListView(id="zone_list")
        with Vertical(id="record_pane"):
            with Horizontal(id="filter_bar"):
                yield Label("Filter:")
                yield Input(placeholder="name or data…", id="filter_input")
                yield Button("N New",       id="btn_new",     variant="default")
                yield Button("E Edit",      id="btn_edit",    variant="default")
                yield Button("D Del",       id="btn_del",     variant="error")
                yield Button("S Search",    id="btn_search",  variant="default")
                yield Button("R Refresh",   id="btn_refresh", variant="default")
            yield DataTable(id="record_table", cursor_type="row", zebra_stripes=True)
            yield Static("Select a zone to view records", id="status_bar")

    def on_mount(self) -> None:
        table = self.query_one("#record_table", DataTable)
        table.add_columns("Name", "Type", "TTL", "Data")

    def load(self) -> None:
        """Called when tab first becomes visible. Guards against premature calls."""
        if self._parent is None:
            log.warning("DNSPanel.load() called before panel mounted — skipping")
            return
        if not self._loaded:
            self._loaded = True
            self._load_all_zones()

    # ---- Zone loading ----

    @work(thread=True)
    def _load_all_zones(self) -> None:
        app = self.app   # capture before entering thread
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update, "Loading zones…")
        all_entries: list[ZoneEntry] = []
        for server in self._dns_servers:
            label = server or "local"
            try:
                log.debug("_load_all_zones  server=%s", label)
                zones = self._managers[server].list_zones()
                log.debug("  got %d zones from %s", len(zones), label)
                for z in zones:
                    all_entries.append(ZoneEntry(server=server, zone=z))
            except Exception as e:
                log.error("_load_all_zones failed for %s: %s\n%s",
                          label, e, traceback.format_exc())
                app.call_from_thread(status.update,
                    f"[red]Error loading zones from {label}: {e}[/]")
        self._zone_entries = all_entries
        app.call_from_thread(self._populate_zones)

    def _sorted_zones(self, entries: list[ZoneEntry]) -> list[tuple[str, str, list[ZoneEntry]]]:
        """
        Return zones grouped and sorted for display.
        Returns list of (group_label, server_label, [ZoneEntry]) tuples.
        Groups per server: Forward Lookup Zones, Reverse Lookup Zones,
                           Trust Points, Conditional Forwarders
        """
        result = []
        servers = list(dict.fromkeys(e.server for e in entries))
        for server in servers:
            server_entries = [e for e in entries if e.server == server]
            label = server or "local"

            forward = sorted(
                [e for e in server_entries
                 if not e.zone.is_reverse and e.zone.zone_type not in ("Forwarder", "Stub")
                 and not e.zone.name.lower().startswith("trustanchors")],
                key=lambda e: e.zone.name.lower()
            )
            reverse = sorted(
                [e for e in server_entries if e.zone.is_reverse],
                key=lambda e: e.zone.name.lower()
            )
            forwarders = sorted(
                [e for e in server_entries if e.zone.zone_type == "Forwarder"],
                key=lambda e: e.zone.name.lower()
            )
            trust = sorted(
                [e for e in server_entries
                 if e.zone.name.lower().startswith("trustanchors")],
                key=lambda e: e.zone.name.lower()
            )

            if forward:
                result.append((f"  Forward Lookup Zones", label, forward))
            if reverse:
                result.append((f"  Reverse Lookup Zones", label, reverse))
            if trust:
                result.append((f"  Trust Points", label, trust))
            if forwarders:
                result.append((f"  Conditional Forwarders", label, forwarders))

        return result

    def _populate_zones(self) -> None:
        try:
            entries = (
                self._zone_entries if self._show_hidden
                else [e for e in self._zone_entries if not e.is_hidden]
            )

            groups = self._sorted_zones(entries)

            lv = self.query_one("#zone_list", ListView)
            lv.clear()
            self._visible_entries = []
            # Maps list-row index → ZoneEntry or None (None = header row)
            self._row_map: list[ZoneEntry | None] = []

            multi_server = len(self._dns_servers) > 1
            current_server = None

            for group_label, server_label, group_entries in groups:
                if multi_server and server_label != current_server:
                    current_server = server_label
                    lv.append(ListItem(Label(f"▶ {server_label}", markup=False)))
                    self._row_map.append(None)

                lv.append(ListItem(Label(group_label, markup=False)))
                self._row_map.append(None)

                for entry in group_entries:
                    lv.append(ListItem(Label(f"    {entry.zone.name}", markup=False)))
                    self._row_map.append(entry)
                    self._visible_entries.append(entry)

            total_fwd  = sum(1 for e in entries if not e.zone.is_reverse)
            total_rev  = sum(1 for e in entries if e.zone.is_reverse)
            hidden_cnt = sum(1 for e in self._zone_entries if e.is_hidden)
            status = f"{total_fwd} forward, {total_rev} reverse"
            if hidden_cnt and not self._show_hidden:
                status += f"  (H to show {hidden_cnt} internal)"
            self.query_one("#status_bar", Static).update(status)

            # Auto-select first forward zone
            if self._visible_entries:
                first = next(
                    (e for e in self._visible_entries if not e.zone.is_reverse),
                    self._visible_entries[0]
                )
                self._selected_entry = first
                self._load_records(first)

        except Exception as e:
            log.error("_populate_zones crashed: %s\n%s", e, traceback.format_exc())

    @on(ListView.Selected, "#zone_list")
    def _zone_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None:
            return
        row_map = getattr(self, "_row_map", [])
        if idx >= len(row_map):
            return
        entry = row_map[idx]
        if entry is not None:
            self._selected_entry = entry
            self._load_records(entry)

    # ---- Record loading ----

    @work(thread=True)
    def _load_records(self, entry: ZoneEntry) -> None:
        if self._loading_records:
            return
        self._loading_records = True
        app   = self.app
        zone  = entry.zone.name
        label = entry.display_server
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update, f"Loading {zone}…")
        try:
            log.debug("_load_records  zone=%s  server=%s", zone, label)
            self._records = self._managers[entry.server].list_records(zone)
            log.debug("  got %d records", len(self._records))
            app.call_from_thread(self._populate_records)
        except Exception as e:
            log.error("_load_records failed: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(status.update, f"[red]Error: {e}[/]")
        finally:
            self._loading_records = False

    def _populate_records(self) -> None:
        try:
            table = self.query_one("#record_table", DataTable)
            table.clear()
            filt = self._filter.lower()
            shown = sorted(
                [r for r in self._records
                 if not filt or filt in r.name.lower() or filt in r.data.lower()],
                key=lambda r: (r.record_type, r.name.lower())
            )
            type_styles = {"A": "green", "AAAA": "cyan", "CNAME": "magenta", "PTR": "yellow"}
            for r in shown:
                style = type_styles.get(r.record_type, "")
                table.add_row(
                    Text(r.name),
                    Text(r.record_type, style=style),
                    Text(r.display_ttl),
                    Text(r.data),
                )
            entry = self._selected_entry
            zone_label   = f" — {entry.zone.name}"       if entry else ""
            server_label = f"  [{entry.display_server}]" if entry else ""
            self.query_one("#status_bar", Static).update(
                f"{len(shown)} records{zone_label}{server_label}"
                + (f"  filter: {self._filter}" if self._filter else "")
            )
        except Exception as e:
            log.error("_populate_records crashed: %s\n%s", e, traceback.format_exc())

    @on(Input.Changed, "#filter_input")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._filter = event.value
        self._populate_records()

    # ---- Actions ----

    def action_toggle_hidden(self) -> None:
        self._show_hidden = not self._show_hidden
        self._populate_zones()

    def action_search_all(self) -> None:
        """Open global search across all zones."""
        if not self._zone_entries:
            self.query_one("#status_bar", Static).update(
                "[yellow]Load zones first (R) before searching[/]"
            )
            return
        self.app.push_screen(
            DNSSearchModal(self._managers, self._zone_entries),
            self._handle_search_result,
        )

    def _handle_search_result(self, result) -> None:
        """Navigate to the zone containing the selected search result."""
        if result is None:
            return
        # Find the ZoneEntry that matches the result's zone and server
        target = next(
            (e for e in self._zone_entries
             if e.zone.name == result.zone
             and (e.server or "local") == result.server),
            None,
        )
        if target is None:
            self.query_one("#status_bar", Static).update(
                f"[red]Zone {result.zone} not found in loaded zones[/]"
            )
            return
        # Navigate to the zone in the list and load its records
        self._selected_entry = target
        self._load_records(target)
        # Highlight the zone in the list view
        self._highlight_zone(target)
        self.query_one("#status_bar", Static).update(
            f"Navigated to {result.zone}  ·  looking for {result.name} ({result.record_type})"
        )

    def _highlight_zone(self, target_entry) -> None:
        """Scroll the zone list to the target zone and select it."""
        try:
            row_map = getattr(self, "_row_map", [])
            for idx, entry in enumerate(row_map):
                if entry is target_entry:
                    lv = self.query_one("#zone_list", ListView)
                    lv.index = idx
                    return
        except Exception as e:
            log.debug("_highlight_zone: %s", e)

    def action_new_record(self) -> None:
        if self._selected_entry:
            self.app.push_screen(
                RecordModal(self._selected_entry.zone.name),
                self._handle_record_result,
            )

    def action_edit_record(self) -> None:
        rec = self._selected_record()
        if rec and self._selected_entry:
            self.app.push_screen(
                RecordModal(self._selected_entry.zone.name, rec),
                self._handle_record_result,
            )

    def action_delete_record(self) -> None:
        rec = self._selected_record()
        if rec:
            self._delete_record(rec)

    def action_refresh(self) -> None:
        self._loaded = False
        self._zone_entries = []
        self._records = []
        self._selected_entry = None
        self._load_all_zones()

    def _selected_record(self) -> DnsRecord | None:
        table = self.query_one("#record_table", DataTable)
        row_idx = table.cursor_row
        if row_idx is None:
            return None
        filt = self._filter.lower()
        shown = sorted(
            [r for r in self._records
             if not filt or filt in r.name.lower() or filt in r.data.lower()],
            key=lambda r: (r.record_type, r.name.lower())
        )
        return shown[row_idx] if 0 <= row_idx < len(shown) else None

    def _manager_for_selected(self) -> DNSManager | None:
        if self._selected_entry is None:
            return None
        return self._managers[self._selected_entry.server]

    @work(thread=True)
    def _delete_record(self, record: DnsRecord) -> None:
        app = self.app
        mgr = self._manager_for_selected()
        if not mgr:
            return
        app.call_from_thread(
            self.query_one("#status_bar", Static).update, f"Deleting {record.name}…"
        )
        try:
            mgr.delete_record(record.zone, record.name, record.record_type, record.data)
            app.call_from_thread(self._load_records, self._selected_entry)
        except Exception as e:
            log.error("_delete_record: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(
                self.query_one("#status_bar", Static).update, f"[red]Delete failed: {e}[/]"
            )

    @on(Button.Pressed, "#btn_new")
    def _btn_new(self) -> None:     self.action_new_record()
    @on(Button.Pressed, "#btn_edit")
    def _btn_edit(self) -> None:    self.action_edit_record()
    @on(Button.Pressed, "#btn_del")
    def _btn_del(self) -> None:     self.action_delete_record()
    @on(Button.Pressed, "#btn_search")
    def _btn_search(self) -> None:  self.action_search_all()
    @on(Button.Pressed, "#btn_refresh")
    def _btn_refresh(self) -> None: self.action_refresh()

    def _handle_record_result(self, result: dict | None) -> None:
        if result:
            self._apply_record(result)

    @work(thread=True)
    def _apply_record(self, result: dict) -> None:
        app = self.app
        mgr = self._manager_for_selected()
        if not mgr:
            return
        zone  = result["zone"]
        name  = result["name"]
        rtype = result["record_type"]
        data  = result["data"]
        ttl   = result["ttl"]
        old   = result.get("old_record")
        status = self.query_one("#status_bar", Static)
        app.call_from_thread(status.update, f"Saving {name}…")
        try:
            if old is None:
                if rtype == "A":      mgr.add_a_record(zone, name, data, ttl)
                elif rtype == "AAAA": mgr.add_aaaa_record(zone, name, data, ttl)
                elif rtype == "CNAME":mgr.add_cname_record(zone, name, data, ttl)
                elif rtype == "PTR":  mgr.add_ptr_record(zone, name, data, ttl)
            else:
                if rtype == "A":      mgr.update_a_record(zone, name, old.data, data, ttl)
                elif rtype == "AAAA": mgr.update_aaaa_record(zone, name, old.data, data, ttl)
                elif rtype == "CNAME":mgr.update_cname_record(zone, name, old.data, data, ttl)
                elif rtype == "PTR":  mgr.update_ptr_record(zone, name, old.data, data, ttl)
            app.call_from_thread(status.update, f"[green]Saved {name} {rtype}[/]")
            app.call_from_thread(self._load_records, self._selected_entry)
        except Exception as e:
            log.error("_apply_record: %s\n%s", e, traceback.format_exc())
            app.call_from_thread(status.update, f"[red]Save failed: {e}[/]")
