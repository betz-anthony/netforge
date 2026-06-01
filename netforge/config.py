"""
Server profile management.
Config file (~/.netforge.yaml) stores all settings except passwords.
Passwords are stored in the OS keyring (Gnome Keyring / KWallet / secret-service).

DNS:  dns_servers is a list of hostnames. Empty = run cmdlets locally on jump host.
DHCP: dhcp_servers is a list of hostnames. Empty = run cmdlets locally on jump host.

Example ~/.netforge.yaml:
  default_server: prod
  servers:
    prod:
      host: mgmt-server.example.com    # WinRM jump host (needs RSAT installed)
      username: EXAMPLE\administrator
      port: 5985
      ssl: false
      dns_servers:                     # omit = use jump host locally
        - dc01.example.com
        - dc02.example.com
      dhcp_servers:                    # omit = use jump host locally
        - dhcp01.example.com
        - dhcp02.example.com
        - dhcp03.example.com
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import keyring
import yaml

CONFIG_PATH = Path.home() / ".netforge.yaml"
KEYRING_SERVICE = "netforge"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class ServerProfile:
    def __init__(
        self,
        name: str,
        host: str,
        username: str,
        port: int = 5985,
        ssl: bool = False,
        password: str = "",
        dns_servers: list[str] | None = None,
        dhcp_servers: list[str] | None = None,
        transport: str = "ntlm",   # "ntlm" or "kerberos"
    ):
        self.name = name
        self.host = host
        self.username = username
        self.port = port
        self.ssl = ssl
        self.password = password        # held in memory only, never written to disk
        self.dns_servers  = dns_servers  or []
        self.dhcp_servers = dhcp_servers or []
        self.transport = transport if transport in ("ntlm", "kerberos") else "ntlm"

    @property
    def effective_dns_servers(self) -> list[str]:
        """DNS servers to manage. Empty list = run cmdlets locally on jump host."""
        return self.dns_servers if self.dns_servers else [""]

    @property
    def effective_dhcp_servers(self) -> list[str]:
        """DHCP servers to manage. Empty list = run cmdlets locally on jump host."""
        return self.dhcp_servers if self.dhcp_servers else [""]

    @property
    def url(self) -> str:
        scheme = "https" if self.ssl else "http"
        return f"{scheme}://{self.host}:{self.port}/wsman"

    def to_dict(self) -> dict:
        """Serialise to config file format (no password)."""
        d: dict = {
            "host":      self.host,
            "username":  self.username,
            "port":      self.port,
            "ssl":       self.ssl,
            "transport": self.transport,
        }
        if self.dns_servers:
            d["dns_servers"] = self.dns_servers
        if self.dhcp_servers:
            d["dhcp_servers"] = self.dhcp_servers
        return d

    def __repr__(self) -> str:
        return f"<ServerProfile {self.name} {self.host}>"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_servers(value: object) -> list[str]:
    """Accept string, list, or None from YAML and normalise to list[str]."""
    if not value:
        return []
    if isinstance(value, str):
        # comma-separated or single hostname
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, list):
        return [str(s).strip() for s in value if s]
    return []


def _profile_from_data(name: str, data: dict, password: str) -> ServerProfile:
    return ServerProfile(
        name=name,
        host=data["host"],
        username=data["username"],
        port=data.get("port", 5985),
        ssl=data.get("ssl", False),
        password=password,
        transport=data.get("transport", "ntlm"),
        # support both old single-value keys and new list keys
        dns_servers=_parse_servers(
            data.get("dns_servers") or data.get("dns_server")
        ),
        dhcp_servers=_parse_servers(
            data.get("dhcp_servers") or data.get("dhcp_server")
        ),
    )


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
    CONFIG_PATH.chmod(0o600)


def list_profiles() -> list[ServerProfile]:
    cfg = load_config()
    profiles = []
    for name, data in cfg.get("servers", {}).items():
        password = keyring.get_password(KEYRING_SERVICE, f"{name}:{data['host']}") or ""
        profiles.append(_profile_from_data(name, data, password))
    return profiles


def get_profile(name: str) -> Optional[ServerProfile]:
    cfg = load_config()
    servers = cfg.get("servers", {})
    if name not in servers:
        return None
    data = servers[name]
    password = keyring.get_password(KEYRING_SERVICE, f"{name}:{data['host']}") or ""
    return _profile_from_data(name, data, password)


def get_default_profile() -> Optional[ServerProfile]:
    cfg = load_config()
    default = cfg.get("default_server")
    if default:
        return get_profile(default)
    profiles = list_profiles()
    return profiles[0] if profiles else None


def save_profile(profile: ServerProfile, set_as_default: bool = False) -> None:
    cfg = load_config()
    cfg.setdefault("servers", {})[profile.name] = profile.to_dict()
    if set_as_default or not cfg.get("default_server"):
        cfg["default_server"] = profile.name
    save_config(cfg)
    if profile.password:
        keyring.set_password(KEYRING_SERVICE, f"{profile.name}:{profile.host}", profile.password)


def delete_profile(name: str) -> None:
    cfg = load_config()
    data = cfg.get("servers", {}).pop(name, None)
    if data:
        try:
            keyring.delete_password(KEYRING_SERVICE, f"{name}:{data['host']}")
        except keyring.errors.PasswordDeleteError:
            pass
    if cfg.get("default_server") == name:
        remaining = list(cfg.get("servers", {}).keys())
        cfg["default_server"] = remaining[0] if remaining else None
    save_config(cfg)


def set_default_profile(name: str) -> None:
    cfg = load_config()
    cfg["default_server"] = name
    save_config(cfg)
