"""
WinRM transport — NTLM and Kerberos support.

WinRMSession     — single authenticated session to one host.
WinRMSessionPool — manages one session per target host, all sharing the
                   same credentials/transport from the profile.

Transport options
-----------------
ntlm      Works from any Linux machine.  Password required per session.
          NTLM tickets cannot be forwarded (double-hop blocked at protocol
          level), but our direct-connection architecture avoids this entirely.

kerberos  Requires /etc/krb5.conf and a valid TGT (kinit or keytab).
          Password field is ignored — the OS ticket cache is used.
          Tickets can be delegated, so -ComputerName forwarding works too,
          though we don't use it.  Best for automation via keytab.

Setup for Kerberos
------------------
  apt install krb5-user libkrb5-dev
  pip install pywinrm[kerberos]   # pulls in gssapi, requests-gssapi

  /etc/krb5.conf:
    [libdefaults]
      default_realm = EXAMPLE.COM
      dns_lookup_kdc = true
      forwardable = true
    [realms]
      EXAMPLE.COM = { kdc = dc01.example.com }
    [domain_realm]
      .example.com = EXAMPLE.COM

  kinit user@EXAMPLE.COM           # interactive
  kinit -kt /etc/netforge/user.keytab user@EXAMPLE.COM   # headless/automation
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import winrm

from netforge.config import ServerProfile

log = logging.getLogger("netforge.transport")

TRANSPORT_NTLM     = "ntlm"
TRANSPORT_KERBEROS = "kerberos"


class WinRMError(Exception):
    pass


class AuthenticationError(WinRMError):
    pass


class KerberosNotAvailable(WinRMError):
    """Raised when kerberos transport is requested but gssapi is not installed."""
    pass


def _check_kerberos_available() -> None:
    """Raise KerberosNotAvailable if the required libraries aren't installed."""
    try:
        import gssapi  # noqa: F401
        import requests_gssapi  # noqa: F401
    except ImportError:
        raise KerberosNotAvailable(
            "Kerberos libraries not installed.\n"
            "Run: pip install pywinrm[kerberos]\n"
            "     apt install krb5-user libkrb5-dev\n"
            "Then run: kinit username@REALM.LOCAL"
        )


# ---------------------------------------------------------------------------
# Single host session
# ---------------------------------------------------------------------------

class WinRMSession:
    """One authenticated WinRM session to one host."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 5985,
        ssl: bool = False,
        transport: str = TRANSPORT_NTLM,
    ):
        self.host      = host
        self.username  = username
        self.port      = port
        self.ssl       = ssl
        self.transport = transport
        self._password = password
        self._session: winrm.Session | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        scheme = "https" if self.ssl else "http"
        return f"{scheme}://{self.host}:{self.port}/wsman"

    def connect(self) -> None:
        try:
            if self.transport == TRANSPORT_KERBEROS:
                _check_kerberos_available()
                self._session = winrm.Session(
                    self.url,
                    auth=(self.username, ""),   # ticket from OS cache, no password
                    transport="kerberos",
                    kerberos_hostname_override=self.host,
                    server_cert_validation="ignore",
                )
                log.info("WinRMSession Kerberos connect  host=%s  user=%s",
                         self.host, self.username)
            else:
                self._session = winrm.Session(
                    self.url,
                    auth=(self.username, self._password),
                    transport="ntlm",
                    server_cert_validation="ignore",
                )
                log.info("WinRMSession NTLM connect  host=%s  user=%s",
                         self.host, self.username)

            result = self._session.run_ps("$env:COMPUTERNAME")
            if result.status_code != 0:
                raise AuthenticationError(result.std_err.decode(errors="replace"))
            log.info("WinRMSession probe OK  host=%s  computername=%s",
                     self.host, result.std_out.decode().strip())

        except KerberosNotAvailable:
            raise
        except winrm.exceptions.InvalidCredentialsError as e:
            raise AuthenticationError(f"Invalid credentials for {self.host}: {e}") from e
        except (AuthenticationError, WinRMError):
            raise
        except Exception as e:
            raise WinRMError(f"Connection to {self.host} failed: {e}") from e

    def run_ps(self, script: str) -> str:
        if self._session is None:
            raise WinRMError(f"Not connected to {self.host}")
        # Timeout prevents indefinite freeze if the WinRM connection hangs
        acquired = self._lock.acquire(timeout=120)
        if not acquired:
            raise WinRMError(f"Timed out waiting for session lock on {self.host} (another command may be hung)")
        try:
            result = self._session.run_ps(script)
        finally:
            self._lock.release()
        if result.status_code != 0:
            stderr = result.std_err.decode(errors="replace").strip()
            raise WinRMError(stderr or f"PowerShell exited {result.status_code}")
        return result.std_out.decode(errors="replace").strip()

    def run_ps_json(self, script: str) -> Any:
        raw = self.run_ps(script)
        if not raw:
            return []

        # Strip any logon banner / MOTD that precedes the JSON.
        # Windows servers configured with a logon message prepend it to stdout.
        # Find the first JSON start character and discard everything before it.
        json_start = -1
        for i, ch in enumerate(raw):
            if ch in ("{", "["):
                json_start = i
                break

        if json_start == -1:
            return []   # no JSON found at all — empty result

        if json_start > 0:
            log.debug("run_ps_json: stripped %d chars of banner before JSON  host=%s",
                      json_start, self.host)
            raw = raw[json_start:]

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise WinRMError(
                f"Failed to parse JSON from {self.host}: {e}\nRaw: {raw[:500]}"
            ) from e

    def disconnect(self) -> None:
        self._session = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    def __repr__(self) -> str:
        state = "connected" if self.connected else "disconnected"
        return f"<WinRMSession {self.host} {self.transport} [{state}]>"


# ---------------------------------------------------------------------------
# Session pool — one session per target host, lazily connected
# ---------------------------------------------------------------------------

class WinRMSessionPool:
    """
    Manages direct WinRM sessions to multiple target hosts.

    All sessions in the pool share the same credentials and transport from
    the profile.  Each target host gets its own independent session so there
    is no jump-host forwarding and no double-hop issue.
    """

    def __init__(self, profile: ServerProfile):
        self._profile   = profile
        self._sessions: dict[str, WinRMSession] = {}
        self._pool_lock = threading.Lock()

    def session_for(self, host: str) -> WinRMSession:
        """
        Return a connected session for *host*.
        Empty string → use profile.host.
        Lazily creates and connects on first access.

        The pool lock only guards dict read/write, NOT the connect() call,
        so two workers connecting to different hosts don't block each other.
        """
        target = host or self._profile.host

        # Fast path — already connected
        with self._pool_lock:
            if target in self._sessions:
                return self._sessions[target]

        # Slow path — create and connect outside the lock so other threads
        # can still access already-connected sessions while this one connects
        sess = WinRMSession(
            host=target,
            username=self._profile.username,
            password=self._profile.password,
            port=self._profile.port,
            ssl=self._profile.ssl,
            transport=self._profile.transport,
        )
        log.info("SessionPool: connecting  host=%s  transport=%s",
                 target, self._profile.transport)
        sess.connect()

        # Store under lock — check again in case another thread raced us
        with self._pool_lock:
            if target not in self._sessions:
                self._sessions[target] = sess
            else:
                sess.disconnect()  # discard duplicate
        return self._sessions[target]

    def probe(self) -> None:
        """Connect to profile.host to verify credentials/transport."""
        self.session_for(self._profile.host)

    def disconnect_all(self) -> None:
        with self._pool_lock:
            for sess in self._sessions.values():
                sess.disconnect()
            self._sessions.clear()

    def __repr__(self) -> str:
        return (f"<WinRMSessionPool {self._profile.host} "
                f"transport={self._profile.transport} "
                f"hosts={list(self._sessions)}>")
