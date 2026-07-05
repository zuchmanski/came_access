"""Config flow for CAME Access integration.

Step 1  – user enters email, password, optional local XTS7 IP
          → integration logs in and discovers devices from the cloud API

Step 2  (only if multiple devices found)
        – user picks the device to control

All discovered SIP/BPT parameters are stored in entry.data so no
re-discovery is needed on every HA restart.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import (
    CameAccessAuthError,
    CameAccessApiError,
    CameAccessClient,
    CameAccessDiscoveryError,
    DiscoveredDevice,
)
from .const import (
    CONF_AUX_OUTPUTS,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TOKEN,
    CONF_KEYCODE,
    CONF_LOCAL_IP,
    CONF_PANEL_ADDR,
    CONF_PASSWORD,
    CONF_PROXY_HOST,
    CONF_SITE_ID,
    CONF_SIP_PASSWORD,
    CONF_SIP_USER,
    CONF_SRC_ADDR,
    CONF_SUBJECT_LABEL,
    CONF_TARGET_USER,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
        vol.Required(CONF_SITE_ID): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Required(CONF_SIP_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_LOCAL_IP, default=""): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }
)


def _device_to_entry_data(username: str, password: str, local_ip: str, dev: DiscoveredDevice) -> dict:
    cfg = dev.door_config
    return {
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
        CONF_LOCAL_IP: local_ip,
        CONF_DEVICE_ID: dev.device_id,
        CONF_SITE_ID: dev.site_id,
        CONF_DEVICE_NAME: dev.name,
        CONF_KEYCODE: cfg.keycode,
        CONF_SIP_USER: cfg.sip_user,
        CONF_SRC_ADDR: cfg.src_addr,
        CONF_PANEL_ADDR: cfg.panel_addr,
        CONF_TARGET_USER: cfg.target_user,
        CONF_SIP_PASSWORD: cfg.sip_password,
        CONF_DEVICE_TOKEN: cfg.device_token,
        CONF_SUBJECT_LABEL: cfg.subject_label,
        CONF_PROXY_HOST: cfg.proxy_host,
        CONF_AUX_OUTPUTS: dev.aux_outputs,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the CAME Access setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._sip_password: str = ""
        self._site_id: str = ""
        self._local_ip: str = ""
        self._discovered: list[DiscoveredDevice] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            local_ip = user_input.get(CONF_LOCAL_IP, "").strip()

            try:
                site_id = int(str(user_input[CONF_SITE_ID]).strip())
            except (ValueError, TypeError):
                errors["base"] = "invalid_site_id"
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_SCHEMA,
                    errors=errors,
                )
            sip_password = user_input[CONF_SIP_PASSWORD]

            # Prevent duplicate entries for the same account + site
            await self.async_set_unique_id(f"{username.lower()}:{site_id}")
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            client = CameAccessClient(session, username, password)

            try:
                await client.async_login()
            except CameAccessAuthError:
                errors["base"] = "invalid_auth"
            except CameAccessApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during CAME Access login")
                errors["base"] = "unknown"
            else:
                try:
                    devices = await client.async_discover_site_devices(site_id, sip_password)
                except CameAccessDiscoveryError as exc:
                    _LOGGER.warning("CAME Access discovery failed: %s", exc)
                    errors["base"] = "no_devices"
                except CameAccessApiError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during CAME Access discovery")
                    errors["base"] = "unknown"
                else:
                    self._username = username
                    self._password = password
                    self._sip_password = sip_password
                    self._site_id = site_id
                    self._local_ip = local_ip
                    self._discovered = devices

                    if len(devices) == 1:
                        return self._create_entry(devices[0])
                    # Multiple devices → let user pick
                    return await self.async_step_pick_device()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_pick_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            chosen_id = int(user_input["device"])
            for dev in self._discovered:
                if dev.device_id == chosen_id:
                    return self._create_entry(dev)
            return self.async_abort(reason="device_not_found")

        options = [
            selector.SelectOptionDict(
                value=str(dev.device_id),
                label=f"{dev.name} (ID {dev.device_id}, site {dev.site_id})",
            )
            for dev in self._discovered
        ]
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    def _create_entry(self, dev: DiscoveredDevice) -> FlowResult:
        data = _device_to_entry_data(self._username, self._password, self._local_ip, dev)
        return self.async_create_entry(
            title=f"CAME Access – {dev.name}",
            data=data,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "CameAccessOptionsFlow":
        return CameAccessOptionsFlow()


class CameAccessOptionsFlow(config_entries.OptionsFlow):
    """Options flow: allow re-discovery to refresh SIP parameters."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            action = user_input.get("action")

            if action == "rediscover":
                return await self._async_rediscover()

            # Just save the options as-is (no changes needed right now)
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="nothing"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="nothing", label="No changes"),
                                selector.SelectOptionDict(
                                    value="rediscover",
                                    label="Re-discover device parameters from cloud API",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def _async_rediscover(self) -> FlowResult:
        """Re-run discovery and update stored SIP parameters."""
        entry = self.config_entry
        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]
        sip_password = entry.data[CONF_SIP_PASSWORD]
        try:
            site_id = int(str(entry.data[CONF_SITE_ID]).strip())
        except (ValueError, TypeError):
            return self.async_abort(reason="invalid_site_id")
        local_ip = entry.data.get(CONF_LOCAL_IP, "")
        target_device_id = int(entry.data[CONF_DEVICE_ID])

        session = async_get_clientsession(self.hass)
        client = CameAccessClient(session, username, password)

        try:
            await client.async_login()
            devices = await client.async_discover_site_devices(site_id, sip_password)
        except CameAccessAuthError:
            return self.async_abort(reason="invalid_auth")
        except (CameAccessApiError, CameAccessDiscoveryError) as exc:
            _LOGGER.warning("Re-discovery failed: %s", exc)
            return self.async_abort(reason="rediscovery_failed")

        # Find our device in the fresh results
        match = next((d for d in devices if d.device_id == target_device_id), None)
        if match is None and devices:
            match = devices[0]  # fallback: take first device

        if match is None:
            return self.async_abort(reason="device_not_found")

        new_data = _device_to_entry_data(username, password, local_ip, match)
        self.hass.config_entries.async_update_entry(entry, data=new_data)
        return self.async_create_entry(data={})
