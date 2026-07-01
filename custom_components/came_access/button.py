"""Button platform for CAME Access – Open Door."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import (
    CameAccessAuthError,
    CameAccessBusyError,
    CameAccessClient,
    CameAccessError,
    CameAccessTimeoutError,
    DoorConfig,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_KEYCODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CameOpenDoorButton(
                client=data["client"],
                door_config=data["door_config"],
                device_id=str(entry.data[CONF_DEVICE_ID]),
                device_name=entry.data.get(CONF_DEVICE_NAME, "CAME XTS7"),
                keycode=entry.data[CONF_KEYCODE],
                entry_id=entry.entry_id,
            )
        ],
        update_before_add=False,
    )


class CameOpenDoorButton(ButtonEntity):
    """Button that sends an OPEN_DOOR command to the XTS7 via SIP."""

    _attr_icon = "mdi:door-open"
    _attr_has_entity_name = True
    _attr_name = "Open Door"

    def __init__(
        self,
        client: CameAccessClient,
        door_config: DoorConfig,
        device_id: str,
        device_name: str,
        keycode: str,
        entry_id: str,
    ) -> None:
        self._client = client
        self._door_config = door_config
        self._device_id = device_id
        self._device_name = device_name
        self._entry_id = entry_id

        self._attr_unique_id = f"came_access_open_door_{device_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_name,
            manufacturer="CAME",
            model=f"XTS7 / BPT ({keycode})",
            configuration_url="https://app.cameconnect.net/",
        )

        # State tracking (exposed as extra_state_attributes)
        self._last_pressed: str | None = None
        self._last_register_status: str | None = None
        self._last_message_status: str | None = None
        self._last_error: str | None = None
        self._last_retries: int = 0

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "last_pressed": self._last_pressed,
            "last_register_status": self._last_register_status,
            "last_message_status": self._last_message_status,
            "last_error": self._last_error,
            "last_retries": self._last_retries,
            "sip_user": self._door_config.sip_user,
            "src_addr": self._door_config.src_addr,
            "panel_addr": self._door_config.panel_addr,
            "target_user": self._door_config.target_user,
            "sip_domain": self._door_config.sip_domain,
            "subject_label": self._door_config.subject_label,
            "proxy_host": self._door_config.proxy_host,
        }

    async def async_press(self) -> None:
        """Handle button press – send the door-open SIP command."""
        self._last_pressed = dt_util.utcnow().isoformat()
        self._last_error = None
        self.async_write_ha_state()

        _LOGGER.debug(
            "Open door pressed for device %s (sip_user=%s, src=%s, dst=%s)",
            self._device_id,
            self._door_config.sip_user,
            self._door_config.src_addr,
            self._door_config.panel_addr,
        )

        try:
            result = await self._client.async_open_door(self._door_config)
        except CameAccessBusyError as exc:
            self._last_error = str(exc)
            self.async_write_ha_state()
            raise HomeAssistantError(
                f"CAME door open failed: unit is busy. "
                "Another call may be in progress on the XTS7."
            ) from exc
        except CameAccessAuthError as exc:
            self._last_error = str(exc)
            self.async_write_ha_state()
            raise HomeAssistantError(
                "CAME door open failed: SIP authentication error. "
                "Check that the Mobile App password is correct in the XTS7 unit."
            ) from exc
        except CameAccessTimeoutError as exc:
            self._last_error = str(exc)
            self.async_write_ha_state()
            raise HomeAssistantError(
                "CAME door open failed: could not reach the SIP proxy. "
                "Check your internet connection."
            ) from exc
        except CameAccessError as exc:
            self._last_error = str(exc)
            self.async_write_ha_state()
            raise HomeAssistantError(f"CAME door open failed: {exc}") from exc
        except Exception as exc:
            self._last_error = str(exc)
            self.async_write_ha_state()
            _LOGGER.exception("Unexpected error during CAME door open")
            raise HomeAssistantError(f"CAME door open unexpected error: {exc}") from exc

        self._last_register_status = result.register_status
        self._last_message_status = result.message_status
        self._last_retries = result.retries_used
        self._last_error = None
        self.async_write_ha_state()

        _LOGGER.info(
            "CAME door open succeeded for %s (retries=%d, register=%s, message=%s)",
            self._device_id,
            result.retries_used,
            result.register_status,
            result.message_status,
        )
