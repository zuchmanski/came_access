# CAME Access – Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/LucaCraft89/came_access.svg)](https://github.com/LucaCraft89/came_access/releases)
[![GitHub License](https://img.shields.io/github/license/LucaCraft89/came_access.svg)](LICENSE)
[![HA Integration](https://img.shields.io/badge/Home%20Assistant-Integration-blue.svg)](https://www.home-assistant.io/)

A custom Home Assistant integration for controlling **CAME XTS7 / BPT** intercom and gate units via the CAME Access cloud infrastructure.

Sends a SIP `MESSAGE` command over TLS directly to the CAME SIP proxy to trigger door/gate opening — the same protocol used by the official CAME Access mobile app.

---

## Features

- **Open door / gate** from Home Assistant with a single button press
- **Auto-discovery** of all SIP and BPT parameters from the cloud API — no manual digging required
- **Transparent OAuth2** login with automatic token refresh
- **Cloud wake-up** (`xipregister`) before each SIP command so the XTS7 is ready to receive
- **486 Busy handling** — if the unit is in a call, the integration retries automatically (configurable retries + delay)
- **Diagnostic sensors** that surface the resolved SIP parameters for easy troubleshooting
- **Re-discovery option** in the integration options to refresh all parameters after a password change or Mobile App slot reassignment

---

## Installation

### HACS (recommended)

1. In Home Assistant, go to **HACS → Integrations**
2. Click ⋮ (top-right menu) → **Custom repositories**
3. Paste the URL below and set the category to **Integration**, then click **Add**

   ```
   https://github.com/LucaCraft89/came_access
   ```

4. Search for **CAME Access** in HACS and click **Download**
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration** and search for **CAME Access**

### Manual

Copy the `custom_components/came_access/` folder from this repo into your HA config directory:

```
config/
└── custom_components/
    └── came_access/
        ├── __init__.py
        ├── api.py
        ├── button.py
        ├── config_flow.py
        ├── const.py
        ├── manifest.json
        ├── sensor.py
        └── translations/
            └── en.json
```

Restart Home Assistant, then add the integration via **Settings → Devices & Services → Add Integration → CAME Access**.

---

## Requirements

- Home Assistant 2024.1 or newer
- A CAME Access account (same email/password as the mobile app)
- The Mobile App slot must be **activated** on the XTS7 unit — done once via the CAME Access app or the XTS7 web interface

---

## Configuration

You will be asked for:

| Field | Description |
|---|---|
| **Email address** | Your CAME Access account email |
| **Password** | Your CAME Access account password |
| **Local IP** *(optional)* | LAN IP of the XTS7 unit — stored for reference only, not used for door-open |

The integration logs in to the CAME Access cloud API and automatically discovers:

- The SIP username, domain, and password for your Mobile App slot
- The BPT source and panel addresses
- The SIP proxy host for your unit
- The FCM device token needed to wake the unit before calling

If your account has multiple XTS7 units you will be asked to pick one.

**Nothing else to configure.** No mitmproxy, no app extraction, no manual credential hunting.

---

## Obtaining a CAME Access Account

If you do not have one yet:

1. Download the **CAME Access** app ([Android](https://play.google.com/store/apps/details?id=com.came.myaccess) / [iOS](https://apps.apple.com/app/came-access/id1448040818))
2. Register with your email address
3. Ask the XTS7 owner/installer to send you an **invitation** from the unit's management page or from the app
4. Accept the invitation — this activates your Mobile App slot on the unit
5. Use those same credentials in the integration setup

The integration uses the same OAuth2 client embedded in the official app and the same REST API. The app does not need to remain installed after initial setup.

---

## Entities

### Button

| Entity | Description |
|---|---|
| `button.<device_name>_open_door` | Sends the `OPEN_DOOR` BPT command via SIP |

Extra state attributes exposed on the button:

| Attribute | Description |
|---|---|
| `last_pressed` | UTC timestamp of last press |
| `last_register_status` | SIP status line from the REGISTER step |
| `last_message_status` | SIP status line from the MESSAGE step |
| `last_retries` | How many 486-Busy retries were needed (0 = first attempt succeeded) |
| `last_error` | Error from the last failed press, or `null` |
| `sip_user` | SIP username in use |
| `sip_domain` | SIP domain in use |
| `src_addr` | BPT L3 source address |
| `panel_addr` | BPT panel address |
| `subject_label` | Mobile App slot label |
| `proxy_host` | Resolved SIP proxy IP |

### Diagnostic Sensors

Six diagnostic sensors are created under the device card (hidden by default, visible by enabling them):

| Sensor | Description |
|---|---|
| `SIP User` | SIP username for this Mobile App slot |
| `SIP Domain` | `<keycode>.xip.cameconnect.net` |
| `SIP Proxy Host` | Resolved IP of the CAME SIP proxy |
| `BPT Source Address` | L3 source address of this slot |
| `BPT Panel Address` | L3 address of the entry panel |
| `Mobile App Slot` | Human-readable label of the active slot |

---

## Troubleshooting

### "Unit is busy"

The XTS7 returned SIP **486 Busy Here** — another call is active on the unit (someone is using the intercom). The integration retries up to 3 times with a 6-second delay between attempts. If still busy after all retries, HA will raise a persistent notification. Wait for the active call to finish and try again.

### "SIP authentication error"

SIP digest authentication failed. Most likely causes:

- The Mobile App slot was **disabled or removed** from the XTS7 unit
- The CAME Access account **password was changed**

Fix: go to **Settings → Devices & Services → CAME Access → Configure → Re-discover device parameters from cloud API**.

### "Could not reach the SIP proxy"

TLS connection to the CAME SIP proxy failed. Check:

- Internet connectivity from the HA host
- Outbound **TCP port 5061** is not blocked by a firewall or the ISP

### Button does nothing / no error shown

Enable debug logging to see the full SIP exchange:

```yaml
# configuration.yaml
logger:
  default: warning
  logs:
    custom_components.came_access: debug
```

After pressing the button, look for this sequence in the log:

```
xipregister OK for sip_user=007…
REGISTER r2: SIP/2.0 200 OK
MESSAGE r2: SIP/2.0 200 OK
CAME door open succeeded for … (retries=0)
```

- `REGISTER r2` anything other than `200 OK` → SIP auth problem, run re-discovery
- `MESSAGE r1: SIP/2.0 486 Busy Here` → unit is in active use, retries will follow
- No log output at all → the button press is not reaching the integration, check HA logs for import errors

---

## Technical Notes

The integration implements the same protocol as the CAME Access mobile app:

1. **OAuth2 password grant** against `https://app.cameconnect.net/api/oauth/token`
2. **`/api/evo/v1/sipaccounts`** — resolves device token and keycode for this account
3. **`/api/evo/v1/sites/{id}/devices`** — retrieves module/feature trees to extract the Mobile App slot SIP username, BPT addresses, and SIP password
4. **`/api/evo/v1/checkdomainip`** — resolves which IP the SIP proxy runs on for this unit's domain
5. **`/api/push/xipregister`** — signals the unit to wake up and register with the proxy
6. **SIP REGISTER + MESSAGE over TLS (port 5061)** — authenticates with Digest MD5 and delivers the `OPEN_DOOR` XML payload

The SIP digest password is `BptX1pM0b1l3` + the raw SIP password returned by the API. The `Subject` header encodes the source/destination BPT addresses and slot label in the format used by the app.

---

## Background & Credits

This integration was born out of the need to control **CAME XTS7 X1 Wi-Fi** units (and similar CAME Access / old VideoEntry compatible hardware) from Home Assistant — including older installations, condominium units not connected to the internet, or units that are CAME Access capable but have never been cloud-paired.

The protocol was reverse engineered from the official **CAME Access** app using [mitmproxy](https://mitmproxy.org/) and Frida to intercept the full API and SIP flow.

SIP logic and overall integration structure were inspired by the excellent [**came_connect**](https://github.com/sdeagh/came_connect) integration by [@sdeagh](https://github.com/sdeagh) — go give it a star if you have a standard CAME Connect installation.

---

## License

[MIT](LICENSE)
