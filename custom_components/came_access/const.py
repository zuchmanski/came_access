"""Constants for CAME Access integration."""

DOMAIN = "came_access"
PLATFORMS = ["button", "sensor"]

# ─── Cloud API ────────────────────────────────────────────────────────────────
API_BASE = "https://app.cameconnect.net/api"

# OAuth client credentials extracted from the CAME Access Android app.
# These are app-level public credentials, not user credentials.
# Obtained via MITM of the app's initial login request (Authorization: Basic <base64>).
CAME_CLIENT_ID = "068ea42057caaffb41ae47a9447d31e0"
CAME_CLIENT_SECRET = (
    "194b06fbc63b8e4ad690cab9084f930543e84de07dddb087201c969fdd3258c"
    "5b622873846cc5180f017498825c55f7ffe464e5007601b3be00cde648de007"
    "55eefafbdb8f4af38db25f52e1a88c28b39c8d9145dc6aa654b0e16751ee91e"
    "08b7c518de4b424fcf372e07e49799e55ee17a2b68611527e022ecff3264e967885"
)

# Token management
TOKEN_REFRESH_MARGIN = 120  # seconds before expiry to force refresh

# ─── SIP / BPT ───────────────────────────────────────────────────────────────
# Default SIP proxy (from CAME infrastructure).
# Resolved dynamically via /api/evo/v1/checkdomainip, falls back to this.
SIP_PROXY_HOST_DEFAULT = "104.239.174.100"
SIP_PROXY_PORT = 5061        # TLS

# Password for SIP Digest auth is: PREFIX + SipPassword (from API)
SIP_AUTH_PREFIX = "BptX1pM0b1l3"

# xipregister parameters
SIP_APP_NAME = "com.came.myaccess"
SIP_OS_TYPE = "android"
SIP_LANG = "pt-PT"

# Fallback addressing (overridden by auto-discovery)
SIP_TARGET_USER_DEFAULT = "00800000000"
SIP_PANEL_ADDR_DEFAULT = "00e00000"

# ─── Module / Feature / Setting IDs (from API device metadata) ───────────────
MODULE_UNIT = 2           # XTS7 unit module
MODULE_ENTRY_PANEL = 1    # Entry panel module (door + buzzer)

FEATURE_MOBILE_APP = 4    # Mobile App slot feature
FEATURE_OPEN_DOOR = 2     # Open door feature
FEATURE_LIVE_VIEW = 1

SETTING_SIP_USER = 1      # SIP username for mobile slot
SETTING_SRC_ADDR = 2      # BPT L3 source address
SETTING_TARGET_USER = 3   # SIP target user (panel)
SETTING_PANEL_ADDR = 4    # BPT panel address
SETTING_ENABLED = 5       # Slot enabled flag

# ─── Config entry keys (stored in entry.data after discovery) ────────────────
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_LOCAL_IP = "local_ip"           # optional, stored but not required for operation

# Discovered values stored in entry.data:
CONF_SITE_ID = "site_id"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_KEYCODE = "keycode"             # e.g. "7FDF0BAD03DB7B0A"
CONF_SIP_USER = "sip_user"           # e.g. "00700100003"
CONF_SRC_ADDR = "src_addr"           # e.g. "00e70003"
CONF_PANEL_ADDR = "panel_addr"       # e.g. "00e00000"
CONF_TARGET_USER = "target_user"     # e.g. "00800000000"
CONF_SIP_PASSWORD = "sip_password"   # e.g. "IgVbwSVWtrwMHbJ2"
CONF_DEVICE_TOKEN = "device_token"   # FCM push token
CONF_SUBJECT_LABEL = "subject_label" # Mobile App slot label
CONF_PROXY_HOST = "proxy_host"       # resolved SIP proxy IP

# ─── Busy / retry ─────────────────────────────────────────────────────────────
BUSY_RETRY_DELAY = 6     # seconds between retries when unit returns 486
BUSY_MAX_RETRIES = 3     # maximum retries before giving up

# ─── Timeouts ────────────────────────────────────────────────────────────────
SIP_CONNECT_TIMEOUT = 10   # TCP+TLS connect timeout (s)
SIP_RECV_TIMEOUT = 6       # per-message receive timeout (s)
HTTP_TIMEOUT = 20          # REST call timeout (s)
