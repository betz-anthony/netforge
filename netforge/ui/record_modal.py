"""Modal for creating and editing DNS records."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from netforge.modules.dns import DnsRecord

RECORD_TYPES = ["A", "AAAA", "CNAME", "PTR"]

TTL_PRESETS = [
    ("5 minutes", "300"),
    ("30 minutes", "1800"),
    ("1 hour", "3600"),
    ("6 hours", "21600"),
    ("1 day", "86400"),
    ("Custom", "__custom__"),
]


class RecordModal(ModalScreen[dict | None]):
    """Create or edit a DNS record. Returns a dict of field values or None on cancel."""

    DEFAULT_CSS = """
    RecordModal {
        align: center middle;
    }
    RecordModal > Vertical {
        width: 64;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    RecordModal .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    RecordModal Label {
        margin-top: 1;
        color: $text-muted;
    }
    RecordModal .error { color: $error; margin-top: 1; }
    RecordModal Horizontal { margin-top: 1; height: auto; align: center middle; }
    RecordModal Button { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, zone: str, record: DnsRecord | None = None):
        super().__init__()
        self.zone = zone
        self.record = record  # None = new record
        self._editing = record is not None

    def compose(self) -> ComposeResult:
        title = f"Edit Record — {self.zone}" if self._editing else f"New Record — {self.zone}"
        rtype = self.record.record_type if self.record else "A"
        name = self.record.name if self.record else ""
        data = self.record.data if self.record else ""
        ttl = str(self.record.ttl) if self.record else "3600"
        data_label = self._data_label(rtype)

        with Vertical():
            yield Static(title, classes="title")

            yield Label("Record type")
            yield Select(
                [(t, t) for t in RECORD_TYPES],
                id="rtype",
                value=rtype,
            )

            yield Label("Name")
            yield Input(value=name, placeholder="hostname or @ for zone root", id="name")

            yield Label(data_label, id="data_label")
            yield Input(value=data, placeholder=self._data_placeholder(rtype), id="data")

            yield Label("TTL")
            yield Select(
                TTL_PRESETS,
                id="ttl_select",
                value=ttl if ttl in [v for _, v in TTL_PRESETS] else "__custom__",
            )
            yield Input(
                value=ttl,
                placeholder="seconds",
                id="ttl_custom",
            )

            yield Static("", id="error_msg", classes="error")

            with Horizontal():
                yield Button("Save", variant="primary", id="btn_save")
                yield Button("Cancel", variant="default", id="btn_cancel")

    @staticmethod
    def _data_label(rtype: str) -> str:
        return {"A": "IPv4 address", "AAAA": "IPv6 address",
                "CNAME": "Target hostname", "PTR": "Pointer FQDN"}.get(rtype, "Data")

    @staticmethod
    def _data_placeholder(rtype: str) -> str:
        return {"A": "10.0.1.50", "AAAA": "2001:db8::1",
                "CNAME": "target.example.com.", "PTR": "host.example.com."}.get(rtype, "")

    @on(Select.Changed, "#rtype")
    def _rtype_changed(self, event: Select.Changed) -> None:
        self.query_one("#data_label", Label).update(self._data_label(event.value))
        self.query_one("#data", Input).placeholder = self._data_placeholder(event.value)

    @on(Select.Changed, "#ttl_select")
    def _ttl_preset(self, event: Select.Changed) -> None:
        if event.value != "__custom__":
            self.query_one("#ttl_custom", Input).value = event.value

    @on(Button.Pressed, "#btn_save")
    def _save(self) -> None:
        error = self.query_one("#error_msg", Static)
        rtype = self.query_one("#rtype", Select).value
        name = self.query_one("#name", Input).value.strip()
        data = self.query_one("#data", Input).value.strip()
        ttl_str = self.query_one("#ttl_custom", Input).value.strip()

        if not name:
            error.update("Name is required")
            return
        if not data:
            error.update("Data is required")
            return
        try:
            ttl = int(ttl_str)
            if ttl < 0:
                raise ValueError
        except ValueError:
            error.update("TTL must be a non-negative integer")
            return

        self.dismiss({
            "zone": self.zone,
            "name": name,
            "record_type": rtype,
            "data": data,
            "ttl": ttl,
            "old_record": self.record,
        })

    @on(Button.Pressed, "#btn_cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
