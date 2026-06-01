"""
DHCP management via PowerShell DhcpServer cmdlets.
Supports: viewing leases, managing reservations.

Each DHCPManager targets ONE specific DHCP server via a direct WinRMSession
from the pool — no -ComputerName forwarding, no double-hop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from netforge.transport.winrm import WinRMSessionPool, WinRMError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ip_str(value: object) -> str:
    """
    Coerce an IP value from JSON to a plain dotted-decimal string.

    PowerShell's ConvertTo-Json serialises System.Net.IPAddress as a struct:
      {"Address": 2570, "AddressFamily": 2, ...}
    We convert the Address integer to dotted-decimal.
    Plain strings and None are passed through unchanged.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        addr_int = value.get("Address")
        if addr_int is not None:
            # IPAddress stores IPv4 as a little-endian 32-bit int
            import socket, struct
            try:
                return socket.inet_ntoa(struct.pack("<I", int(addr_int)))
            except Exception:
                return str(addr_int)
    return str(value)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DhcpScope:
    scope_id: str       # e.g. 10.0.1.0
    name: str
    subnet_mask: str
    start_range: str
    end_range: str
    state: str          # Active, InActive
    lease_duration: str

    @classmethod
    def from_dict(cls, d: dict) -> "DhcpScope":
        return cls(
            scope_id=_ip_str(d.get("ScopeId", "")),
            name=d.get("Name", "") or "",
            subnet_mask=_ip_str(d.get("SubnetMask", "")),
            start_range=_ip_str(d.get("StartRange", "")),
            end_range=_ip_str(d.get("EndRange", "")),
            state=d.get("State", ""),
            lease_duration=str(d.get("LeaseDuration", "")),
        )

    @property
    def active(self) -> bool:
        return self.state == "Active"


@dataclass
class DhcpLease:
    ip_address: str
    client_id: str      # MAC address
    hostname: str
    scope_id: str
    address_state: str  # Active, Expired, InactiveReservation, ActiveReservation
    lease_expires: str
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict, scope_id: str = "") -> "DhcpLease":
        return cls(
            ip_address=_ip_str(d.get("IPAddress", "")),
            client_id=d.get("ClientId", ""),
            hostname=d.get("HostName", "") or "",
            scope_id=_ip_str(scope_id or d.get("ScopeId", "")),
            address_state=d.get("AddressState", ""),
            lease_expires=str(d.get("LeaseExpiryTime", "")),
            description=d.get("Description", "") or "",
        )

    @property
    def is_reservation(self) -> bool:
        return "Reservation" in self.address_state


@dataclass
class DhcpReservation:
    ip_address: str
    client_id: str      # MAC address
    name: str
    scope_id: str
    description: str = ""
    reservation_type: str = "Both"   # Both, Dhcp, Bootp

    @classmethod
    def from_dict(cls, d: dict, scope_id: str = "") -> "DhcpReservation":
        return cls(
            ip_address=_ip_str(d.get("IPAddress", "")),
            client_id=d.get("ClientId", ""),
            name=d.get("Name", "") or "",
            scope_id=_ip_str(scope_id or d.get("ScopeId", "")),
            description=d.get("Description", "") or "",
            reservation_type=d.get("Type", "Both"),
        )


# ---------------------------------------------------------------------------
# DHCP Manager
# ---------------------------------------------------------------------------

class DHCPManager:
    """
    Manages DHCP on a specific server.
    Uses a direct WinRMSession to that server — no -ComputerName forwarding.
    server="" means connect to profile.host.
    """

    def __init__(self, pool: WinRMSessionPool, server: str = ""):
        self._pool   = pool
        self._server = server

    @property
    def _session(self):
        return self._pool.session_for(self._server)

    # ---- Scopes ----

    def list_scopes(self) -> list[DhcpScope]:
        raw = self._session.run_ps_json("""
Get-DhcpServerv4Scope |
    Select-Object ScopeId, Name, SubnetMask, StartRange, EndRange, State, LeaseDuration |
    ConvertTo-Json -Depth 3
""")
        if not raw:
            return []
        if isinstance(raw, dict):
            raw = [raw]
        return [DhcpScope.from_dict(s) for s in raw]

    # ---- Leases ----

    def list_leases(self, scope_id: str) -> list[DhcpLease]:
        raw = self._session.run_ps_json(f"""
$sid = '{scope_id}'
Get-DhcpServerv4Lease -ScopeId $sid |
    Select-Object IPAddress, ClientId, HostName, AddressState, LeaseExpiryTime, Description |
    ConvertTo-Json -Depth 3
""")
        if not raw:
            return []
        if isinstance(raw, dict):
            raw = [raw]
        return [DhcpLease.from_dict(l, scope_id) for l in raw]

    def list_all_leases(self) -> list[DhcpLease]:
        leases = []
        for scope in self.list_scopes():
            try:
                leases.extend(self.list_leases(scope.scope_id))
            except WinRMError:
                pass
        return leases

    # ---- Reservations ----

    def list_reservations(self, scope_id: str) -> list[DhcpReservation]:
        raw = self._session.run_ps_json(f"""
$sid = '{scope_id}'
Get-DhcpServerv4Reservation -ScopeId $sid |
    Select-Object IPAddress, ClientId, Name, Description, Type |
    ConvertTo-Json -Depth 3
""")
        if not raw:
            return []
        if isinstance(raw, dict):
            raw = [raw]
        return [DhcpReservation.from_dict(r, scope_id) for r in raw]

    def list_all_reservations(self) -> list[DhcpReservation]:
        reservations = []
        for scope in self.list_scopes():
            try:
                reservations.extend(self.list_reservations(scope.scope_id))
            except WinRMError:
                pass
        return reservations

    def add_reservation(self, scope_id: str, ip: str, mac: str, name: str,
                        description: str = "", reservation_type: str = "Both") -> None:
        mac_norm = mac.lower().replace(":", "-").replace(".", "-")
        desc_ps = f"-Description '{description}'" if description else ""
        self._session.run_ps(
            f"Add-DhcpServerv4Reservation "
            f"-ScopeId '{scope_id}' -IPAddress '{ip}' -ClientId '{mac_norm}' "
            f"-Name '{name}' {desc_ps} -Type '{reservation_type}'"
        )

    def update_reservation(self, scope_id: str, ip: str, name: str,
                           description: str = "") -> None:
        # Set-DhcpServerv4Reservation identifies the reservation by IP only,
        # -ScopeId is not a valid parameter for Set- (only for Get-/Add-/Remove-)
        desc_ps = f"-Description '{description}'" if description else ""
        self._session.run_ps(
            f"Set-DhcpServerv4Reservation -IPAddress '{ip}' -Name '{name}' {desc_ps}"
        )

    def delete_reservation(self, scope_id: str, ip: str) -> None:
        self._session.run_ps(
            f"Remove-DhcpServerv4Reservation -ScopeId '{scope_id}' -IPAddress '{ip}'"
        )

    def convert_lease_to_reservation(self, scope_id: str, ip: str) -> None:
        self._session.run_ps(f"""
$lease = Get-DhcpServerv4Lease -ScopeId '{scope_id}' -IPAddress '{ip}'
if (-not $lease) {{ throw "Lease {ip} not found in scope {scope_id}" }}
Add-DhcpServerv4Reservation `
  -ScopeId '{scope_id}' `
  -IPAddress $lease.IPAddress `
  -ClientId $lease.ClientId `
  -Name $lease.HostName `
  -Type Both
""")
