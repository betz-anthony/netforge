"""Main Textual application — NetForge DNS/DHCP Manager."""

from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, ContentSwitcher, Footer, Header, Static

from netforge.config import ServerProfile, get_default_profile
from netforge.transport.winrm import (
    WinRMSessionPool, WinRMError, AuthenticationError, KerberosNotAvailable
)
from netforge.ui.dns_panel import DNSPanel
from netforge.ui.dhcp_panel import DHCPPanel
from netforge.ui.diag_modal import DiagModal
from netforge.ui.login_modal import LoginModal
from netforge.ui.servers_panel import ServersPanel

LOG_PATH = Path.home() / ".netforge.log"

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("netforge.app")


def _log_exc(label: str, exc: Exception) -> None:
    log.error("%s: %s", label, exc)
    log.error(traceback.format_exc())


class NetForgeApp(App):
    TITLE = "NetForge DNS/DHCP Manager"
    CSS = """
    Screen { layout: vertical; }

    #nav_bar {
        height: 3;
        layout: horizontal;
        background: $surface;
        border-bottom: solid $primary-darken-2;
        padding: 0 1;
        align: left middle;
    }
    #nav_bar Button { margin: 0 1 0 0; min-width: 14; }
    #nav_bar Button.-active-nav {
        background: $primary; color: $text; border: solid $primary;
    }
    #nav_bar #nav_spacer { width: 1fr; }

    #content { height: 1fr; }

    #connection_status {
        dock: bottom; height: 1; padding: 0 1;
        background: $surface-darken-1; color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("f1",     "show_panel('dns')",     "F1 DNS",      show=True),
        Binding("f2",     "show_panel('dhcp')",    "F2 DHCP",     show=True),
        Binding("f3",     "show_panel('servers')", "F3 Servers",  show=True),
        Binding("ctrl+d", "show_diag",             "Diagnostics", show=True),
        Binding("ctrl+l", "reconnect",             "Reconnect",   show=False),
        Binding("q,f10",  "quit",                  "Quit",        show=True),
    ]

    def __init__(self, cli_host=None, cli_user=None, cli_port=5985, cli_ssl=False):
        super().__init__()
        self._cli_host = cli_host
        self._cli_user = cli_user
        self._cli_port = cli_port
        self._cli_ssl  = cli_ssl
        self._pool:           WinRMSessionPool | None = None
        self._active_profile: ServerProfile    | None = None
        self._dns_panel:      DNSPanel         | None = None
        self._dhcp_panel:     DHCPPanel        | None = None
        self._current_panel = "servers"
        log.info("NetForgeApp initialised  log=%s", LOG_PATH)

    def on_exception(self, error: Exception) -> None:
        _log_exc("Textual on_exception", error)

    # ---- Compose — NO placeholder panels, only servers ----

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="nav_bar"):
            yield Button("F1 DNS",     id="btn_dns",     variant="default")
            yield Button("F2 DHCP",    id="btn_dhcp",    variant="default")
            yield Button("F3 Servers", id="btn_servers", variant="default")
            yield Static("", id="nav_spacer")
        with ContentSwitcher(initial="panel_servers", id="content"):
            yield ServersPanel(id="panel_servers")
        yield Static("Not connected", id="connection_status")
        yield Footer()

    def on_mount(self) -> None:
        self._set_active_nav("servers")
        try:
            if self._cli_host:
                self.push_screen(LoginModal(
                    prefill_host=self._cli_host,
                    prefill_user=self._cli_user or "",
                    prefill_port=self._cli_port,
                    prefill_ssl=self._cli_ssl,
                ), self._handle_login)
            else:
                default = get_default_profile()
                if default and default.password:
                    self._silent_connect(default)
                else:
                    self.call_after_refresh(self._show_login)
        except Exception as e:
            _log_exc("on_mount", e)

    def _set_active_nav(self, panel: str) -> None:
        for bid in ("btn_dns", "btn_dhcp", "btn_servers"):
            try:
                btn = self.query_one(f"#{bid}", Button)
                btn.add_class("-active-nav") if bid == f"btn_{panel}" \
                    else btn.remove_class("-active-nav")
            except Exception:
                pass

    # ---- Nav buttons ----

    @on(Button.Pressed, "#btn_dns")
    def _nav_dns(self) -> None:     self.action_show_panel("dns")

    @on(Button.Pressed, "#btn_dhcp")
    def _nav_dhcp(self) -> None:    self.action_show_panel("dhcp")

    @on(Button.Pressed, "#btn_servers")
    def _nav_servers(self) -> None: self.action_show_panel("servers")

    def action_show_panel(self, panel: str) -> None:
        try:
            self._current_panel = panel
            panel_id = f"panel_{panel}"
            # Guard: panel may not be mounted yet
            try:
                self.query_one(f"#{panel_id}")
            except Exception:
                log.debug("action_show_panel: #%s not in tree, ignoring", panel_id)
                return
            # Explicitly hide all dynamic panels, show only the requested one
            for pid, p in (("panel_dns", self._dns_panel),
                           ("panel_dhcp", self._dhcp_panel)):
                if p is not None:
                    p.display = (pid == panel_id)
            # servers panel visibility is managed by ContentSwitcher natively
            self.query_one("#content", ContentSwitcher).current = panel_id
            self._set_active_nav(panel)
            self.call_after_refresh(self._trigger_panel_load, panel)
        except Exception as e:
            _log_exc("action_show_panel", e)

    def _trigger_panel_load(self, panel: str) -> None:
        try:
            if panel == "dns"  and self._dns_panel  is not None:
                self._dns_panel.load()
            elif panel == "dhcp" and self._dhcp_panel is not None:
                self._dhcp_panel.load()
        except Exception as e:
            _log_exc("_trigger_panel_load", e)

    # ---- Login ----

    def _show_login(self) -> None:
        self.push_screen(LoginModal(), self._handle_login)

    def _handle_login(self, profile: ServerProfile | None) -> None:
        if profile:
            self._silent_connect(profile)

    # ---- Silent connect (normal path — no modal) ----

    @work(thread=True)
    def _silent_connect(self, profile: ServerProfile) -> None:
        log.info("_silent_connect  host=%s  transport=%s", profile.host, profile.transport)
        app    = self.app
        status = self.query_one("#connection_status", Static)

        try:
            app.call_from_thread(status.update, f"Connecting to {profile.host}…")

            pool = WinRMSessionPool(profile)
            pool.probe()
            log.info("probe OK  host=%s", profile.host)

            for server in profile.effective_dns_servers:
                try:
                    pool.session_for(server)
                except Exception as e:
                    log.warning("DNS pre-connect %s: %s", server or profile.host, e)

            for server in profile.effective_dhcp_servers:
                try:
                    pool.session_for(server)
                except Exception as e:
                    log.warning("DHCP pre-connect %s: %s", server or profile.host, e)

            log.info("_silent_connect done, calling _apply_connection on UI thread")
            app.call_from_thread(app._apply_connection, pool, profile)

        except KerberosNotAvailable as e:
            _log_exc("KerberosNotAvailable", e)
            app.call_from_thread(status.update,
                "[red]Kerberos not available — pip install netforge[kerberos][/]")
            app.call_from_thread(app.notify,
                "Kerberos not installed", severity="error", timeout=8)
        except AuthenticationError as e:
            _log_exc("AuthenticationError", e)
            app.call_from_thread(status.update, f"[red]Auth failed: {e}[/]")
            app.call_from_thread(app.notify,
                f"Auth failed: {e}", severity="error", timeout=8)
        except WinRMError as e:
            _log_exc("WinRMError", e)
            app.call_from_thread(status.update, f"[red]Connection failed: {e}[/]")
            app.call_from_thread(app.notify,
                f"WinRM error: {e}", severity="error", timeout=8)
        except Exception as e:
            _log_exc("Unexpected", e)
            app.call_from_thread(status.update, f"[red]Error: {e}[/]")
            app.call_from_thread(app.notify,
                f"Error: {e}", severity="error", timeout=8)

    # ---- Diagnostic connect (Ctrl+D — shows DiagModal) ----

    @work(thread=True)
    def _diag_connect(self, profile: ServerProfile) -> None:
        log.info("_diag_connect  host=%s", profile.host)
        app  = self.app
        diag = DiagModal(profile.host)
        app.call_from_thread(app.push_screen, diag)
        time.sleep(0.15)
        status = self.query_one("#connection_status", Static)

        try:
            app.call_from_thread(status.update, f"Connecting to {profile.host}…")
            app.call_from_thread(diag.log_step, f"URL:       {profile.url}")
            app.call_from_thread(diag.log_step, f"Transport: {profile.transport.upper()}  Port: {profile.port}")
            app.call_from_thread(diag.log_step, f"User:      {profile.username}")
            app.call_from_thread(diag.log_step, f"DNS:       {profile.effective_dns_servers}")
            app.call_from_thread(diag.log_step, f"DHCP:      {profile.effective_dhcp_servers}")

            pool = WinRMSessionPool(profile)
            app.call_from_thread(diag.log_step, f"Probing {profile.host}…")
            pool.probe()
            app.call_from_thread(diag.log_ok, f"{profile.host} OK")

            for server in profile.effective_dns_servers:
                target = server or profile.host
                app.call_from_thread(diag.log_step, f"DNS: {target}…")
                try:
                    pool.session_for(server)
                    app.call_from_thread(diag.log_ok, f"DNS {target} OK")
                except Exception as e:
                    app.call_from_thread(diag.log_error, f"DNS {target} failed", e)

            for server in profile.effective_dhcp_servers:
                target = server or profile.host
                app.call_from_thread(diag.log_step, f"DHCP: {target}…")
                try:
                    pool.session_for(server)
                    app.call_from_thread(diag.log_ok, f"DHCP {target} OK")
                except Exception as e:
                    app.call_from_thread(diag.log_error, f"DHCP {target} failed", e)

            app.call_from_thread(diag.log_ok, "All connections OK — click Close to continue")
            diag._result_pool    = pool
            diag._result_profile = profile
            app.call_from_thread(diag.show_close_button)

        except KerberosNotAvailable as e:
            _log_exc("KerberosNotAvailable", e)
            app.call_from_thread(diag.log_error, "Kerberos not installed.", e)
            app.call_from_thread(status.update, "[red]Kerberos not available[/]")
        except AuthenticationError as e:
            _log_exc("AuthenticationError", e)
            app.call_from_thread(diag.log_error, "Authentication failed.", e)
            app.call_from_thread(status.update, f"[red]Auth failed: {e}[/]")
        except WinRMError as e:
            _log_exc("WinRMError", e)
            app.call_from_thread(diag.log_error, "WinRM error.", e)
            app.call_from_thread(status.update, f"[red]Connection failed: {e}[/]")
        except Exception as e:
            _log_exc("Unexpected", e)
            app.call_from_thread(diag.log_error, f"Unexpected: {type(e).__name__}", e)
            app.call_from_thread(status.update, f"[red]Error: {e}[/]")

    def on_diag_dismissed(self, pool: WinRMSessionPool,
                          profile: ServerProfile) -> None:
        """Called by DiagModal with explicit pool+profile after Close clicked."""
        self._apply_connection(pool, profile)

    # ---- Apply connection — always on UI thread ----

    def _apply_connection(self, pool: WinRMSessionPool,
                          profile: ServerProfile) -> None:
        """Remove old panels, mount new ones, switch to DNS. UI thread only."""
        log.info("_apply_connection  host=%s", profile.host)
        try:
            if self._pool:
                try:
                    self._pool.disconnect_all()
                except Exception:
                    pass
            self._pool = pool
            self._active_profile = profile

            switcher = self.query_one("#content", ContentSwitcher)

            # Remove old dns/dhcp panels if present
            for pid in ("panel_dns", "panel_dhcp"):
                try:
                    old = self.query_one(f"#{pid}")
                    old.remove()
                except Exception:
                    pass   # not present — fine

            # Build fresh panels
            self._dns_panel = DNSPanel(
                pool,
                dns_servers=profile.effective_dns_servers,
                id="panel_dns",
            )
            self._dhcp_panel = DHCPPanel(
                pool,
                dhcp_servers=profile.effective_dhcp_servers,
                id="panel_dhcp",
            )

            # Mount before the servers panel
            servers = switcher.query_one("#panel_servers")
            switcher.mount(self._dns_panel,  before=servers)
            switcher.mount(self._dhcp_panel, before=servers)

            # ContentSwitcher may not auto-hide dynamically mounted children —
            # explicitly hide both until we switch to them
            self._dns_panel.display  = False
            self._dhcp_panel.display = False

            # Update status bar
            dns_list  = profile.effective_dns_servers
            dhcp_list = profile.effective_dhcp_servers
            dns_str   = ", ".join(s or "local" for s in dns_list)
            dhcp_str  = ", ".join(s or "local" for s in dhcp_list)
            targets   = (f"DNS+DHCP: {dns_str}"
                         if dns_list == dhcp_list
                         else f"DNS: {dns_str}  DHCP: {dhcp_str}")
            self.query_one("#connection_status", Static).update(
                f"[green]●[/]  {profile.username}@{profile.host}:{profile.port}"
                f"  {profile.transport.upper()}"
                f"  {'HTTPS' if profile.ssl else 'HTTP'}"
                f"  {targets}"
            )

            log.debug("_apply_connection: panels mounted, scheduling _activate_dns")
            # Two refresh cycles: first lets mount complete, second triggers load
            self.call_after_refresh(self._activate_dns)

        except Exception as e:
            _log_exc("_apply_connection", e)

    def _activate_dns(self) -> None:
        """Switch ContentSwitcher to DNS panel and trigger zone load."""
        try:
            switcher = self.query_one("#content", ContentSwitcher)
            # Explicitly manage visibility — ContentSwitcher may not hide
            # dynamically mounted children automatically
            if self._dhcp_panel is not None:
                self._dhcp_panel.display = False
            if self._dns_panel is not None:
                self._dns_panel.display = True
            switcher.current = "panel_dns"
            self._set_active_nav("dns")
            self._current_panel = "dns"
            log.debug("_activate_dns: ContentSwitcher.current = panel_dns")
            self.call_after_refresh(self._do_dns_load)
        except Exception as e:
            _log_exc("_activate_dns", e)

    def _do_dns_load(self) -> None:
        try:
            if self._dns_panel is not None:
                log.debug("_do_dns_load: calling dns_panel.load()")
                self._dns_panel.load()
            else:
                log.warning("_do_dns_load: dns_panel is None")
        except Exception as e:
            _log_exc("_do_dns_load", e)

    # ---- Actions ----

    def action_show_diag(self) -> None:
        if self._active_profile:
            self._diag_connect(self._active_profile)
        else:
            self.notify("No active profile — connect first (F3)", severity="warning")

    def action_reconnect(self) -> None:
        if self._active_profile:
            self._silent_connect(self._active_profile)
        else:
            self._show_login()

    def switch_session(self, pool: WinRMSessionPool,
                       profile: ServerProfile) -> None:
        """Called by ServersPanel Connect button."""
        self._apply_connection(pool, profile)

    def action_add_server(self) -> None:
        self.push_screen(LoginModal(), self._handle_login)
