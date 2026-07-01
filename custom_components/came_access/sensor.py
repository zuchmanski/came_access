"""Sensor platform for CAME Access – diagnostic / status sensors."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CameAccessClient, DoorConfig
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_KEYCODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_SENSOR_DESCRIPTIONS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="sip_user",
        name="SIP User",
        icon="mdi:account-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="sip_domain",
        name="SIP Domain",
        icon="mdi:dns",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="proxy_host",
        name="SIP Proxy Host",
        icon="mdi:server-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="src_addr",
        name="BPT Source Address",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="panel_addr",
        name="BPT Panel Address",
        icon="mdi:intercom",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="subject_label",
        name="Mobile App Slot",
        icon="mdi:label",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client: CameAccessClient = data["client"]
    door_config: DoorConfig = data["door_config"]
    device_id = str(entry.data[CONF_DEVICE_ID])
    device_name = entry.data.get(CONF_DEVICE_NAME, "CAME XTS7")
    keycode = entry.data[CONF_KEYCODE]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=device_name,
        manufacturer="CAME",
        model=f"XTS7 / BPT ({keycode})",
        configuration_url="https://app.cameconnect.net/",
    )

    # Build a simple value map from the DoorConfig dataclass fields
    values: dict[str, str] = {
        "sip_user": door_config.sip_user,
        "sip_domain": door_config.sip_domain,
        "proxy_host": door_config.proxy_host,
        "src_addr": door_config.src_addr,
        "panel_addr": door_config.panel_addr,
        "subject_label": door_config.subject_label,
    }

    async_add_entities(
        [
            CameAccessDiagnosticSensor(
                description=desc,
                native_value=values.get(desc.key, "unknown"),
                device_info=device_info,
                device_id=device_id,
            )
            for desc in _SENSOR_DESCRIPTIONS
        ],
        update_before_add=False,
    )


class CameAccessDiagnosticSensor(SensorEntity):
    """A static diagnostic sensor that surfaces one piece of device config."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        native_value: str,
        device_info: DeviceInfo,
        device_id: str,
    ) -> None:
        self.entity_description = description
        self._attr_native_value = native_value
        self._attr_device_info = device_info
        self._attr_unique_id = f"came_access_{device_id}_{description.key}"
