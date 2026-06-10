"""Generate SVG screenshots of NetForge for documentation.

Usage:
    python scripts/generate_screenshots.py

Outputs SVG files to docs/screenshots/.  No live WinRM connection needed —
data is entirely mocked.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make netforge importable from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netforge.config import ServerProfile
from netforge.modules.dns import DnsZone, DnsRecord
from netforge.modules.dhcp import DhcpScope, DhcpLease, DhcpReservation

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_ZONES = [
    DnsZone(name="corp.local",           zone_type="Primary",   is_reverse=False, is_ad_integrated=True),
    DnsZone(name="infra.corp.local",     zone_type="Primary",   is_reverse=False, is_ad_integrated=True),
    DnsZone(name="dev.corp.local",       zone_type="Primary",   is_reverse=False, is_ad_integrated=True),
    DnsZone(name="10.in-addr.arpa",      zone_type="Primary",   is_reverse=True,  is_ad_integrated=True),
    DnsZone(name="168.192.in-addr.arpa", zone_type="Primary",   is_reverse=True,  is_ad_integrated=True),
    DnsZone(name="external.example.com", zone_type="Secondary", is_reverse=False, is_ad_integrated=False),
    DnsZone(name="_msdcs.corp.local",    zone_type="Primary",   is_reverse=False, is_ad_integrated=True),
]

MOCK_RECORDS: dict[str, list[DnsRecord]] = {
    "corp.local": [
        DnsRecord(name="@",          record_type="A",     ttl=3600,  data="10.0.0.1",   zone="corp.local"),
        DnsRecord(name="dc01",       record_type="A",     ttl=3600,  data="10.0.0.5",   zone="corp.local"),
        DnsRecord(name="dc02",       record_type="A",     ttl=3600,  data="10.0.0.6",   zone="corp.local"),
        DnsRecord(name="mail",       record_type="A",     ttl=3600,  data="10.0.0.20",  zone="corp.local"),
        DnsRecord(name="vpn",        record_type="A",     ttl=3600,  data="203.0.113.1",zone="corp.local"),
        DnsRecord(name="smtp",       record_type="CNAME", ttl=3600,  data="mail.corp.local.", zone="corp.local"),
        DnsRecord(name="imap",       record_type="CNAME", ttl=3600,  data="mail.corp.local.", zone="corp.local"),
        DnsRecord(name="www",        record_type="CNAME", ttl=3600,  data="web01.corp.local.", zone="corp.local"),
        DnsRecord(name="web01",      record_type="A",     ttl=3600,  data="10.0.1.10",  zone="corp.local"),
        DnsRecord(name="web02",      record_type="A",     ttl=3600,  data="10.0.1.11",  zone="corp.local"),
        DnsRecord(name="db01",       record_type="A",     ttl=3600,  data="10.0.2.20",  zone="corp.local"),
        DnsRecord(name="db02",       record_type="A",     ttl=3600,  data="10.0.2.21",  zone="corp.local"),
        DnsRecord(name="ntp",        record_type="CNAME", ttl=3600,  data="dc01.corp.local.", zone="corp.local"),
        DnsRecord(name="mgmt",       record_type="A",     ttl=0,     data="10.0.0.100", zone="corp.local"),
        DnsRecord(name="ipv6host",   record_type="AAAA",  ttl=3600,  data="2001:db8::1", zone="corp.local"),
    ],
    "infra.corp.local": [
        DnsRecord(name="esxi01",     record_type="A",     ttl=3600,  data="10.0.10.11", zone="infra.corp.local"),
        DnsRecord(name="esxi02",     record_type="A",     ttl=3600,  data="10.0.10.12", zone="infra.corp.local"),
        DnsRecord(name="vcenter",    record_type="A",     ttl=3600,  data="10.0.10.20", zone="infra.corp.local"),
        DnsRecord(name="nas01",      record_type="A",     ttl=3600,  data="10.0.10.30", zone="infra.corp.local"),
        DnsRecord(name="backup",     record_type="A",     ttl=3600,  data="10.0.10.40", zone="infra.corp.local"),
    ],
    "10.in-addr.arpa": [
        DnsRecord(name="5.0.0",      record_type="PTR",   ttl=3600,  data="dc01.corp.local.",  zone="10.in-addr.arpa"),
        DnsRecord(name="6.0.0",      record_type="PTR",   ttl=3600,  data="dc02.corp.local.",  zone="10.in-addr.arpa"),
        DnsRecord(name="10.1.0",     record_type="PTR",   ttl=3600,  data="web01.corp.local.", zone="10.in-addr.arpa"),
        DnsRecord(name="11.1.0",     record_type="PTR",   ttl=3600,  data="web02.corp.local.", zone="10.in-addr.arpa"),
        DnsRecord(name="20.2.0",     record_type="PTR",   ttl=3600,  data="db01.corp.local.",  zone="10.in-addr.arpa"),
    ],
}

MOCK_SCOPES = [
    DhcpScope(scope_id="10.0.1.0", name="Workstations",
              subnet_mask="255.255.255.0", start_range="10.0.1.10",
              end_range="10.0.1.200", state="Active", lease_duration="8.00:00:00"),
    DhcpScope(scope_id="10.0.2.0", name="Servers",
              subnet_mask="255.255.255.0", start_range="10.0.2.10",
              end_range="10.0.2.100", state="Active", lease_duration="0.00:00:00"),
    DhcpScope(scope_id="192.168.10.0", name="Guest WiFi",
              subnet_mask="255.255.255.0", start_range="192.168.10.10",
              end_range="192.168.10.240", state="Active", lease_duration="4:00:00"),
    DhcpScope(scope_id="10.0.50.0", name="Lab",
              subnet_mask="255.255.255.0", start_range="10.0.50.10",
              end_range="10.0.50.200", state="InActive", lease_duration="1.00:00:00"),
]

MOCK_LEASES: dict[str, list[DhcpLease]] = {
    "10.0.1.0": [
        DhcpLease(ip_address="10.0.1.15",  client_id="00-11-22-33-44-01", hostname="LAPTOP-ALICE",    address_state="Active",              scope_id="10.0.1.0", lease_expires="2026-06-12 08:30:00"),
        DhcpLease(ip_address="10.0.1.22",  client_id="00-11-22-33-44-02", hostname="WS-BOB",          address_state="Active",              scope_id="10.0.1.0", lease_expires="2026-06-12 09:15:00"),
        DhcpLease(ip_address="10.0.1.33",  client_id="00-11-22-33-44-03", hostname="LAPTOP-CAROL",    address_state="Active",              scope_id="10.0.1.0", lease_expires="2026-06-12 10:00:00"),
        DhcpLease(ip_address="10.0.1.45",  client_id="00-11-22-33-44-04", hostname="DESKTOP-DAN",     address_state="Expired",             scope_id="10.0.1.0", lease_expires="2026-06-09 18:00:00"),
        DhcpLease(ip_address="10.0.1.50",  client_id="00-11-22-33-44-05", hostname="WS-EVE",          address_state="Active",              scope_id="10.0.1.0", lease_expires="2026-06-12 11:30:00"),
        DhcpLease(ip_address="10.0.1.101", client_id="aa-bb-cc-dd-ee-01", hostname="PRINTER-MFP01",   address_state="ActiveReservation",   scope_id="10.0.1.0", lease_expires=""),
        DhcpLease(ip_address="10.0.1.102", client_id="aa-bb-cc-dd-ee-02", hostname="PRINTER-FLOOR2",  address_state="ActiveReservation",   scope_id="10.0.1.0", lease_expires=""),
    ],
}

MOCK_RESERVATIONS: dict[str, list[DhcpReservation]] = {
    "10.0.1.0": [
        DhcpReservation(ip_address="10.0.1.101", client_id="aa-bb-cc-dd-ee-01",
                        name="PRINTER-MFP01",   description="Main floor MFP",
                        scope_id="10.0.1.0",    reservation_type="Both"),
        DhcpReservation(ip_address="10.0.1.102", client_id="aa-bb-cc-dd-ee-02",
                        name="PRINTER-FLOOR2",  description="2nd floor laser",
                        scope_id="10.0.1.0",    reservation_type="Both"),
        DhcpReservation(ip_address="10.0.1.103", client_id="aa-bb-cc-dd-ee-03",
                        name="AP-LOBBY",        description="Lobby access point",
                        scope_id="10.0.1.0",    reservation_type="Both"),
    ],
    "10.0.2.0": [
        DhcpReservation(ip_address="10.0.2.10", client_id="de-ad-be-ef-00-01",
                        name="web01",           description="Web server 1",
                        scope_id="10.0.2.0",    reservation_type="Both"),
        DhcpReservation(ip_address="10.0.2.11", client_id="de-ad-be-ef-00-02",
                        name="web02",           description="Web server 2",
                        scope_id="10.0.2.0",    reservation_type="Both"),
        DhcpReservation(ip_address="10.0.2.20", client_id="de-ad-be-ef-00-03",
                        name="db01",            description="Primary database",
                        scope_id="10.0.2.0",    reservation_type="Both"),
    ],
}

# ---------------------------------------------------------------------------
# Mock classes
# ---------------------------------------------------------------------------

class MockWinRMSessionPool:
    def probe(self):                  pass
    def session_for(self, host=""):   return MagicMock()
    def disconnect_all(self):         pass


class MockDNSManager:
    def __init__(self, pool, server=""):
        self._server = server

    def list_zones(self) -> list[DnsZone]:
        return MOCK_ZONES

    def list_records(self, zone: str) -> list[DnsRecord]:
        return MOCK_RECORDS.get(zone, [])


class MockDHCPManager:
    def __init__(self, pool, server=""):
        self._server = server

    def list_scopes(self) -> list[DhcpScope]:
        return MOCK_SCOPES

    def list_leases(self, scope_id: str) -> list[DhcpLease]:
        return MOCK_LEASES.get(scope_id, [])

    def list_reservations(self, scope_id: str) -> list[DhcpReservation]:
        return MOCK_RESERVATIONS.get(scope_id, [])


# ---------------------------------------------------------------------------
# Demo profile
# ---------------------------------------------------------------------------

DEMO_PROFILE = ServerProfile(
    name="corp-prod",
    host="dc01.corp.local",
    username=r"CORP\administrator",
    password="",
    port=5985,
    ssl=False,
    transport="ntlm",
    dns_servers=["dc01.corp.local"],
    dhcp_servers=["dhcp01.corp.local"],
)

# ---------------------------------------------------------------------------
# Screenshot runner
# ---------------------------------------------------------------------------

async def take_screenshots() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    patches = [
        patch("netforge.ui.dns_panel.DNSManager",  MockDNSManager),
        patch("netforge.ui.dhcp_panel.DHCPManager", MockDHCPManager),
        patch("netforge.ui.app.WinRMSessionPool",   MockWinRMSessionPool),
        patch("netforge.ui.app.get_default_profile", lambda: None),
        patch("netforge.config.list_profiles", lambda: [DEMO_PROFILE]),
        patch("netforge.ui.servers_panel.list_profiles", lambda: [DEMO_PROFILE]),
    ]
    for p in patches:
        p.start()

    try:
        from netforge.ui.app import NetForgeApp

        # ---- Screenshot 1: Login modal ----------------------------------------
        print("  [1/5] login modal…")
        app = NetForgeApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.3)
            svg = app.export_screenshot()
        (OUTPUT_DIR / "01_login.svg").write_text(svg)
        print("        → docs/screenshots/01_login.svg")

        # ---- Demo base: suppress login modal, auto-connect with mock data --------
        class DemoBase(NetForgeApp):
            """Skips login modal; connects directly with mock data on mount."""
            def _show_login(self) -> None:
                pass  # suppress the auto-login modal

            def on_mount(self) -> None:
                # Parent on_mount also runs (Textual calls all MRO handlers) but
                # _show_login is now a no-op so no modal is pushed.
                pool = MockWinRMSessionPool()
                self.call_after_refresh(self._apply_connection, pool, DEMO_PROFILE)

        # ---- Screenshot 2: DNS panel (connected) --------------------------------
        print("  [2/5] DNS panel…")
        app = DemoBase()
        async with app.run_test(size=(200, 52)) as pilot:
            await pilot.pause(2.5)   # wait for workers: zones + records
            svg = app.export_screenshot()
        (OUTPUT_DIR / "02_dns_panel.svg").write_text(svg)
        print("        → docs/screenshots/02_dns_panel.svg")

        # ---- Screenshot 3: DHCP leases ------------------------------------------
        print("  [3/5] DHCP leases…")
        app = DemoBase()
        async with app.run_test(size=(200, 52)) as pilot:
            await pilot.pause(2.0)
            await pilot.press("f2")   # switch to DHCP panel
            await pilot.pause(2.5)   # wait for scopes + leases
            svg = app.export_screenshot()
        (OUTPUT_DIR / "03_dhcp_leases.svg").write_text(svg)
        print("        → docs/screenshots/03_dhcp_leases.svg")

        # ---- Screenshot 4: DHCP reservations ------------------------------------
        print("  [4/5] DHCP reservations…")
        app = DemoBase()
        async with app.run_test(size=(200, 52)) as pilot:
            await pilot.pause(2.0)
            await pilot.press("f2")
            await pilot.pause(2.5)
            # Switch the Leases/Reservations selector to Reservations
            dhcp_panel = app.query_one("#panel_dhcp")
            view_select = dhcp_panel.query_one("#view_select")
            await pilot.click(view_select)
            await pilot.pause(0.5)
            await pilot.press("down", "enter")
            await pilot.pause(2.0)
            svg = app.export_screenshot()
        (OUTPUT_DIR / "04_dhcp_reservations.svg").write_text(svg)
        print("        → docs/screenshots/04_dhcp_reservations.svg")

        # ---- Screenshot 5: Servers panel ----------------------------------------
        print("  [5/5] Servers panel…")
        app = DemoBase()
        async with app.run_test(size=(200, 52)) as pilot:
            await pilot.pause(1.5)
            await pilot.press("f3")
            await pilot.pause(0.5)
            svg = app.export_screenshot()
        (OUTPUT_DIR / "05_servers_panel.svg").write_text(svg)
        print("        → docs/screenshots/05_servers_panel.svg")

    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    print("NetForge screenshot generator")
    print(f"Output → {OUTPUT_DIR}\n")
    asyncio.run(take_screenshots())
    print("\nDone.")
