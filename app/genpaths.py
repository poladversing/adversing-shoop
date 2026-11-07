"""Persist SuperJinX-compatible, per-install Xray paths and Nginx routes."""
import json
import os
import re
import tempfile
import uuid

from . import config

PATHS_FILE = os.path.join(config.DATA_DIR, "paths.json")
NGINX_PATHS_FILE = os.path.join(config.DATA_DIR, "nginx-paths.conf")

# The five reference profiles, in their public route order.
_PATH_PREFIXES = {
    "vless_ws": "/ws/",
    "vless_ws_2": "/stream/",
    "trojan_ws": "/live/",
    "vmess_ws": "/gw/",
    "vless_httpupgrade": "/cdn/",
}
# Retain active TiTaN paths on upgrade so existing WS links keep working. The
# old VLESS XHTTP path is reused for HTTPUpgrade; VMess XHTTP is migrated to WS.
_ACCEPTED_PREFIXES = {
    "vless_ws": ("/ws/", "/vl-ws/"),
    "vless_ws_2": ("/stream/",),
    "trojan_ws": ("/live/", "/tr-ws/"),
    "vmess_ws": ("/gw/", "/vm-ws/"),
    "vless_httpupgrade": ("/cdn/", "/xhttp/", "/hup/"),
}
_SUFFIX_RE = r"(?:[0-9a-f]{16}|[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"


def _valid_path(key: str, value) -> bool:
    if not isinstance(value, str):
        return False
    return any(
        re.fullmatch(re.escape(prefix) + _SUFFIX_RE, value)
        for prefix in _ACCEPTED_PREFIXES[key]
    )


def _new_path(prefix: str) -> str:
    return prefix + str(uuid.uuid4())


def _new_paths() -> dict[str, str]:
    return {key: _new_path(prefix) for key, prefix in _PATH_PREFIXES.items()}


def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".titan-paths-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_paths(paths: dict[str, str]) -> None:
    _atomic_write(PATHS_FILE, json.dumps(paths, indent=2, sort_keys=True) + "\n")


def _read_saved() -> dict:
    try:
        with open(PATHS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _load_or_create() -> dict[str, str]:
    saved = _read_saved()
    paths: dict[str, str] = {}
    used: set[str] = set()
    for key, prefix in _PATH_PREFIXES.items():
        # Preserve the same-name TiTaN key, or adopt its former XHTTP key as the
        # new VLESS HTTPUpgrade route. A new VLESS WS-2 route is added once.
        candidates = [key]
        if key == "vless_httpupgrade":
            candidates += ["xhttp", "httpupgrade"]
        if key == "vless_ws_2":
            candidates += ["vless_ws2"]
        value = next((saved.get(candidate) for candidate in candidates
                      if _valid_path(key, saved.get(candidate))
                      and saved.get(candidate) not in used), None)
        paths[key] = value or _new_path(prefix)
        used.add(paths[key])

    if paths != saved:
        _write_paths(paths)
    return paths


def _http_location(path: str, port: int) -> str:
    """The reference's unbuffered HTTP/1.1 Upgrade proxy for WS and HU."""
    return f"""location = {path} {{
    proxy_pass http://127.0.0.1:{port};
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection \"upgrade\";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    proxy_request_buffering off;
    gzip off;
    proxy_read_timeout 1h;
    proxy_send_timeout 1h;
    proxy_socket_keepalive on;
}}"""


def _write_nginx_include(paths: dict[str, str]) -> None:
    locations = [
        _http_location(paths["vless_ws"], config.XRAY_VLESS_WS_PORT),
        _http_location(paths["vless_ws_2"], config.XRAY_VLESS_WS_2_PORT),
        _http_location(paths["trojan_ws"], config.XRAY_TROJAN_WS_PORT),
        _http_location(paths["vmess_ws"], config.XRAY_VMESS_WS_PORT),
        _http_location(paths["vless_httpupgrade"], config.XRAY_VLESS_HTTPUPGRADE_PORT),
    ]
    _atomic_write(NGINX_PATHS_FILE, "\n\n".join(locations) + "\n")


def get_paths() -> dict[str, str]:
    """Return the persisted map and keep the generated Nginx include in sync."""
    paths = _load_or_create()
    _write_nginx_include(paths)
    return paths


def set_paths(data: dict) -> tuple[dict[str, str], bool]:
    """Adopt an authenticated main panel's path map on a remote TiTaN node."""
    if not isinstance(data, dict) or any(
        not _valid_path(key, data.get(key)) for key in _PATH_PREFIXES
    ):
        raise ValueError("invalid-path-map")
    paths = {key: data[key] for key in _PATH_PREFIXES}
    if len(set(paths.values())) != len(paths):
        raise ValueError("invalid-path-map")
    current = _read_saved()
    changed = any(current.get(key) != paths[key] for key in paths)
    if changed:
        _write_paths(paths)
    _write_nginx_include(paths)
    return paths, changed


def main() -> None:
    paths = get_paths()
    print(f"Saved transport paths to {PATHS_FILE}")
    print(f"Generated Nginx locations in {NGINX_PATHS_FILE}")
    for key, path in paths.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    main()
