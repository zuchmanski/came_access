"""CAME Access API client.

Covers:
  - OAuth2 password-grant login and transparent token refresh
  - Auto-discovery of all SIP / BPT parameters from the cloud API
  - SIP REGISTER + MESSAGE over TLS to trigger door-open / AUX
  - 486 Busy retry loop with configurable back-off
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import re
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    BUSY_MAX_RETRIES,
    BUSY_RETRY_DELAY,
    CAME_CLIENT_ID,
    CAME_CLIENT_SECRET,
    FEATURE_MOBILE_APP,
    HTTP_TIMEOUT,
    MODULE_ENTRY_PANEL,
    MODULE_UNIT,
    SETTING_ENABLED,
    SETTING_PANEL_ADDR,
    SETTING_SIP_USER,
    SETTING_SRC_ADDR,
    SETTING_TARGET_USER,
    SIP_APP_NAME,
    SIP_AUTH_PREFIX,
    SIP_CONNECT_TIMEOUT,
    SIP_LANG,
    SIP_OS_TYPE,
    SIP_PANEL_ADDR_DEFAULT,
    SIP_PROXY_HOST_DEFAULT,
    SIP_PROXY_PORT,
    SIP_RECV_TIMEOUT,
    SIP_TARGET_USER_DEFAULT,
    TOKEN_REFRESH_MARGIN,
)

_LOGGER = logging.getLogger(__name__)

# ─── Exceptions ──────────────────────────────────────────────────────────────

class CameAccessError(Exception):
    """Base exception for all CAME Access errors."""

class CameAccessAuthError(CameAccessError):
    """OAuth or SIP authentication failure (bad credentials / expired token)."""

class CameAccessApiError(CameAccessError):
    """REST API call returned an unexpected status or payload."""

class CameAccessBusyError(CameAccessError):
    """The XTS7 unit returned SIP 486 Busy Here on all attempts."""

class CameAccessTimeoutError(CameAccessError):
    """Network or SIP operation timed out."""

class CameAccessDiscoveryError(CameAccessError):
    """Could not auto-discover required SIP/BPT parameters from the API."""


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class DoorConfig:
    """Everything needed to send a SIP door-open command."""
    sip_user: str        # 00700100003
    keycode: str         # 7FDF0BAD03DB7B0A
    src_addr: str        # 00e70003
    panel_addr: str      # 00e00000
    target_user: str     # 00800000000
    sip_password: str    # IgVbwSVWtrwMHbJ2  (raw, from API)
    device_token: str    # FCM push token
    subject_label: str   # "Mobile App 4"
    proxy_host: str = SIP_PROXY_HOST_DEFAULT
    proxy_port: int = SIP_PROXY_PORT

    @property
    def sip_domain(self) -> str:
        return f"{self.keycode}.xip.cameconnect.net"

    @property
    def auth_password(self) -> str:
        """Full SIP digest password: BptX1pM0b1l3 + raw SIP password."""
        return f"{SIP_AUTH_PREFIX}{self.sip_password}"


@dataclass
class DiscoveredDevice:
    """One XTS7 / BPT device found in the cloud API."""
    device_id: int
    site_id: int
    name: str
    door_config: DoorConfig


@dataclass
class ActionResult:
    """Result of a door-open or AUX command."""
    success: bool
    register_status: str = ""
    message_status: str = ""
    retries_used: int = 0
    error: str = ""


# ─── Private helpers ──────────────────────────────────────────────────────────

def _basic_auth_header() -> dict[str, str]:
    """Build the Basic Authorization header used for all OAuth requests."""
    raw = f"{CAME_CLIENT_ID}:{CAME_CLIENT_SECRET}".encode()
    token = base64.b64encode(raw).decode()
    return {"Authorization": f"Basic {token}"}


def _settings_map(settings: Any) -> dict[int, str]:
    if not isinstance(settings, list):
        return {}
    out: dict[int, str] = {}
    for item in settings:
        if isinstance(item, dict) and "SettingId" in item and "Value" in item:
            out[int(item["SettingId"])] = str(item["Value"])
    return out


def _coerce_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        for key in ("Data", "data", "Items", "items"):
            v = payload.get(key)
            if isinstance(v, list):
                return [i for i in v if isinstance(i, dict)]
    return []


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _build_open_door_xml(src: str, dst: str) -> str:
    return (
        "<BPT_COMMAND><COMMAND><type>OPEN_DOOR</type>"
        f"<src_addr>{src}</src_addr>"
        f"<dst_addr>{dst}</dst_addr>"
        "</COMMAND></BPT_COMMAND>"
    )


def _build_aux_xml(src: str, dst: str, aux_code: int) -> str:
    return (
        "<BPT_COMMAND><COMMAND><type>AUX_COMMAND</type>"
        f"<aux_code>{aux_code}</aux_code>"
        f"<src_addr>{src}</src_addr>"
        f"<dst_addr>{dst}</dst_addr>"
        "</COMMAND></BPT_COMMAND>"
    )


def _build_subject(src: str, dst: str, label: str) -> str:
    """Subject header format reverse-engineered from CAME Access app traffic."""
    return f"{src};{dst};00001;;{label[:24]}"


# ─── SIP helpers (all blocking, called via asyncio.to_thread) ─────────────────

def _tls_connect(host: str, port: int) -> ssl.SSLSocket:
    """Open a TLS-over-TCP connection to the SIP proxy.
    Falls back to legacy cipher set if the server doesn't support modern TLS.
    """
    def _ctx(legacy: bool = False) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if legacy:
            with contextlib.suppress(Exception):
                ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
            if hasattr(ssl, "TLSVersion"):
                with contextlib.suppress(Exception):
                    ctx.minimum_version = ssl.TLSVersion.TLSv1
        return ctx

    raw = socket.create_connection((host, port), timeout=SIP_CONNECT_TIMEOUT)
    try:
        return _ctx().wrap_socket(raw, server_hostname=host)
    except ssl.SSLError as exc:
        raw.close()
        _LOGGER.debug("TLS handshake failed (%s), retrying with legacy settings", exc)
        raw2 = socket.create_connection((host, port), timeout=SIP_CONNECT_TIMEOUT)
        try:
            return _ctx(legacy=True).wrap_socket(raw2, server_hostname=host)
        except Exception:
            raw2.close()
            raise


def _recv_sip(sock: ssl.SSLSocket) -> str:
    """Read one complete SIP message (headers + body) from the socket."""
    buf = b""
    sock.settimeout(SIP_RECV_TIMEOUT)
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            text = buf.decode(errors="replace")
            if "\r\n\r\n" not in text:
                continue
            m = re.search(r"Content-Length:\s*(\d+)", text, re.I)
            content_len = int(m.group(1)) if m else 0
            header_end = text.index("\r\n\r\n") + 4
            if len(text) - header_end >= content_len:
                break
    except socket.timeout:
        pass
    return buf.decode(errors="replace")


def _sip_status(response: str) -> int:
    try:
        return int(response.split()[1])
    except (IndexError, ValueError):
        return 0


def _sip_status_line(response: str) -> str:
    return response.split("\r\n", 1)[0] if response else "no response"


def _parse_challenge(header_line: str) -> dict[str, str]:
    _, _, rest = header_line.partition(":")
    rest = rest.strip()
    if rest.lower().startswith("digest "):
        rest = rest[7:]
    params: dict[str, str] = {}
    for key, val in re.findall(r'(\w+)=(".*?"|[^,\s]+)', rest):
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        params[key.lower()] = val
    return params


def _digest_auth_header(
    header_name: str,
    challenge: dict[str, str],
    method: str,
    uri: str,
    sip_user: str,
    password: str,
) -> str:
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop_offer = challenge.get("qop", "")
    opaque = challenge.get("opaque", "")

    ha1 = _md5(f"{sip_user}:{realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")

    picked_qop = "auth" if "auth" in qop_offer else (qop_offer.split(",")[0].strip() if qop_offer else "")

    if picked_qop:
        nc = "00000001"
        cnonce = uuid.uuid4().hex[:16]
        response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{picked_qop}:{ha2}")
        parts = [
            f'{header_name}: Digest username="{sip_user}"',
            f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"',
            f'response="{response}"', "algorithm=MD5",
            f"qop={picked_qop}", f"nc={nc}", f'cnonce="{cnonce}"',
        ]
    else:
        response = _md5(f"{ha1}:{nonce}:{ha2}")
        parts = [
            f'{header_name}: Digest username="{sip_user}"',
            f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"',
            f'response="{response}"', "algorithm=MD5",
        ]

    if opaque:
        parts.append(f'opaque="{opaque}"')
    return ", ".join(parts)


def _challenge_line(response: str) -> str:
    for line in response.split("\r\n"):
        lo = line.lower()
        if lo.startswith("www-authenticate:") or lo.startswith("proxy-authenticate:"):
            return line
    return ""


def _build_register(
    cfg: DoorConfig,
    local_ip: str,
    local_port: int,
    call_id: str,
    tag: str,
    cseq: int,
    auth: str = "",
) -> bytes:
    branch = f"z9hG4bK{uuid.uuid4().hex[:12]}"
    contact = f"sip:{cfg.sip_user}@{local_ip}:{local_port};transport=tls"
    lines = [
        f"REGISTER sip:{cfg.sip_domain} SIP/2.0",
        f"Via: SIP/2.0/TLS {local_ip}:{local_port};branch={branch};rport",
        "Max-Forwards: 70",
        f"To: <sip:{cfg.sip_user}@{cfg.sip_domain}>",
        f"From: <sip:{cfg.sip_user}@{cfg.sip_domain}>;tag={tag}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} REGISTER",
        f"Contact: <{contact}>",
        "User-Agent: came-access-ha/1.0.0",
        "Expires: 300",
    ]
    if auth:
        lines.append(auth)
    lines.append("Content-Length: 0")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _build_message(
    cfg: DoorConfig,
    local_ip: str,
    local_port: int,
    call_id: str,
    tag: str,
    cseq: int,
    body: str,
    subject: str,
    auth: str = "",
) -> bytes:
    to_uri = f"sip:{cfg.target_user}@{cfg.sip_domain}"
    branch = f"z9hG4bK{uuid.uuid4().hex[:12]}"
    body_bytes = body.encode("utf-8")
    lines = [
        f"MESSAGE {to_uri} SIP/2.0",
        f"Via: SIP/2.0/TLS {local_ip}:{local_port};branch={branch};rport",
        "Max-Forwards: 70",
        f"To: <{to_uri}>",
        f"From: <sip:{cfg.sip_user}@{cfg.sip_domain}>;tag={tag}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} MESSAGE",
        f"Subject: {subject}",
        "Content-Type: text/xml; charset=utf-8",
        "User-Agent: came-access-ha/1.0.0",
    ]
    if auth:
        lines.append(auth)
    lines.append(f"Content-Length: {len(body_bytes)}")
    lines.append("")
    lines.append(body)
    return ("\r\n".join(lines) + "\r\n").encode()


def _sip_send_command_blocking(cfg: DoorConfig, xml_body: str, subject: str) -> ActionResult:
    """
    Blocking SIP flow: TLS connect → REGISTER (with digest auth) →
    MESSAGE (with digest auth) → disconnect.

    Called via asyncio.to_thread so it never blocks the event loop.

    Raises:
        CameAccessBusyError  – on SIP 486 Busy Here
        CameAccessAuthError  – on SIP 401/407 with no challenge or wrong creds
        CameAccessTimeoutError – on network timeout
        CameAccessApiError   – on unexpected SIP status
    """
    try:
        sock = _tls_connect(cfg.proxy_host, cfg.proxy_port)
    except (socket.timeout, OSError, ssl.SSLError) as exc:
        raise CameAccessTimeoutError(f"SIP TLS connect failed: {exc}") from exc

    try:
        local_ip, local_port = sock.getsockname()[:2]

        # ── REGISTER ─────────────────────────────────────────────────────────
        reg_cid = f"{uuid.uuid4().hex}@{local_ip}"
        reg_tag = uuid.uuid4().hex[:8]
        reg_uri = f"sip:{cfg.sip_domain}"

        sock.sendall(_build_register(cfg, local_ip, local_port, reg_cid, reg_tag, 1))
        r1 = _recv_sip(sock)
        code1 = _sip_status(r1)
        _LOGGER.debug("REGISTER r1: %s", _sip_status_line(r1))

        if code1 in (401, 407):
            cl = _challenge_line(r1)
            if not cl:
                raise CameAccessAuthError(f"SIP REGISTER 401 with no challenge ({_sip_status_line(r1)})")
            ch = _parse_challenge(cl)
            hdr_name = "Authorization" if code1 == 401 else "Proxy-Authorization"
            auth_hdr = _digest_auth_header(hdr_name, ch, "REGISTER", reg_uri, cfg.sip_user, cfg.auth_password)
            sock.sendall(_build_register(cfg, local_ip, local_port, reg_cid, reg_tag, 2, auth_hdr))
            r1 = _recv_sip(sock)
            code1 = _sip_status(r1)
            _LOGGER.debug("REGISTER r2: %s", _sip_status_line(r1))

        if code1 != 200:
            raise CameAccessAuthError(f"SIP REGISTER failed: {_sip_status_line(r1)}")

        reg_status = _sip_status_line(r1)

        # ── MESSAGE ───────────────────────────────────────────────────────────
        msg_cid = f"{uuid.uuid4().hex}@{local_ip}"
        msg_tag = uuid.uuid4().hex[:8]
        to_uri = f"sip:{cfg.target_user}@{cfg.sip_domain}"

        sock.sendall(_build_message(cfg, local_ip, local_port, msg_cid, msg_tag, 1, xml_body, subject))
        r2 = _recv_sip(sock)
        code2 = _sip_status(r2)
        _LOGGER.debug("MESSAGE r1: %s", _sip_status_line(r2))

        if code2 == 486:
            raise CameAccessBusyError("XTS7 unit is busy (486 Busy Here)")

        if code2 in (401, 407):
            cl = _challenge_line(r2)
            if not cl:
                raise CameAccessAuthError(f"SIP MESSAGE 401 with no challenge ({_sip_status_line(r2)})")
            ch = _parse_challenge(cl)
            hdr_name = "Authorization" if code2 == 401 else "Proxy-Authorization"
            auth_hdr = _digest_auth_header(hdr_name, ch, "MESSAGE", to_uri, cfg.sip_user, cfg.auth_password)
            sock.sendall(_build_message(cfg, local_ip, local_port, msg_cid, msg_tag, 2, xml_body, subject, auth_hdr))
            r2 = _recv_sip(sock)
            code2 = _sip_status(r2)
            _LOGGER.debug("MESSAGE r2: %s", _sip_status_line(r2))

        if code2 == 486:
            raise CameAccessBusyError("XTS7 unit is busy (486 Busy Here)")

        if code2 not in (200, 202):
            raise CameAccessApiError(f"SIP MESSAGE failed: {_sip_status_line(r2)}")

        return ActionResult(
            success=True,
            register_status=reg_status,
            message_status=_sip_status_line(r2),
        )

    finally:
        with contextlib.suppress(Exception):
            sock.close()


# ─── Main client ──────────────────────────────────────────────────────────────

class CameAccessClient:
    """
    Async CAME Access cloud client.

    Handles:
      - Password-grant OAuth with transparent refresh
      - Full auto-discovery of SIP/BPT parameters
      - Door-open and AUX via SIP over TLS
      - 486 Busy retry loop (up to BUSY_MAX_RETRIES × BUSY_RETRY_DELAY s)
    """

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    # ── OAuth ──────────────────────────────────────────────────────────────────

    async def async_login(self) -> None:
        """Perform initial password-grant OAuth and store tokens.

        Raises CameAccessAuthError on bad credentials.
        """
        data = {
            "username": self._username,
            "password": self._password,
            "grant_type": "password",
            "scope": "offline",
        }
        headers = {
            **_basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with self._session.post(
                f"{API_BASE}/oauth/token",
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as resp:
                js: dict = await resp.json(content_type=None)
                if resp.status == 401 or (
                    resp.status == 400 and js.get("error") in {"invalid_grant", "invalid_client", "unauthorized_client"}
                ):
                    raise CameAccessAuthError(f"Login failed: {js.get('error_description', js)}")
                if resp.status != 200 or "access_token" not in js:
                    raise CameAccessApiError(f"OAuth token endpoint returned {resp.status}: {js}")
                self._store_tokens(js)
        except aiohttp.ClientError as exc:
            raise CameAccessApiError(f"Network error during login: {exc}") from exc

    async def _async_refresh(self) -> None:
        """Use the refresh_token to obtain a new access_token."""
        if not self._refresh_token:
            await self.async_login()
            return
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        headers = {
            **_basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with self._session.post(
                f"{API_BASE}/oauth/token",
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as resp:
                js: dict = await resp.json(content_type=None)
                if resp.status in (400, 401):
                    # Refresh token expired or revoked — do a full re-login
                    _LOGGER.info("Refresh token rejected (%s), performing full re-login", resp.status)
                    await self.async_login()
                    return
                if resp.status != 200 or "access_token" not in js:
                    raise CameAccessApiError(f"Token refresh returned {resp.status}: {js}")
                self._store_tokens(js)
        except aiohttp.ClientError as exc:
            raise CameAccessApiError(f"Network error during token refresh: {exc}") from exc

    def _store_tokens(self, js: dict) -> None:
        self._access_token = js["access_token"]
        self._refresh_token = js.get("refresh_token") or self._refresh_token
        ttl = int(js.get("expires_in", 0)) or 7200
        self._expires_at = time.monotonic() + max(60, ttl - TOKEN_REFRESH_MARGIN)

    def _token_valid(self) -> bool:
        return bool(self._access_token) and time.monotonic() < self._expires_at

    async def async_ensure_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._token_valid():
            return self._access_token  # type: ignore[return-value]
        async with self._token_lock:
            if not self._token_valid():
                await self._async_refresh()
        return self._access_token  # type: ignore[return-value]

    # ── REST helpers ───────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> tuple[int, Any]:
        """Authenticated GET with one automatic 401 retry."""
        token = await self.async_ensure_token()
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                async with self._session.get(
                    f"{API_BASE}{path}",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                ) as resp:
                    try:
                        js = await resp.json(content_type=None)
                    except Exception:
                        js = {}
                    if resp.status == 401 and attempt == 0:
                        self._access_token = None
                        token = await self.async_ensure_token()
                        continue
                    return resp.status, js
            except aiohttp.ClientError as exc:
                raise CameAccessApiError(f"GET {path} network error: {exc}") from exc
        return 500, {}

    # ── Discovery ──────────────────────────────────────────────────────────────

    async def async_discover_devices(self) -> list[DiscoveredDevice]:
        """
        Full auto-discovery chain:
          1. /api/evo/v1/sipaccounts       → device_token, keycode, sip_user
          2. /api/evo/v1/sites             → site list
          3. /api/evo/v1/sites/{id}/devices → full module/feature/SipAccounts data
          4. /api/evo/v1/checkdomainip     → SIP proxy host

        Returns a list of DiscoveredDevice (one per XTS7/BPT unit found).
        Raises CameAccessDiscoveryError if nothing is found.
        """
        # Step 1 – SIP accounts (maps current user → their FCM device_token + keycode)
        sip_accounts = await self._fetch_sip_accounts()
        if not sip_accounts:
            raise CameAccessDiscoveryError(
                "No SIP accounts found. Make sure the CAME Access app is installed and "
                "the account has accepted an invitation to an XTS7 unit."
            )

        devices: list[DiscoveredDevice] = []
        seen: set[int] = set()

        for account in sip_accounts:
            device_token = str(account.get("DeviceToken", "")).strip()
            keycode = str(account.get("Keycode", "")).strip()
            sip_user_from_account = str(account.get("SipUsername", "")).strip()

            if not device_token or not keycode:
                continue

            # Step 2 – Sites
            try:
                sites = await self._fetch_sites(device_token)
            except CameAccessApiError as exc:
                _LOGGER.warning("Could not fetch sites for token %s…: %s", device_token[:12], exc)
                continue

            for site in sites:
                site_id = site.get("Id") or site.get("SiteId")
                if site_id is None:
                    continue

                # Step 3 – Devices in this site
                try:
                    raw_devices = await self._fetch_site_devices(site_id, device_token)
                except CameAccessApiError as exc:
                    _LOGGER.warning("Could not fetch devices for site %s: %s", site_id, exc)
                    continue

                for raw_dev in raw_devices:
                    dev_id = raw_dev.get("DeviceId") or raw_dev.get("Id")
                    if dev_id is None or int(dev_id) in seen:
                        continue

                    try:
                        door_cfg = _extract_door_config(
                            raw_dev,
                            keycode=keycode,
                            device_token=device_token,
                            preferred_sip_user=sip_user_from_account,
                        )
                    except CameAccessDiscoveryError as exc:
                        _LOGGER.debug("Skipping device %s: %s", dev_id, exc)
                        continue

                    # Step 4 – Resolve SIP proxy host
                    proxy_host = await self._resolve_proxy_host(door_cfg.sip_domain)
                    door_cfg = DoorConfig(
                        sip_user=door_cfg.sip_user,
                        keycode=door_cfg.keycode,
                        src_addr=door_cfg.src_addr,
                        panel_addr=door_cfg.panel_addr,
                        target_user=door_cfg.target_user,
                        sip_password=door_cfg.sip_password,
                        device_token=door_cfg.device_token,
                        subject_label=door_cfg.subject_label,
                        proxy_host=proxy_host,
                        proxy_port=SIP_PROXY_PORT,
                    )

                    dev_name = str(
                        raw_dev.get("AliasName") or raw_dev.get("Name") or f"CAME Device {dev_id}"
                    ).strip()

                    devices.append(DiscoveredDevice(
                        device_id=int(dev_id),
                        site_id=int(site_id),
                        name=dev_name,
                        door_config=door_cfg,
                    ))
                    seen.add(int(dev_id))

        if not devices:
            raise CameAccessDiscoveryError(
                "No compatible XTS7/BPT device was found under this account. "
                "Ensure the CAME Access app shows the unit and the invitation has been accepted."
            )

        return devices

    async def _fetch_sip_accounts(self) -> list[dict]:
        status, js = await self._get("/evo/v1/sipaccounts")
        if status != 200:
            raise CameAccessApiError(f"/sipaccounts returned {status}: {js}")
        accounts = _coerce_list(js)
        # The endpoint may return a bare list rather than wrapped
        if not accounts and isinstance(js, list):
            accounts = [i for i in js if isinstance(i, dict)]
        return accounts

    async def _fetch_sites(self, device_token: str) -> list[dict]:
        status, js = await self._get("/evo/v1/sites", params={"dt": device_token})
        if status != 200:
            raise CameAccessApiError(f"/sites returned {status}: {js}")
        sites = _coerce_list(js)
        if not sites and isinstance(js, list):
            sites = [i for i in js if isinstance(i, dict)]
        if not sites:
            raise CameAccessDiscoveryError("No sites found for this account.")
        return sites

    async def _fetch_site_devices(self, site_id: int | str, device_token: str) -> list[dict]:
        status, js = await self._get(
            f"/evo/v1/sites/{site_id}/devices",
            params={"dt": device_token},
        )
        if status != 200:
            raise CameAccessApiError(f"/sites/{site_id}/devices returned {status}: {js}")
        devs = _coerce_list(js)
        if not devs and isinstance(js, list):
            devs = [i for i in js if isinstance(i, dict)]
        return devs

    async def _resolve_proxy_host(self, sip_domain: str) -> str:
        """Ask the API which IP to use for this SIP domain."""
        try:
            status, js = await self._get("/evo/v1/checkdomainip", params={"sipDomain": sip_domain})
            if status == 200 and isinstance(js, dict):
                ip = str(js.get("IpAddress") or js.get("ip") or "").strip()
                if ip:
                    _LOGGER.debug("SIP proxy for %s resolved to %s", sip_domain, ip)
                    return ip
        except Exception as exc:
            _LOGGER.debug("checkdomainip failed (%s), using default proxy", exc)
        return SIP_PROXY_HOST_DEFAULT

    # ── Cloud pre-notify (xipregister) ────────────────────────────────────────

    async def _async_xipregister(self, cfg: DoorConfig) -> None:
        """
        Tell CAME's push service that HA is about to make a SIP call.
        This wakes up the XTS7 so it's ready to receive the MESSAGE.
        Non-fatal if it fails.
        """
        try:
            status, js = await self._get(
                "/push/xipregister",
                params={
                    "sipUri": cfg.sip_user,
                    "sipDomain": cfg.sip_domain,
                    "voipDeviceToken": cfg.device_token,
                    "pushDeviceToken": cfg.device_token,
                    "remoteSrv": 1,
                    "appName": SIP_APP_NAME,
                    "osType": SIP_OS_TYPE,
                    "lang": SIP_LANG,
                },
            )
            if status != 200:
                _LOGGER.warning("xipregister returned %s (continuing with SIP anyway)", status)
            else:
                _LOGGER.debug("xipregister OK for sip_user=%s", cfg.sip_user)
        except Exception as exc:
            _LOGGER.warning("xipregister failed (%s), continuing with SIP anyway", exc)

    # ── Door / AUX commands ───────────────────────────────────────────────────

    async def async_open_door(self, cfg: DoorConfig) -> ActionResult:
        """
        Open the door linked to `cfg`.

        Flow:
          1. xipregister (cloud wake-up signal)
          2. SIP REGISTER + MESSAGE over TLS
          3. If 486 Busy: wait BUSY_RETRY_DELAY s, retry up to BUSY_MAX_RETRIES times
        """
        await self._async_xipregister(cfg)
        xml = _build_open_door_xml(cfg.src_addr, cfg.panel_addr)
        subject = _build_subject(cfg.src_addr, cfg.panel_addr, cfg.subject_label)
        return await self._run_with_busy_retry(cfg, xml, subject)

    async def async_trigger_aux(self, cfg: DoorConfig, aux_code: int) -> ActionResult:
        """Trigger an AUX output on the entry panel."""
        await self._async_xipregister(cfg)
        xml = _build_aux_xml(cfg.src_addr, cfg.panel_addr, aux_code)
        subject = _build_subject(cfg.src_addr, cfg.panel_addr, cfg.subject_label)
        result = await self._run_with_busy_retry(cfg, xml, subject)
        result.retries_used = result.retries_used  # pass through
        return result

    async def _run_with_busy_retry(
        self, cfg: DoorConfig, xml: str, subject: str
    ) -> ActionResult:
        """
        Run the blocking SIP command via asyncio.to_thread.
        On CameAccessBusyError, sleep and retry up to BUSY_MAX_RETRIES times.
        After exhausting retries, re-raise so the caller can surface it to HA.
        """
        last_exc: CameAccessBusyError | None = None
        for attempt in range(BUSY_MAX_RETRIES + 1):
            if attempt > 0:
                _LOGGER.warning(
                    "XTS7 unit is busy, retry %d/%d in %ds…",
                    attempt, BUSY_MAX_RETRIES, BUSY_RETRY_DELAY,
                )
                await asyncio.sleep(BUSY_RETRY_DELAY)
            try:
                result = await asyncio.to_thread(_sip_send_command_blocking, cfg, xml, subject)
                result.retries_used = attempt
                return result
            except CameAccessBusyError as exc:
                last_exc = exc
                continue  # retry
            # Other exceptions (auth, timeout, API) propagate immediately
        # All retries exhausted
        raise CameAccessBusyError(
            f"XTS7 unit was busy after {BUSY_MAX_RETRIES} retries "
            f"({BUSY_MAX_RETRIES * BUSY_RETRY_DELAY}s total wait). "
            "Another call may be active on the unit."
        ) from last_exc


# ─── Device-metadata extractor ────────────────────────────────────────────────

def _extract_door_config(
    raw_dev: dict,
    *,
    keycode: str,
    device_token: str,
    preferred_sip_user: str = "",
) -> DoorConfig:
    """
    Parse a raw device dict from /api/evo/v1/sites/{id}/devices and build a
    DoorConfig.  Raises CameAccessDiscoveryError if required fields are absent.
    """
    dev_keycode = str(raw_dev.get("Keycode", "") or keycode).strip()
    if not dev_keycode:
        raise CameAccessDiscoveryError(f"Device {raw_dev.get('Id')} has no Keycode")

    modules = raw_dev.get("Modules") or []
    if not isinstance(modules, list):
        modules = []

    panel_addr = SIP_PANEL_ADDR_DEFAULT
    target_user = SIP_TARGET_USER_DEFAULT
    sip_user = ""
    src_addr = ""
    sip_password = ""
    subject_label = "Mobile App"
    embedded_device_token = device_token  # may be overridden from embedded SipAccounts

    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = module.get("ModuleId")
        module_settings = _settings_map(module.get("Settings"))
        features = module.get("Features") or []

        if module_id == MODULE_ENTRY_PANEL:
            # panel_addr (SettingId=4) and target_user (SettingId=3)
            panel_addr = module_settings.get(SETTING_PANEL_ADDR, panel_addr)
            target_user = module_settings.get(SETTING_TARGET_USER, target_user)

        if module_id == MODULE_UNIT:
            # Extract Mobile App slots from Features (FeatureId=4)
            for feat in features:
                if not isinstance(feat, dict):
                    continue
                if feat.get("FeatureId") != FEATURE_MOBILE_APP:
                    continue
                feat_settings = _settings_map(feat.get("Settings"))
                enabled = feat_settings.get(SETTING_ENABLED, "true").lower()
                if enabled in {"false", "0", "no"}:
                    continue
                slot_sip_user = feat_settings.get(SETTING_SIP_USER, "").strip()
                slot_src_addr = feat_settings.get(SETTING_SRC_ADDR, "").strip()
                if not slot_sip_user or not slot_src_addr:
                    continue
                slot_label = str(feat.get("AliasName") or feat.get("Name") or slot_sip_user).strip()

                # Pick this slot if it matches the preferred sip_user, or take first
                if not sip_user or (preferred_sip_user and slot_sip_user == preferred_sip_user):
                    sip_user = slot_sip_user
                    src_addr = slot_src_addr
                    subject_label = slot_label

            # SipPassword is in the module-level SipAccounts list
            sip_accs = module.get("SipAccounts") or []
            if isinstance(sip_accs, list):
                for acc in sip_accs:
                    if not isinstance(acc, dict):
                        continue
                    acc_user = str(acc.get("SipUsername", "")).strip()
                    # Match against our chosen sip_user (or just take first)
                    if not sip_password or (sip_user and acc_user == sip_user):
                        pwd = str(acc.get("SipPassword", "")).strip()
                        if pwd:
                            sip_password = pwd
                        tok = str(acc.get("DeviceToken", "")).strip()
                        if tok:
                            embedded_device_token = tok
                        if not src_addr:
                            src_addr = str(acc.get("BptL3Addr", "")).strip()

    # Validate
    missing = [k for k, v in [
        ("sip_user", sip_user),
        ("src_addr", src_addr),
        ("sip_password", sip_password),
        ("keycode", dev_keycode),
    ] if not v]
    if missing:
        raise CameAccessDiscoveryError(
            f"Device {raw_dev.get('Id')}: missing required fields: {', '.join(missing)}"
        )

    return DoorConfig(
        sip_user=sip_user,
        keycode=dev_keycode,
        src_addr=src_addr,
        panel_addr=panel_addr,
        target_user=target_user,
        sip_password=sip_password,
        device_token=embedded_device_token or device_token,
        subject_label=subject_label,
    )
