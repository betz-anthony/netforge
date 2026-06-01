# NetForge — Windows DNS/DHCP TUI Manager

A terminal UI for managing Windows DNS and DHCP servers from Linux or macOS,
using WinRM over NTLM or Kerberos. No domain membership required.

---

## Architecture

Each DNS and DHCP server gets its own **direct WinRM connection** from the
Linux machine. There is no jump host and no `-ComputerName` forwarding, which
avoids the NTLM double-hop problem entirely.

```
Linux / macOS
  ├─ WinRM (NTLM or Kerberos) → dc01.example.com   (DNS — cmdlets run locally)
  ├─ WinRM (NTLM or Kerberos) → dc02.example.com   (DNS — cmdlets run locally)
  ├─ WinRM (NTLM or Kerberos) → dhcp01.example.com (DHCP — cmdlets run locally)
  └─ WinRM (NTLM or Kerberos) → dhcp02.example.com (DHCP — cmdlets run locally)
```

RSAT must be installed on each target server (`Install-WindowsFeature RSAT-DNS-Server`
/ `Install-WindowsFeature RSAT-DHCP`) so the PowerShell cmdlets are available.

---

## Installation

### 1. System packages

**Debian / Ubuntu**
```bash
apt install python3-pip python3-venv krb5-user libkrb5-dev python3-dev gcc
```

**RHEL / CentOS / Rocky**
```bash
yum install python3-pip krb5-workstation krb5-libs python3-devel gcc
```

**macOS**
```bash
brew install krb5
```

### 2. Create a virtual environment (recommended)
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install netforge

**NTLM only (simplest)**
```bash
pip install -e .
```

**With Kerberos support**
```bash
pip install -e ".[kerberos]"
```

**Headless / no desktop keyring (servers, containers, SSH sessions)**
```bash
pip install -e ".[headless]"
# or both:
pip install -e ".[kerberos,headless]"
```

---

## Windows Server Setup

Run the following on **each** DNS and DHCP server as Administrator:

```powershell
# Enable WinRM
winrm quickconfig -Force

# Allow NTLM authentication
Set-Item WSMan:\localhost\Service\Auth\Negotiate $true

# Open firewall — HTTP (5985) or HTTPS (5986)
netsh advfirewall firewall add rule name="WinRM HTTP" `
    protocol=TCP dir=in localport=5985 action=allow

# Install RSAT on DNS servers
Install-WindowsFeature RSAT-DNS-Server

# Install RSAT on DHCP servers
Install-WindowsFeature RSAT-DHCP
```

**Optional — HTTPS (recommended for production)**
```powershell
$cert = New-SelfSignedCertificate -DnsName "dc01.example.com" `
    -CertStoreLocation Cert:\LocalMachine\My
New-Item -Path WSMan:\localhost\Listener -Transport HTTPS `
    -Address * -CertificateThumbPrint $cert.Thumbprint -Force
netsh advfirewall firewall add rule name="WinRM HTTPS" `
    protocol=TCP dir=in localport=5986 action=allow
```

---

## Authentication

### NTLM (default — works everywhere)
No extra setup. Enter `DOMAIN\username` and password in the login screen.
Passwords are stored in the OS keyring (Gnome Keyring, macOS Keychain, KWallet)
— never written to disk in plaintext.

### Kerberos (recommended for production)
Requires `pip install -e ".[kerberos]"` and system Kerberos libraries.

**Configure `/etc/krb5.conf`:**
```ini
[libdefaults]
    default_realm = EXAMPLE.COM
    dns_lookup_kdc = true
    forwardable = true

[realms]
    EXAMPLE.COM = {
        kdc = dc01.example.com
        admin_server = dc01.example.com
    }

[domain_realm]
    .example.com = EXAMPLE.COM
    example.com  = EXAMPLE.COM
```

**Authenticate:**
```bash
# Interactive (valid for ~10 hours)
kinit username@EXAMPLE.COM

# Headless / automation via keytab
kinit -kt /etc/netforge/username.keytab username@EXAMPLE.COM

# Verify ticket
klist
```

Then select **Kerberos** in the netforge login screen — the password field is
ignored and the OS ticket cache is used automatically.

---

## Usage

```bash
# Launch interactively
python -m netforge

# Pre-fill host and username (password prompted in UI)
python -m netforge --host dc01.example.com --user "EXAMPLE\administrator"

# Use HTTP instead of HTTPS
python -m netforge --no-ssl
```

---

## Config file — `~/.netforge.yaml`

Passwords are stored in the **OS keyring only** — never written to this file.

```yaml
default_server: prod

servers:

  # Production — separate DNS and DHCP servers
  prod:
    host: mgmt-server.example.com  # primary host (used for credential probe)
    username: EXAMPLE\svc-dnsadmin
    port: 5985
    ssl: false
    transport: ntlm                # ntlm (default) or kerberos
    dns_servers:                   # each gets a direct WinRM connection
      - dc01.example.com
      - dc02.example.com
    dhcp_servers:
      - dhcp01.example.com
      - dhcp02.example.com
      - dhcp03.example.com

  # Lab — DNS and DHCP on same DC, Kerberos auth
  lab:
    host: lab-dc.lab.example.com
    username: adminuser@LAB.EXAMPLE.COM  # Kerberos UPN format
    port: 5985
    ssl: false
    transport: kerberos
    # dns_servers / dhcp_servers omitted = connect directly to host

  # HTTPS example
  prod-secure:
    host: dc01.example.com
    username: EXAMPLE\administrator
    port: 5986
    ssl: true
    transport: ntlm
```

**Config field reference**

| Field | Required | Default | Notes |
|---|---|---|---|
| `host` | yes | — | Primary host — used for credential probe |
| `username` | yes | — | `DOMAIN\user` for NTLM, `user@REALM` for Kerberos |
| `port` | no | 5985 | 5985 HTTP, 5986 HTTPS |
| `ssl` | no | false | Set true for HTTPS |
| `transport` | no | ntlm | `ntlm` or `kerberos` |
| `dns_servers` | no | [host] | List of DNS servers to manage directly |
| `dhcp_servers` | no | [host] | List of DHCP servers to manage directly |

---

## DNS Features

- **Grouped zone tree** — Forward Lookup Zones, Reverse Lookup Zones, Trust Points,
  Conditional Forwarders — sorted alphabetically, matching RSAT DNS Manager layout
- **Internal zone filter** — `_msdcs`, `_sites`, `_tcp`, `_udp` hidden by default;
  press **H** to toggle visibility
- **Record types** — A, AAAA, CNAME, PTR — full create / edit / delete
- **Filter** — type to filter records by name or data
- **Multi-server** — zones from multiple DNS servers shown in grouped tree

## DHCP Features

- **Scope list** — all scopes from all configured DHCP servers, active/inactive indicated
- **Leases view** — all current leases with state, MAC, hostname, expiry
- **Reservations view** — all reservations with MAC, name, description, type
- **Filter** — filter by IP, MAC, hostname, or description
- **New reservation** — create from scratch
- **Edit reservation** — update name and description
- **Delete reservation** — remove a reservation
- **Promote lease** — convert an active dynamic lease to a permanent reservation
  (reads MAC and hostname from the existing lease automatically)
- **Multi-server** — scopes from multiple DHCP servers shown grouped by server

---

## Keybindings

| Key | Action |
|---|---|
| F1 | DNS panel |
| F2 | DHCP panel |
| F3 | Servers panel |
| N | New record / reservation |
| E | Edit selected |
| D | Delete selected |
| P | Promote lease → reservation (DHCP leases view only) |
| H | Toggle internal AD zones (DNS only) |
| R | Refresh current panel |
| Ctrl+D | Re-run connection diagnostics |
| Ctrl+L | Reconnect |
| Escape | Close modal |
| F10 / Q | Quit |

---

## Log file

`~/.netforge.log` — full debug log written on every run. Includes all WinRM
connections, PowerShell commands, JSON responses, and error tracebacks.
Useful for diagnosing connection or permission issues.

```bash
tail -f ~/.netforge.log          # watch live
grep ERROR ~/.netforge.log       # show only errors
```

---

## Troubleshooting

**`WIN32 5` / Access Denied on DNS or DHCP cmdlets**
The account needs to be a member of `DnsAdmins` (for DNS) or
`DHCP Administrators` (for DHCP) on the target server, or be a Domain Admin.
```powershell
Add-ADGroupMember -Identity "DnsAdmins"         -Members "username"
Add-ADGroupMember -Identity "DHCP Administrators" -Members "username"
```

**`KDC reply did not match expectations` (Kerberos)**
The realm in `/etc/krb5.conf` must be uppercase and match the AD domain exactly.
Check `default_realm = EXAMPLE.COM` not `example.com`.

**`No module named 'gssapi'` (Kerberos)**
```bash
apt install libkrb5-dev python3-dev gcc
pip install ".[kerberos]"
```

**Keyring errors on headless servers**
```bash
pip install ".[headless]"
```

**Logon banners in output**
If Windows servers display a logon banner (e.g. "Type logoff and press Enter…"),
netforge automatically strips it from PowerShell output before JSON parsing.

**WinRM HTTP 400 errors**
Caused by concurrent shell limits. netforge uses one session per target server
with a threading lock, so this should not occur. If it does, increase the limit:
```powershell
Set-Item WSMan:\localhost\Shell\MaxShellsPerUser 10
```
