"""Login / server-connect modal — supports NTLM and Kerberos transports."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from netforge.config import ServerProfile, _parse_servers, list_profiles, save_profile
from netforge.transport.winrm import TRANSPORT_KERBEROS, TRANSPORT_NTLM

TRANSPORT_OPTIONS = [
    ("NTLM  (password, works everywhere)", TRANSPORT_NTLM),
    ("Kerberos  (kinit / keytab, no password sent)", TRANSPORT_KERBEROS),
]


class LoginModal(ModalScreen[ServerProfile | None]):
    """Collect credentials and return a ServerProfile on success."""

    DEFAULT_CSS = """
    LoginModal { align: center middle; }
    LoginModal > Vertical {
        width: 70;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    LoginModal .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    LoginModal .hint {
        color: $text-muted;
        margin-top: 0;
        padding-left: 1;
    }
    LoginModal .krb-hint {
        color: $warning;
        margin-top: 0;
        padding-left: 1;
    }
    LoginModal Label  { margin-top: 1; color: $text-muted; }
    LoginModal Input  { margin-top: 0; }
    LoginModal Select { margin-top: 0; }
    LoginModal .error { color: $error; margin-top: 1; }
    LoginModal Horizontal { margin-top: 1; height: auto; align: center middle; }
    LoginModal Button { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        prefill_host:      str  = "",
        prefill_user:      str  = "",
        prefill_port:      int  = 5985,
        prefill_ssl:       bool = False,
        prefill_dns:       str  = "",
        prefill_dhcp:      str  = "",
        prefill_transport: str  = TRANSPORT_NTLM,
    ):
        super().__init__()
        self._prefill_host      = prefill_host
        self._prefill_user      = prefill_user
        self._prefill_port      = prefill_port
        self._prefill_ssl       = prefill_ssl
        self._prefill_dns       = prefill_dns
        self._prefill_dhcp      = prefill_dhcp
        self._prefill_transport = prefill_transport

    def compose(self) -> ComposeResult:
        saved = list_profiles()
        profile_opts = [(p.name, p.name) for p in saved]

        with Vertical():
            yield Static("Connect to Server", classes="title")

            if profile_opts:
                yield Label("Saved profile")
                yield Select(
                    profile_opts + [("— new —", "__new__")],
                    id="profile_select",
                    value=profile_opts[0][1] if profile_opts else "__new__",
                )

            yield Label("Transport")
            yield Select(TRANSPORT_OPTIONS, id="transport_select",
                         value=self._prefill_transport)
            yield Static(
                "NTLM: enter username + password below.",
                id="transport_hint", classes="hint",
            )

            yield Label("Host / IP  (primary — used for credential probe)")
            yield Input(value=self._prefill_host,
                        placeholder="mgmt-server.example.com", id="host")

            yield Label("Username", id="lbl_username")
            yield Input(value=self._prefill_user,
                        placeholder=r"DOMAIN\username  or  user@REALM.LOCAL",
                        id="username")

            yield Label("Password", id="lbl_password")
            yield Input(password=True, placeholder="••••••••", id="password")

            yield Label("Port")
            yield Input(value=str(self._prefill_port), placeholder="5985", id="port")

            yield Label("Protocol")
            yield Select(
                [("HTTP (port 5985)", "http"), ("HTTPS (port 5986)", "https")],
                id="ssl_select",
                value="https" if self._prefill_ssl else "http",
            )

            yield Label("DNS servers  (blank = connect directly to host)")
            yield Input(
                value=self._prefill_dns,
                placeholder="dc01.example.com, dc02.example.com",
                id="dns_servers",
            )
            yield Static(
                "Comma-separated. Each gets its own direct WinRM connection.",
                classes="hint",
            )

            yield Label("DHCP servers  (blank = connect directly to host)")
            yield Input(
                value=self._prefill_dhcp,
                placeholder="dhcp01.example.com, dhcp02.example.com",
                id="dhcp_servers",
            )
            yield Static(
                "Comma-separated. Multiple servers queried for scopes/leases.",
                classes="hint",
            )

            yield Label("Save as profile name  (optional)")
            yield Input(placeholder="e.g. prod", id="profile_name")

            yield Static("", id="error_msg", classes="error")

            with Horizontal():
                yield Button("Connect", variant="primary", id="btn_connect")
                yield Button("Cancel",  variant="default", id="btn_cancel")

    # ---- Transport selector ----

    @on(Select.Changed, "#transport_select")
    def _transport_changed(self, event: Select.Changed) -> None:
        is_krb = event.value == TRANSPORT_KERBEROS
        hint   = self.query_one("#transport_hint", Static)
        pw     = self.query_one("#password",       Input)
        lbl_pw = self.query_one("#lbl_password",   Label)

        if is_krb:
            hint.update(
                "Kerberos: password field ignored — uses OS ticket cache (kinit).\n"
                "Username format: user@REALM.LOCAL  or  DOMAIN\\user"
            )
            hint.add_class("krb-hint")
            hint.remove_class("hint")
            pw.placeholder = "(not used — Kerberos uses kinit / keytab)"
            pw.disabled = True
        else:
            hint.update("NTLM: enter username + password below.")
            hint.remove_class("krb-hint")
            hint.add_class("hint")
            pw.placeholder = "••••••••"
            pw.disabled = False

    # ---- Profile loader ----

    @on(Select.Changed, "#profile_select")
    def _load_profile(self, event: Select.Changed) -> None:
        if event.value == "__new__":
            return
        profiles = {p.name: p for p in list_profiles()}
        if event.value not in profiles:
            return
        p = profiles[event.value]
        self.query_one("#host",             Input).value  = p.host
        self.query_one("#username",         Input).value  = p.username
        self.query_one("#port",             Input).value  = str(p.port)
        self.query_one("#ssl_select",       Select).value = "https" if p.ssl else "http"
        self.query_one("#transport_select", Select).value = p.transport
        self.query_one("#dns_servers",      Input).value  = ", ".join(p.dns_servers)
        self.query_one("#dhcp_servers",     Input).value  = ", ".join(p.dhcp_servers)
        if p.password and p.transport == TRANSPORT_NTLM:
            self.query_one("#password", Input).value = p.password

    # ---- Connect ----

    @on(Button.Pressed, "#btn_connect")
    def _connect(self) -> None:
        self._do_connect()

    @on(Input.Submitted)
    def _submitted(self) -> None:
        self._do_connect()

    def _do_connect(self) -> None:
        transport    = self.query_one("#transport_select", Select).value
        host         = self.query_one("#host",             Input).value.strip()
        username     = self.query_one("#username",         Input).value.strip()
        password     = self.query_one("#password",         Input).value
        port_str     = self.query_one("#port",             Input).value.strip()
        ssl          = self.query_one("#ssl_select",       Select).value == "https"
        dns_raw      = self.query_one("#dns_servers",      Input).value.strip()
        dhcp_raw     = self.query_one("#dhcp_servers",     Input).value.strip()
        profile_name = self.query_one("#profile_name",     Input).value.strip()
        error        = self.query_one("#error_msg",        Static)

        if not host:
            error.update("Host is required"); return
        if not username:
            error.update("Username is required"); return
        if transport == TRANSPORT_NTLM and not password:
            error.update("Password is required for NTLM"); return

        try:
            port = int(port_str) if port_str else (5986 if ssl else 5985)
        except ValueError:
            error.update("Port must be a number"); return

        profile = ServerProfile(
            name=profile_name or host,
            host=host,
            username=username,
            password=password if transport == TRANSPORT_NTLM else "",
            port=port,
            ssl=ssl,
            transport=transport,
            dns_servers=_parse_servers(dns_raw),
            dhcp_servers=_parse_servers(dhcp_raw),
        )

        if profile_name:
            save_profile(profile)

        self.dismiss(profile)

    @on(Button.Pressed, "#btn_cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
