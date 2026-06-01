"""
DNS management via PowerShell DnsServer cmdlets.
Supports: A, AAAA, CNAME, PTR records + zone listing.

Each DNSManager targets ONE specific DNS server.  It obtains a direct
WinRMSession to that server from the WinRMSessionPool — no -ComputerName
forwarding, no double-hop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from netforge.transport.winrm import WinRMSessionPool, WinRMError


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DnsZone:
    name: str
    zone_type: str          # Primary, Secondary, Stub, Forwarder
    is_reverse: bool
    is_ad_integrated: bool

    @classmethod
    def from_dict(cls, d: dict) -> "DnsZone":
        return cls(
            name=d.get("ZoneName", ""),
            zone_type=d.get("ZoneType", ""),
            is_reverse=bool(d.get("IsReverseLookupZone", False)),
            is_ad_integrated=bool(d.get("IsDsIntegrated", False)),
        )


@dataclass
class DnsRecord:
    name: str
    record_type: str        # A, AAAA, CNAME, PTR
    ttl: int
    data: str               # IP for A/AAAA, hostname for CNAME/PTR
    zone: str = ""
    timestamp: str = ""

    @property
    def display_ttl(self) -> str:
        if self.ttl == 0:
            return "static"
        h, remainder = divmod(self.ttl, 3600)
        m, s = divmod(remainder, 60)
        if h:
            return f"{h}h{m:02d}m"
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"


# ---------------------------------------------------------------------------
# DNS Manager
# ---------------------------------------------------------------------------

class DNSManager:
    """
    Manages DNS on a specific server.
    Uses a direct WinRMSession to that server — no -ComputerName forwarding.
    server="" means connect to profile.host (the default/only server).
    """

    def __init__(self, pool: WinRMSessionPool, server: str = ""):
        self._pool   = pool
        self._server = server  # target DNS server hostname ("" = profile.host)

    @property
    def _session(self):
        return self._pool.session_for(self._server)

    # ---- Zones ----

    def list_zones(self) -> list[DnsZone]:
        raw = self._session.run_ps_json("""
Get-DnsServerZone |
    Select-Object ZoneName, ZoneType, IsReverseLookupZone, IsDsIntegrated |
    ConvertTo-Json -Depth 3
""")
        if isinstance(raw, dict):
            raw = [raw]
        return [DnsZone.from_dict(z) for z in raw]

    def add_zone(self, zone_name: str, zone_type: str = "Primary") -> None:
        if zone_type == "Primary":
            self._session.run_ps(
                f"Add-DnsServerPrimaryZone -Name '{zone_name}' -ReplicationScope 'Domain'"
            )
        else:
            raise WinRMError(f"Zone type '{zone_type}' not supported via this tool")

    def delete_zone(self, zone_name: str) -> None:
        self._session.run_ps(
            f"Remove-DnsServerZone -Name '{zone_name}' -Force"
        )

    # ---- Records ----

    def list_records(self, zone: str) -> list[DnsRecord]:
        # Assign zone to a variable first to keep the encoded command short.
        # The Data computed property is split to separate lines to avoid
        # the WinRM 8192-byte encoded-command limit on some PS versions.
        raw = self._session.run_ps_json(f"""
$zone = '{zone}'
$records = Get-DnsServerResourceRecord -ZoneName $zone |
    Where-Object {{ $_.RecordType -in 'A','AAAA','CNAME','PTR' }}
$out = foreach ($r in $records) {{
    $data = ''
    if ($r.RecordData.IPv4Address)   {{ $data = $r.RecordData.IPv4Address.ToString() }}
    elseif ($r.RecordData.IPv6Address)  {{ $data = $r.RecordData.IPv6Address.ToString() }}
    elseif ($r.RecordData.HostNameAlias){{ $data = $r.RecordData.HostNameAlias }}
    elseif ($r.RecordData.PtrDomainName){{ $data = $r.RecordData.PtrDomainName }}
    [PSCustomObject]@{{
        HostName   = $r.HostName
        RecordType = $r.RecordType
        TTL        = $r.TimeToLive.TotalSeconds
        Data       = $data
    }}
}}
$out | ConvertTo-Json -Depth 3
""")
        if not raw:
            return []
        if isinstance(raw, dict):
            raw = [raw]
        records = []
        for r in raw:
            records.append(DnsRecord(
                name=r.get("HostName", ""),
                record_type=r.get("RecordType", ""),
                ttl=int(r.get("TTL") or 0),
                data=r.get("Data", ""),
                zone=zone,
            ))
        return records

    # ---- A record ----

    def add_a_record(self, zone: str, name: str, ip: str, ttl: int = 3600) -> None:
        self._session.run_ps(
            f"Add-DnsServerResourceRecordA "
            f"-ZoneName '{zone}' -Name '{name}' -IPv4Address '{ip}' "
            f"-TimeToLive (New-TimeSpan -Seconds {ttl})"
        )

    def update_a_record(self, zone: str, name: str, old_ip: str, new_ip: str, ttl: int = 3600) -> None:
        self._session.run_ps(f"""
$old = Get-DnsServerResourceRecord -ZoneName '{zone}' -Name '{name}' -RRType A |
       Where-Object {{ $_.RecordData.IPv4Address.ToString() -eq '{old_ip}' }}
$new = $old.Clone()
$new.RecordData.IPv4Address = [System.Net.IPAddress]::Parse('{new_ip}')
$new.TimeToLive = New-TimeSpan -Seconds {ttl}
Set-DnsServerResourceRecord -ZoneName '{zone}' -OldInputObject $old -NewInputObject $new
""")

    # ---- AAAA record ----

    def add_aaaa_record(self, zone: str, name: str, ipv6: str, ttl: int = 3600) -> None:
        self._session.run_ps(
            f"Add-DnsServerResourceRecordAAAA "
            f"-ZoneName '{zone}' -Name '{name}' -IPv6Address '{ipv6}' "
            f"-TimeToLive (New-TimeSpan -Seconds {ttl})"
        )

    def update_aaaa_record(self, zone: str, name: str, old_ip: str, new_ip: str, ttl: int = 3600) -> None:
        self._session.run_ps(f"""
$old = Get-DnsServerResourceRecord -ZoneName '{zone}' -Name '{name}' -RRType AAAA |
       Where-Object {{ $_.RecordData.IPv6Address.ToString() -eq '{old_ip}' }}
$new = $old.Clone()
$new.RecordData.IPv6Address = [System.Net.IPAddress]::Parse('{new_ip}')
$new.TimeToLive = New-TimeSpan -Seconds {ttl}
Set-DnsServerResourceRecord -ZoneName '{zone}' -OldInputObject $old -NewInputObject $new
""")

    # ---- CNAME record ----

    def add_cname_record(self, zone: str, name: str, target: str, ttl: int = 3600) -> None:
        self._session.run_ps(
            f"Add-DnsServerResourceRecordCName "
            f"-ZoneName '{zone}' -Name '{name}' -HostNameAlias '{target}' "
            f"-TimeToLive (New-TimeSpan -Seconds {ttl})"
        )

    def update_cname_record(self, zone: str, name: str, old_target: str, new_target: str, ttl: int = 3600) -> None:
        self._session.run_ps(f"""
$old = Get-DnsServerResourceRecord -ZoneName '{zone}' -Name '{name}' -RRType CNAME |
       Where-Object {{ $_.RecordData.HostNameAlias -eq '{old_target}' }}
$new = $old.Clone()
$new.RecordData.HostNameAlias = '{new_target}'
$new.TimeToLive = New-TimeSpan -Seconds {ttl}
Set-DnsServerResourceRecord -ZoneName '{zone}' -OldInputObject $old -NewInputObject $new
""")

    # ---- PTR record ----

    def add_ptr_record(self, zone: str, name: str, fqdn: str, ttl: int = 3600) -> None:
        self._session.run_ps(
            f"Add-DnsServerResourceRecordPtr "
            f"-ZoneName '{zone}' -Name '{name}' -PtrDomainName '{fqdn}' "
            f"-TimeToLive (New-TimeSpan -Seconds {ttl})"
        )

    def update_ptr_record(self, zone: str, name: str, old_fqdn: str, new_fqdn: str, ttl: int = 3600) -> None:
        self._session.run_ps(f"""
$old = Get-DnsServerResourceRecord -ZoneName '{zone}' -Name '{name}' -RRType PTR |
       Where-Object {{ $_.RecordData.PtrDomainName -eq '{old_fqdn}' }}
$new = $old.Clone()
$new.RecordData.PtrDomainName = '{new_fqdn}'
$new.TimeToLive = New-TimeSpan -Seconds {ttl}
Set-DnsServerResourceRecord -ZoneName '{zone}' -OldInputObject $old -NewInputObject $new
""")

    # ---- Generic delete ----

    def delete_record(self, zone: str, name: str, record_type: str, data: str) -> None:
        rtype_map = {"A": "A", "AAAA": "AAAA", "CNAME": "CName", "PTR": "Ptr"}
        ps_type = rtype_map.get(record_type, record_type)
        self._session.run_ps(f"""
$rec = Get-DnsServerResourceRecord -ZoneName '{zone}' -Name '{name}' -RRType '{ps_type}' |
       Where-Object {{
         ($_.RecordData.IPv4Address -and $_.RecordData.IPv4Address.ToString() -eq '{data}') -or
         ($_.RecordData.IPv6Address -and $_.RecordData.IPv6Address.ToString() -eq '{data}') -or
         ($_.RecordData.HostNameAlias -eq '{data}') -or
         ($_.RecordData.PtrDomainName -eq '{data}')
       }}
if ($rec) {{ Remove-DnsServerResourceRecord -ZoneName '{zone}' -InputObject $rec -Force }}
else {{ throw "Record not found: {name} {record_type} {data}" }}
""")
