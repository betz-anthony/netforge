"""Servers tab — manage saved server profiles and switch active connection."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Static

from netforge.config import (
    ServerProfile,
    delete_profile,
    list_profiles,
    set_default_profile,
)
from netforge.transport.winrm import WinRMSessionPool, WinRMError, AuthenticationError


class ServersPanel(Vertical):
    """Server profile list with connect / add / delete actions."""

    BINDINGS = [
        Binding("n",     "add_server",     "Add"),
        Binding("d",     "delete_server",  "Delete"),
        Binding("enter", "connect_server", "Connect"),
        Binding("s",     "set_default",    "Set Default"),
    ]

    DEFAULT_CSS = """
    ServersPanel { padding: 1 2; }
    ServersPanel #server_table { height: 1fr; margin-bottom: 1; }
    ServersPanel #action_bar { height: 3; align: left middle; }
    ServersPanel #action_bar Button { margin-right: 1; }
    ServersPanel #status_bar { height: 1; color: $text-muted; }
    ServersPanel .section_label {
        color: $text-muted; text-style: bold; margin-bottom: 1;
    }
    """

    def __init__(self, active_profile: ServerProfile | None = None, **kwargs):
        super().__init__(**kwargs)
        self._profiles: list[ServerProfile] = []
        self._active_profile = active_profile

    def compose(self) -> ComposeResult:
        yield Label("Saved Servers", classes="section_label")
        yield DataTable(id="server_table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="action_bar"):
            yield Button("N Add Server", id="btn_add",     variant="default")
            yield Button("Connect",      id="btn_connect", variant="primary")
            yield Button("S Set Default",id="btn_default", variant="default")
            yield Button("D Delete",     id="btn_delete",  variant="error")
        yield Static("", id="status_bar")

    def on_mount(self) -> None:
        table = self.query_one("#server_table", DataTable)
        table.add_columns("", "Name", "Host", "User", "Port", "SSL", "Transport", "DNS Servers", "DHCP Servers")
        self._refresh_table()

    def _refresh_table(self) -> None:
        self._profiles = list_profiles()
        table = self.query_one("#server_table", DataTable)
        table.clear()
        for p in self._profiles:
            active_marker = "[green]●[/]" if (
                self._active_profile and p.host == self._active_profile.host
            ) else " "
            dns_str  = ", ".join(p.dns_servers)  or f"[dim]{p.host}[/]"
            dhcp_str = ", ".join(p.dhcp_servers) or f"[dim]{p.host}[/]"
            transport_str = (
                "[cyan]Kerberos[/]" if p.transport == "kerberos" else "NTLM"
            )
            table.add_row(
                active_marker, p.name, p.host, p.username,
                str(p.port), "yes" if p.ssl else "no",
                transport_str, dns_str, dhcp_str,
            )
        self.query_one("#status_bar", Static).update(
            f"{len(self._profiles)} server(s) saved"
        )

    def _selected_profile(self) -> ServerProfile | None:
        table = self.query_one("#server_table", DataTable)
        idx = table.cursor_row
        if idx is not None and 0 <= idx < len(self._profiles):
            return self._profiles[idx]
        return None

    def action_add_server(self) -> None:
        self.app.action_add_server()

    def action_delete_server(self) -> None:
        profile = self._selected_profile()
        if profile:
            delete_profile(profile.name)
            self._refresh_table()
            self.query_one("#status_bar", Static).update(f"Deleted {profile.name}")

    def action_connect_server(self) -> None:
        profile = self._selected_profile()
        if profile:
            self.app._silent_connect(profile)

    def action_set_default(self) -> None:
        profile = self._selected_profile()
        if profile:
            set_default_profile(profile.name)
            self._refresh_table()
            self.query_one("#status_bar", Static).update(f"Default set to {profile.name}")

    @on(Button.Pressed, "#btn_add")
    def _btn_add(self) -> None:     self.action_add_server()
    @on(Button.Pressed, "#btn_connect")
    def _btn_connect(self) -> None: self.action_connect_server()
    @on(Button.Pressed, "#btn_default")
    def _btn_default(self) -> None: self.action_set_default()
    @on(Button.Pressed, "#btn_delete")
    def _btn_delete(self) -> None:  self.action_delete_server()

    @on(DataTable.RowSelected, "#server_table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_connect_server()
