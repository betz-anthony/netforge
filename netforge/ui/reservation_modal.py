"""Modal for creating and editing DHCP reservations."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from netforge.modules.dhcp import DhcpReservation


class ReservationModal(ModalScreen[dict | None]):
    """Create or edit a DHCP reservation."""

    DEFAULT_CSS = """
    ReservationModal {
        align: center middle;
    }
    ReservationModal > Vertical {
        width: 64;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    ReservationModal .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    ReservationModal Label { margin-top: 1; color: $text-muted; }
    ReservationModal .error { color: $error; margin-top: 1; }
    ReservationModal Horizontal { margin-top: 1; height: auto; align: center middle; }
    ReservationModal Button { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, scope_id: str, reservation: DhcpReservation | None = None):
        super().__init__()
        self.scope_id = scope_id
        self.reservation = reservation
        self._editing = reservation is not None

    def compose(self) -> ComposeResult:
        title = f"Edit Reservation — {self.scope_id}" if self._editing \
            else f"New Reservation — {self.scope_id}"
        res = self.reservation

        with Vertical():
            yield Static(title, classes="title")

            yield Label("IP Address")
            yield Input(
                value=res.ip_address if res else "",
                placeholder="10.0.1.100",
                id="ip",
                disabled=self._editing,  # can't change IP on existing reservation
            )

            yield Label("MAC Address")
            yield Input(
                value=res.client_id if res else "",
                placeholder="aa:bb:cc:dd:ee:ff",
                id="mac",
                disabled=self._editing,  # MAC is identity, not editable
            )

            yield Label("Name / Hostname")
            yield Input(
                value=res.name if res else "",
                placeholder="workstation-01",
                id="name",
            )

            yield Label("Description (optional)")
            yield Input(
                value=res.description if res else "",
                placeholder="",
                id="description",
            )

            if not self._editing:
                yield Label("Reservation type")
                yield Select(
                    [("DHCP + BOOTP", "Both"), ("DHCP only", "Dhcp"), ("BOOTP only", "Bootp")],
                    id="res_type",
                    value="Both",
                )

            yield Static("", id="error_msg", classes="error")

            with Horizontal():
                yield Button("Save", variant="primary", id="btn_save")
                yield Button("Cancel", variant="default", id="btn_cancel")

    @on(Button.Pressed, "#btn_save")
    def _save(self) -> None:
        error = self.query_one("#error_msg", Static)
        ip = self.query_one("#ip", Input).value.strip()
        name = self.query_one("#name", Input).value.strip()
        description = self.query_one("#description", Input).value.strip()

        if not self._editing:
            mac = self.query_one("#mac", Input).value.strip()
            res_type = self.query_one("#res_type", Select).value
        else:
            mac = self.reservation.client_id
            res_type = self.reservation.reservation_type

        if not ip:
            error.update("IP address is required")
            return
        if not mac and not self._editing:
            error.update("MAC address is required")
            return
        if not name:
            error.update("Name is required")
            return

        self.dismiss({
            "scope_id": self.scope_id,
            "ip": ip,
            "mac": mac,
            "name": name,
            "description": description,
            "reservation_type": res_type,
            "is_edit": self._editing,
            "old_reservation": self.reservation,
        })

    @on(Button.Pressed, "#btn_cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
