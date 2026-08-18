# CAME Access – Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/LucaCraft89/came_access.svg)](https://github.com/LucaCraft89/came_access/releases)
[![GitHub License](https://img.shields.io/github/license/LucaCraft89/came_access.svg)](LICENSE)
[![HA Integration](https://img.shields.io/badge/Home%20Assistant-Integration-blue.svg)](https://www.home-assistant.io/)

A custom Home Assistant integration for controlling **CAME XTS7 / BPT** intercom and gate units via the CAME Access cloud infrastructure.

Sends a SIP `MESSAGE` command over TLS directly to the CAME SIP proxy to trigger door/gate opening and **AUX outputs** — the same protocol used by the official CAME Access mobile app.

---

## Features

- **Open door / gate** from Home Assistant with a single button press
- **AUX outputs** — one button per configured AUX output on the entry panel (Aux 1–N, e.g. gate, lights), each sending a BPT `AUX_COMMAND` over SIP
- **Discovery** of all SIP and BPT parameters via the cloud API's Bearer-only `plants` endpoint (SIP username, BPT addresses, AUX outputs, SIP proxy host)
- **Transparent OAuth2** login with automatic token refresh
- **Best-effort cloud wake-up** (`xipregister`) before each SIP command — soft-fails when no device token is available, SIP delivery works regardless
- **486 Busy handling** — if the unit is in a call, the integration retries automatically (configurable retries + delay)
- **Diagnostic sensors** — static SIP parameters plus live state (last command, SIP statuses, token expiry, stale-proxy detection), and a downloadable diagnostics snapshot
- **Fresh SIP proxy per command** — the proxy IP is re-resolved before every command so a CAME-side address rotation can't silently break the unit
- **Re-discovery option** in the integration options to refresh all parameters after a Mobile App slot reassignment
- **Local SIP** used to send the open command to reduce the delay

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
        ├── brand/
        ├── button.py
        ├── config_flow.py
        ├── const.py
        ├── diagnostics.py
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
- Your **Site ID** (from the CAME Access web app URL) and your Mobile App slot's **SIP password** — see [Configuration](#configuration)

---

## Configuration

You will be asked for:

| Field | Description |
|---|---|
| **Email address** | Your CAME Access account email |
| **Password** | Your CAME Access account password |
| **Site ID** | The numeric site ID from the CAME Access web app (the number in the `cameconnect.net` site URL) |
| **SIP password** | The SIP password for your Mobile App slot (see below) |
| **Local IP** *(optional)* | LAN IP of the XTS7 unit — stored for reference only, not used for door-open |

Once you submit, the integration logs in to the CAME Access cloud API and, for the given site, discovers via the Bearer-only `/api/evo/v1/sites/{id}/plants` endpoint:

- The SIP username and BPT source address for **your** Mobile App slot (matched to your account email)
- The BPT panel and target addresses
- The list of **AUX outputs** configured on the entry panel
- The SIP proxy host for your unit

If the site has multiple XTS7 units you will be asked to pick one.

### Why Site ID and SIP password are entered manually

The CAME Access cloud API **no longer returns the SIP password or an FCM device token** for third-party callers, and the site-list endpoint requires a device token that is not available. The integration therefore uses the web dashboard's `plants` endpoint (which needs only the Bearer token) and asks you to supply:

- **Site ID** — open your site in the CAME Access web app at [cameconnect.net](https://cameconnect.net/); the number in the URL is the Site ID.
- **SIP password** — the password for your Mobile App slot. If you don't know it, it can be recovered from the app's traffic (e.g. via [mitmproxy](https://mitmproxy.org/)); the digest password used on the wire is `BptX1pM0b1l3` + this value.

> **Note:** a device (FCM) token is *not* required. It is only used by the optional `xipregister` wake-up, which fails soft — door-open and AUX commands are delivered by SIP regardless.

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

### Buttons

| Entity | Description |
|---|---|
| `button.<device_name>_open_door` | Sends the `OPEN_DOOR` BPT command via SIP |
| `button.<device_name>_<aux_label>` | One per AUX output on the entry panel — sends the `AUX_COMMAND` BPT command via SIP. Named after the AUX alias set in the CAME Access app (e.g. *Brama*), falling back to `Aux N`. Icons are mapped from the app icon (gate → `mdi:gate`, light → `mdi:lightbulb`, …). |

Extra state attributes exposed on the **Open Door** button:

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

Each **AUX** button exposes: `aux_code` (the BPT AUX index that is sent), `last_pressed`, `last_message_status`, and `last_error`.

AUX buttons whose label is just the generic `Aux N` are **disabled by default** — enable them from the entity settings if you actually use those outputs.

### Diagnostic Sensors

Six static sensors surface the resolved SIP parameters (hidden by default, visible by enabling them):

| Sensor | Description |
|---|---|
| `SIP User` | SIP username for this Mobile App slot |
| `SIP Domain` | `<keycode>.xip.cameconnect.net` |
| `SIP Proxy Host` | Resolved IP of the CAME SIP proxy |
| `BPT Source Address` | L3 source address of this slot |
| `BPT Panel Address` | L3 address of the entry panel |
| `Mobile App Slot` | Human-readable label of the active slot |

In addition, live sensors (polled every 30 s) reflect the runtime state of the last command and the OAuth session — useful for spotting "worked, then stopped after a while" issues:

| Sensor | Description |
|---|---|
| `Last Command` | Type of the last command (`open_door` / `aux N`) |
| `Last Command Result` | `success` / `failed` |
| `Last Command Error` | Error from the last command, or `none` |
| `Last Wake-up (xipregister)` | Wake-up status (`200 OK`, `skipped (no device token)`, …) |
| `Last SIP Register` | Status line from the REGISTER step |
| `Last SIP Message` | Status line from the MESSAGE step |
| `Last Busy Retries` | How many 486-Busy retries were used |
| `Last Command Duration` | Wall-clock duration of the last command, in ms |
| `SIP Proxy (last resolved)` | Freshly-resolved SIP proxy IP |
| `SIP Proxy Stale` | Whether the stored proxy IP differs from the freshly-resolved one |
| `Token Expires In` | Seconds until the OAuth access token expires |
| `Token Valid` | Whether the current access token is still valid |

---

## Troubleshooting

### "Unit is busy"

The XTS7 returned SIP **486 Busy Here** — another call is active on the unit (someone is using the intercom). The integration retries up to 3 times with a 6-second delay between attempts. If still busy after all retries, HA will raise a persistent notification. Wait for the active call to finish and try again.

### "SIP authentication error"

SIP digest authentication failed. Most likely causes:

- The **SIP password** entered during setup is wrong (this is the most common cause — it is entered manually)
- The Mobile App slot was **disabled or removed** from the XTS7 unit

Fix: remove and re-add the integration with the correct **SIP password**. If the slot addresses changed, use **Settings → Devices & Services → CAME Access → Configure → Re-discover device parameters from cloud API** to refresh them.

### "Could not reach the SIP proxy"

TLS connection to the CAME SIP proxy failed. Check:

- Internet connectivity from the HA host
- Outbound **TCP port 5061** is not blocked by a firewall or the ISP

### Button does nothing / no error shown

Download a diagnostics snapshot first — **Settings → Devices & Services → CAME Access → ⋮ → Download diagnostics**. It contains (with passwords and tokens redacted) the OAuth token state, the full result of the last command, and the stored SIP proxy IP vs a freshly-resolved one.

For the full SIP exchange, enable debug logging:

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
2. **`/api/evo/v1/sites/{id}/plants`** — Bearer-only endpoint that returns the module/feature tree for the site. The integration extracts the Mobile App slot (matched to your account email) for the SIP username and BPT source address, the entry-panel addresses, and the list of AUX outputs. The SIP password is supplied by you (the API no longer returns it).
3. **`/api/evo/v1/checkdomainip`** — resolves which IP the SIP proxy runs on for this unit's domain. This runs before **every** command (not just at setup) and the fresh IP is used for the connection — CAME rotates proxy addresses, so a cached IP would otherwise go stale and stop commands from arriving
4. **`/api/push/xipregister`** — best-effort signal to wake the unit and register with the proxy (skipped/soft-failed when no device token is available)
5. **SIP REGISTER + MESSAGE over TLS (port 5061)** — authenticates with Digest MD5 and delivers the `OPEN_DOOR` or `AUX_COMMAND` XML payload

The SIP digest password is `BptX1pM0b1l3` + your SIP password. The AUX command carries `<type>AUX_COMMAND</type>` with an `<aux_code>` (the Aux index, 1–N) plus the source/panel BPT addresses. The `Subject` header encodes the source/destination BPT addresses and slot label in the format used by the app.

> The legacy discovery chain (`/sipaccounts` → `/sites/{id}/devices?dt=…`) is retained in `api.py` but is no longer used by the config flow, since the API stopped returning a usable device token and SIP password to third-party callers.

---

## To-Do (seeking help from community)

1. [x] **Add** AUX support — one button per AUX output, sending a BPT `AUX_COMMAND` over SIP (tested on an XTS7 X1)
2. [ ] **Test** on other Units like the 5 inch variant (also no way to test it)
3. [ ] **Auto-discover the Site ID** — currently entered manually; the web dashboard's site-list call would remove this step

---

## Background & Credits

This integration was born out of the need to control **CAME XTS7 X1 Wi-Fi** units (and similar CAME Access / old VideoEntry compatible hardware) from Home Assistant — including older installations, condominium units not connected to the internet, or units that are CAME Access capable but have never been cloud-paired.

The protocol was reverse engineered from the official **CAME Access** app using [mitmproxy](https://mitmproxy.org/) and Frida to intercept the full API and SIP flow.

SIP logic and overall integration structure were inspired by the excellent [**came_connect**](https://github.com/sdeagh/came_connect) integration by [@sdeagh](https://github.com/sdeagh) — go give it a star if you have a standard CAME Connect installation.

---

## License

[MIT](LICENSE)
