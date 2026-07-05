"""Diagnostics support for CAME Access.

Produces a downloadable snapshot (Settings → Devices & Services → CAME Access →
⋮ → Download diagnostics) that includes runtime state useful for debugging the
"works then stops after a while" symptom:

  - OAuth token validity / time-to-expiry / refresh counts
  - the last command's full result (xipregister status, SIP statuses, timing)
  - the stored SIP proxy IP vs a freshly-resolved one (detects a stale IP)
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data

_LOGGER = logging.getLogger(__name__)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import CameAccessClient, DoorConfig
from .const import (
    CONF_PASSWORD,
    CONF_SIP_PASSWORD,
    DOMAIN,
)

TO_REDACT = {CONF_PASSWORD, CONF_SIP_PASSWORD, "sip_password", "device_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client: CameAccessClient | None = data.get("client")
    door_config: DoorConfig | None = data.get("door_config")

    diag: dict[str, Any] = {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "aux_outputs": data.get("aux_outputs", []),
    }

    if client is not None:
        diag["token"] = client.token_diagnostics()
        diag["last_action"] = (
            async_redact_data(asdict(client.last_action), TO_REDACT)
            if client.last_action is not None else None
        )

    if door_config is not None:
        stored_proxy = door_config.proxy_host
        try:
            live_proxy = await client.async_check_proxy(door_config.sip_domain) if client else ""
        except Exception as exc:  # diagnostics must never raise
            _LOGGER.debug("Failed to resolve live proxy: %s", exc)
            live_proxy = f"error: {exc}"
        diag["sip"] = {
            "sip_user": door_config.sip_user,
            "sip_domain": door_config.sip_domain,
            "src_addr": door_config.src_addr,
            "panel_addr": door_config.panel_addr,
            "target_user": door_config.target_user,
            "subject_label": door_config.subject_label,
            "has_device_token": bool(door_config.device_token),
            "proxy_host_stored": stored_proxy,
            "proxy_host_live": live_proxy,
            "proxy_stale": bool(live_proxy) and live_proxy != stored_proxy,
        }

    return diag
