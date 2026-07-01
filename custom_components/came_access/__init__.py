"""CAME Access Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CameAccessClient, DoorConfig
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_TOKEN,
    CONF_KEYCODE,
    CONF_PANEL_ADDR,
    CONF_PASSWORD,
    CONF_PROXY_HOST,
    CONF_SIP_PASSWORD,
    CONF_SIP_USER,
    CONF_SRC_ADDR,
    CONF_SUBJECT_LABEL,
    CONF_TARGET_USER,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
    SIP_PROXY_HOST_DEFAULT,
    SIP_PROXY_PORT,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CAME Access from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    client = CameAccessClient(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    # Authenticate eagerly so we surface bad-credential errors at setup time
    # rather than silently failing when the button is first pressed.
    try:
        await client.async_login()
    except Exception as exc:
        _LOGGER.error("CAME Access login failed during setup: %s", exc)
        # Don't abort – we'll retry on the next press. HA will mark the entry
        # as having a setup error via the standard exception handling.
        raise

    # Reconstruct the DoorConfig from stored data (no re-discovery needed)
    door_config = DoorConfig(
        sip_user=entry.data[CONF_SIP_USER],
        keycode=entry.data[CONF_KEYCODE],
        src_addr=entry.data[CONF_SRC_ADDR],
        panel_addr=entry.data[CONF_PANEL_ADDR],
        target_user=entry.data[CONF_TARGET_USER],
        sip_password=entry.data[CONF_SIP_PASSWORD],
        device_token=entry.data[CONF_DEVICE_TOKEN],
        subject_label=entry.data[CONF_SUBJECT_LABEL],
        proxy_host=entry.data.get(CONF_PROXY_HOST, SIP_PROXY_HOST_DEFAULT),
        proxy_port=SIP_PROXY_PORT,
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "door_config": door_config,
        "device_id": entry.data[CONF_DEVICE_ID],
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when options change (e.g. after re-discovery)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    return unload_ok
