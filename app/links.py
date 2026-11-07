"""Build SuperJinX-compatible VLESS, VMess and Trojan links/subscriptions."""
import base64
import json
import time
from urllib.parse import quote

from . import genpaths

# The active customer-facing matrix mirrors SuperJinX: VLESS WS/HTTPUpgrade,
# VMess WS and Trojan WS. VLESS WS has two separately routable profiles.
SERVED_TRANSPORTS = {
    "vless": {"ws", "httpupgrade"},
    "vmess": {"ws"},
    "trojan": {"ws"},
}
EARLY_DATA = "?ed=2560"
PROFILES = {
    "ws": {"path_key": "vless_ws", "transport": "ws", "protocol": "vless",
           "fingerprint": "chrome"},
    "ws2": {"path_key": "vless_ws_2", "transport": "ws", "protocol": "vless",
            "fingerprint": "firefox"},
    "httpupgrade": {"path_key": "vless_httpupgrade", "transport": "httpupgrade",
                    "protocol": "vless", "fingerprint": "ios"},
    "vmess-ws": {"path_key": "vmess_ws", "transport": "ws", "protocol": "vmess",
                  "fingerprint": "edge"},
    "trojan-ws": {"path_key": "trojan_ws", "transport": "ws", "protocol": "trojan",
                  "fingerprint": "safari"},
}


def configuration_name(user: dict, server_flag: str = "🌐") -> str:
    """Return the client-facing config name for its user and target server."""
    username = str(user.get("name") or "User").strip() or "User"
    flag = str(server_flag or "🌐").strip() or "🌐"
    return f"TiTaN-{username} {flag}"


def volume_text(used_bytes: int, quota_bytes: int) -> str:
    quota = quota_bytes or 0
    if 0 < quota < 1024**3:
        return f"{used_bytes / 1024**2:.2f}/{quota / 1024**2:.0f}MB"
    return f"{used_bytes / 1024**3:.2f}/{quota / 1024**3:g}GB"


def _transport_for(user: dict, settings: dict) -> str:
    protocol = (user.get("protocol") or "vless").lower()
    if protocol not in SERVED_TRANSPORTS:
        protocol = "vless"
    requested = (user.get("transport")
                 or settings.get("default_transport", "ws") or "ws").lower()
    allowed = SERVED_TRANSPORTS[protocol]
    return requested if requested in allowed else "ws"


def _fragment_params(settings: dict) -> dict:
    if not settings.get("fragment_enabled"):
        return {}
    return {
        "fp_len": settings.get("fragment_length", "10-30"),
        "fp_int": settings.get("fragment_interval", "10-20"),
    }


def _profile_key(protocol: str, transport: str, user: dict) -> str:
    explicit = str(user.get("_profile_key") or "").lower()
    if explicit in PROFILES:
        profile = PROFILES[explicit]
        if profile["protocol"] == protocol and profile["transport"] == transport:
            return explicit
    if protocol == "vless" and transport == "ws":
        return "ws2" if (user.get("fingerprint") or "").lower() == "firefox" else "ws"
    if protocol == "vless" and transport == "httpupgrade":
        return "httpupgrade"
    if protocol == "vmess":
        return "vmess-ws"
    if protocol == "trojan":
        return "trojan-ws"
    return "ws"


def _path_for(protocol: str, transport: str, user: dict | None = None) -> str:
    paths = genpaths.get_paths()
    profile = PROFILES[_profile_key(protocol, transport, user or {})]
    return paths[profile["path_key"]] + EARLY_DATA


def _fingerprint_for(protocol: str, transport: str, user: dict, settings: dict) -> str:
    return (user.get("_profile_fingerprint") or user.get("fingerprint")
            or settings.get("default_fingerprint")
            or PROFILES[_profile_key(protocol, transport, user)]["fingerprint"])


def _alpn_for(user: dict, settings: dict) -> str:
    if user.get("_profile_alpn") is not None:
        return user["_profile_alpn"]
    value = user.get("alpn")
    return settings.get("default_alpn", "http/1.1") if value is None else value


def _host_params(host: str, path: str, sni: str, fp: str, alpn: str,
                 transport: str, settings: dict) -> str:
    parts = [
        f"type={quote(transport, safe='')}",
        f"host={quote(host, safe='')}",
        f"path={quote(path, safe='/')}",
        f"sni={quote(sni, safe='')}",
        f"fp={quote(fp, safe='')}",
    ]
    if alpn:
        parts.append(f"alpn={quote(alpn, safe=',/')}")
    for key, value in _fragment_params(settings).items():
        parts.append(f"{key}={quote(str(value), safe='')}")
    return "&".join(parts)


def build_vless_link(host: str, port: int, user: dict, settings: dict,
                     server_flag: str = "🌐") -> str:
    uuid = user["uuid"]
    transport = _transport_for(user, settings)
    fingerprint = _fingerprint_for("vless", transport, user, settings)
    alpn = _alpn_for(user, settings)
    sni = settings.get("sni_override") or host
    name = quote(configuration_name(user, server_flag), safe="")
    params = _host_params(
        host, _path_for("vless", transport, user), sni, fingerprint, alpn,
        transport, settings,
    )
    return (
        f"vless://{uuid}@{host}:{port}?encryption=none&security=tls&"
        f"{params}#{name}"
    )


def build_vmess_link(host: str, port: int, user: dict, settings: dict,
                     server_flag: str = "🌐") -> str:
    transport = _transport_for(user, settings)
    fingerprint = _fingerprint_for("vmess", transport, user, settings)
    alpn = _alpn_for(user, settings)
    sni = settings.get("sni_override") or host
    config_data = {
        "v": "2",
        "ps": configuration_name(user, server_flag),
        "add": host,
        "port": str(port),
        "id": user["uuid"],
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": host,
        "path": _path_for("vmess", "ws", user),
        "tls": "tls",
        "sni": sni,
        "alpn": alpn,
        "fp": fingerprint,
    }
    encoded = base64.b64encode(
        json.dumps(config_data, separators=(",", ":")).encode()
    ).decode()
    return "vmess://" + encoded


def build_trojan_link(host: str, port: int, user: dict, settings: dict,
                      server_flag: str = "🌐") -> str:
    password = quote(user["uuid"], safe="")
    sni = quote(settings.get("sni_override") or host, safe="")
    alpn = quote(_alpn_for(user, settings), safe=",/")
    fingerprint = quote(_fingerprint_for("trojan", "ws", user, settings), safe="")
    path = quote(_path_for("trojan", "ws", user), safe="/")
    name = quote(configuration_name(user, server_flag), safe="")
    return (
        f"trojan://{password}@{host}:{port}?security=tls&type=ws&"
        f"host={quote(host, safe='')}&path={path}&sni={sni}&alpn={alpn}&fp={fingerprint}#{name}"
    )


def build_links(host: str, port: int, user: dict, settings: dict,
                server_pub: str = "", server_flag: str = "🌐") -> dict:
    """Return the primary link and status metadata for one TiTaN user."""
    protocol = (user.get("protocol") or "vless").lower()
    if protocol not in SERVED_TRANSPORTS:
        user = {**user, "protocol": "vless", "transport": "ws", "security": "tls"}
        protocol = "vless"
    if protocol == "vless":
        main_link = build_vless_link(host, port, user, settings, server_flag)
    elif protocol == "vmess":
        main_link = build_vmess_link(host, port, user, settings, server_flag)
    else:
        main_link = build_trojan_link(host, port, user, settings, server_flag)

    used = (user.get("used_up") or 0) + (user.get("used_down") or 0)
    expiry = ""
    if user.get("expire_at"):
        expiry = f"{max(0, int((user['expire_at'] - time.time()) // 86400))}d"
    remark = (
        f"TiTaN {user.get('name', 'User')} | "
        f"{volume_text(used, user.get('quota_bytes') or 0)} | {expiry or '∞'}"
    )
    dummy_path = quote(genpaths.get_paths()["vless_ws"], safe="/")
    dummy = (
        "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443?"
        f"encryption=none&security=none&type=ws&host=127.0.0.1&path={dummy_path}#{quote(remark)}"
    )
    info = [{"remark": remark, "link": dummy, "kind": "status"}]
    return {"main": main_link, "all": [main_link], "info": info}


def subscription_text(links: list[str]) -> str:
    return base64.b64encode("\n".join(links).encode()).decode()
