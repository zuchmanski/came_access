"""Sensor platform for CAME Access – diagnostic / status sensors."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

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

# Poll the runtime diagnostic sensors every 30s (token countdown + last action).
SCAN_INTERVAL = timedelta(seconds=30)


def _last(client: CameAccessClient, attr: str, default="unknown"):
    la = client.last_action
    if la is None:
        return default
    val = getattr(la, attr, default)
    return default if val in (None, "") else val


# (key, name, icon, unit, value_fn) — value_fn reads live state off the client.
_RUNTIME_SENSORS: list[tuple] = [
    ("last_command", "Last Command", "mdi:gesture-tap-button", None,
     lambda c: _last(c, "kind")),
    ("last_result", "Last Command Result", "mdi:check-circle-outline", None,
     lambda c: "unknown" if c.last_action is None else ("success" if c.last_action.success else "failed")),
    ("last_error", "Last Command Error", "mdi:alert-circle-outline", None,
     lambda c: _last(c, "error", default="none")),
    ("last_xipregister", "Last Wake-up (xipregister)", "mdi:bell-ring-outline", None,
     lambda c: _last(c, "xipregister_status")),
    ("last_register", "Last SIP Register", "mdi:login", None,
     lambda c: _last(c, "register_status")),
    ("last_message", "Last SIP Message", "mdi:message-arrow-right-outline", None,
     lambda c: _last(c, "message_status")),
    ("last_retries", "Last Busy Retries", "mdi:reload", None,
     lambda c: 0 if c.last_action is None else c.last_action.retries_used),
    ("last_duration", "Last Command Duration", "mdi:timer-outline", "ms",
     lambda c: None if c.last_action is None else c.last_action.elapsed_ms),
    ("proxy_live", "SIP Proxy (last resolved)", "mdi:server-network", None,
     lambda c: _last(c, "proxy_resolved")),
    ("proxy_stale", "SIP Proxy Stale", "mdi:alert-decagram-outline", None,
     lambda c: "unknown" if c.last_action is None
     else str(bool(c.last_action.proxy_resolved) and c.last_action.proxy_resolved != c.last_action.proxy_host)),
    ("token_expires_in", "Token Expires In", "mdi:timer-lock-outline", "s",
     lambda c: c.token_diagnostics()["expires_in_seconds"]),
    ("token_valid", "Token Valid", "mdi:key-outline", None,
     lambda c: str(c.token_diagnostics()["token_valid"])),
]


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

    entities: list[SensorEntity] = [
        CameAccessDiagnosticSensor(
            description=desc,
            native_value=values.get(desc.key, "unknown"),
            device_info=device_info,
            device_id=device_id,
        )
        for desc in _SENSOR_DESCRIPTIONS
    ]

    entities.extend(
        CameAccessRuntimeSensor(
            key=key,
            name=name,
            icon=icon,
            unit=unit,
            value_fn=value_fn,
            client=client,
            device_info=device_info,
            device_id=device_id,
        )
        for key, name, icon, unit, value_fn in _RUNTIME_SENSORS
    )

    async_add_entities(entities, update_before_add=False)


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
        self._attr_icon = description.icon


class CameAccessRuntimeSensor(SensorEntity):
    """A polled diagnostic sensor reflecting live client state (last command,
    token status, resolved proxy). Value is computed on each read."""

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        key: str,
        name: str,
        icon: str,
        unit: str | None,
        value_fn: Callable[[CameAccessClient], object],
        client: CameAccessClient,
        device_info: DeviceInfo,
        device_id: str,
    ) -> None:
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._value_fn = value_fn
        self._client = client
        self._attr_device_info = device_info
        self._attr_unique_id = f"came_access_{device_id}_{key}"

    @property
    def native_value(self):
        try:
            return self._value_fn(self._client)
        except Exception as exc:  # a diagnostic sensor must never break the platform
            _LOGGER.debug("Error computing sensor %s: %s", self._attr_unique_id, exc)
            return None
