"""Runtime configuration derived from environment variables."""
import logging
import os
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("TITAN_DATA_DIR", "/data")


def _usable_data_dir(path: str) -> str:
    """Return a writable persistent directory or a temporary fallback."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".titan-write-test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return path
    except OSError as exc:
        fallback = tempfile.mkdtemp(prefix="titan-data-")
        logging.getLogger("titan.config").error(
            "data directory %s is unavailable (%s); using %s. Data will not persist.",
            path, exc, fallback,
        )
        return fallback


DATA_DIR = _usable_data_dir(DATA_DIR)
DB_PATH = os.environ.get("TITAN_DB_PATH", os.path.join(DATA_DIR, "titan.db"))
XRAY_CONFIG_PATH = os.environ.get(
    "TITAN_XRAY_CONFIG", os.path.join(DATA_DIR, "xray.json")
)
XRAY_BIN = os.environ.get("XRAY_BIN", "/usr/local/bin/xray")
XRAY_LOG_PATH = os.environ.get("TITAN_XRAY_LOG", os.path.join(DATA_DIR, "xray.log"))
XRAY_EXTERNAL_SUPERVISOR = (os.environ.get("XRAY_EXTERNAL_SUPERVISOR", "0")
                            .strip().lower() in {"1", "true", "yes", "on"})

PUBLIC_PORT = int(os.environ.get("PORT", "8080"))
PANEL_PORT = int(os.environ.get("PANEL_PORT", "10000"))


def _panel_hosts() -> list[str]:
    explicit = (os.environ.get("PANEL_HOST") or "").strip()
    return [explicit or "127.0.0.1"]


PANEL_BIND_HOSTS = _panel_hosts()
PANEL_HOST = PANEL_BIND_HOSTS[0]

# SuperJinX's five loopback listeners, in the same tag/port order.
XRAY_VLESS_WS_PORT = int(os.environ.get("XRAY_VLESS_WS_PORT", "10001"))
XRAY_VLESS_WS_2_PORT = int(os.environ.get(
    "XRAY_VLESS_WS_2_PORT", os.environ.get("XRAY_VLESS_WS2_PORT", "10002")
))
XRAY_TROJAN_WS_PORT = int(os.environ.get("XRAY_TROJAN_WS_PORT", "10003"))
XRAY_VMESS_WS_PORT = int(os.environ.get("XRAY_VMESS_WS_PORT", "10004"))
XRAY_VLESS_HTTPUPGRADE_PORT = int(os.environ.get(
    "XRAY_VLESS_HTTPUPGRADE_PORT", "10005"
))
XRAY_API_PORT = int(os.environ.get("XRAY_API_PORT", "10085"))

SUPPORT_URL = os.environ.get("TITAN_SUPPORT_URL", "https://t.me/Code_Shield").strip()
# Explicit notification-channel target, kept independent from support settings.
TELEGRAM_CHANNEL_URL = os.environ.get(
    "TITAN_TELEGRAM_CHANNEL_URL", "https://t.me/Code_Shield"
).strip()
GITHUB_URL = os.environ.get("TITAN_GITHUB_URL", "https://github.com/mobil2424/rashidiii").strip()

SESSION_COOKIE = "titan_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
LOGIN_SOFT_FAILS = 3
LOGIN_BACKOFF_CAP_SECONDS = 15
LOGIN_MAX_ATTEMPTS = 8
LOGIN_HARD_LOCK_ATTEMPTS = 30
LOGIN_LOCK_SECONDS = 10 * 60

ROLE = os.environ.get("TITAN_ROLE", "main").strip().lower() or "main"
IS_NODE = ROLE == "node"
NODE_SECRET = os.environ.get("TITAN_NODE_SECRET", "")
NODE_TOKEN = os.environ.get("TITAN_NODE_TOKEN", "")
NODE_NAME = os.environ.get("TITAN_NODE_NAME", "").strip()


def _node_public_url() -> str:
    value = (os.environ.get("TITAN_NODE_URL")
             or os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return "https://" + value


NODE_URL = _node_public_url()
MAIN_URL = os.environ.get("TITAN_MAIN_URL", "").strip().rstrip("/")
NODE_SYNC_INTERVAL = int(os.environ.get("TITAN_NODE_SYNC_INTERVAL", "60"))

DASHBOARD_PROFILE_AVATAR_STEMS = (
    "16_TiTaN_Mountain_Wordmark",
    "06_TiTaN_Mountain_Cube",
    "10_TiTaN_Crystal_Cube",
    "11_TiTaN_Nebula_Cube",
    "12_TiTaN_Crown_Emblem",
    "13_TiTaN_Digital_Wave",
)
DASHBOARD_PROFILE_AVATAR_DEFAULT = "dashboard:16_TiTaN_Mountain_Wordmark"

USER_PROFILE_AVATAR_STEMS = (
    "01_TiTaN_Male_Blue_Hoodie",
    "04_TiTaN_Female_Lavender_Hair",
    "05_TiTaN_Male_Cap",
    "06_TiTaN_Female_Bun",
    "10_TiTaN_Female_Beanie",
)

DEFAULT_SETTINGS = {
    "public_domain": "",
    "public_port": 443,
    "admin_avatar": DASHBOARD_PROFILE_AVATAR_DEFAULT,
    "default_transport": "ws",
    "default_fingerprint": "chrome",
    "default_alpn": "http/1.1",
    "sni_override": "",
    "fragment_enabled": False,
    "fragment_length": "10-30",
    "fragment_interval": "10-20",
    "restrict_ips": True,
    "block_ads": True,
    "block_iran_sites": False,
    "backup_enabled": True,
    "backup_interval_hours": 24,
}

BLOCKED_TAGS = {"block-ads", "block-iran", "block-adult", "block-custom"}
VALID_FINGERPRINTS = {
    "chrome", "firefox", "safari", "ios", "android", "edge", "360",
    "qq", "random", "randomized",
}
VALID_ALPNS = {"http/1.1", "h2,http/1.1", "h3,h2,http/1.1", ""}
VALID_PROTOCOLS = {"vless", "vmess", "trojan"}
VALID_TRANSPORTS = {"ws", "httpupgrade"}
VALID_SECURITY = {"tls"}
