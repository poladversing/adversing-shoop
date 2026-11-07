"""Acceptance tests for the SuperJinX-style loopback Xray/HTTPUpgrade matrix."""
import base64
import json
import uuid
from pathlib import Path

import pytest

from app import config, db, genpaths, xray

ORIGIN = {"Origin": "http://testserver"}
XRAY_INTERNAL_PORTS = set(range(10001, 10006)) | {10085}


@pytest.mark.parametrize(
    ("protocol", "transport"),
    [
        ("vless", "ws"),
        ("vless", "httpupgrade"),
        ("vmess", "ws"),
        ("trojan", "ws"),
    ],
)
def test_supported_protocol_transport_links_are_generated(admin, protocol, transport):
    response = admin.post("/api/users", headers=ORIGIN, json={
        "name": f"{protocol}-{transport}",
        "protocol": protocol,
        "transport": transport,
    })
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert (user["protocol"], user["transport"], user["security"]) == (
        protocol, transport, "tls"
    )
    assert user["main_link"].startswith(f"{protocol}://")
    if protocol == "vmess":
        encoded = user["main_link"].split("://", 1)[1]
        payload = json.loads(base64.b64decode(encoded + "=="))
        assert payload["net"] == "ws" and payload["tls"] == "tls"
        assert "?ed=2560" in payload["path"]
        assert 1 <= int(payload["port"]) <= 65535
        assert int(payload["port"]) not in XRAY_INTERNAL_PORTS
    else:
        assert f"type={transport}" in user["main_link"]
        assert "security=tls" in user["main_link"]
        assert "ed%3D2560" in user["main_link"]
        assert not any(f":{port}" in user["main_link"] for port in XRAY_INTERNAL_PORTS)
    admin.delete(f"/api/users/{user['uid']}", headers=ORIGIN)


@pytest.mark.parametrize(
    ("protocol", "transport", "expected"),
    [("vless", "xhttp", "httpupgrade"), ("vmess", "xhttp", "ws")],
)
def test_legacy_xhttp_api_requests_map_to_reference_transport(admin, protocol, transport, expected):
    response = admin.post("/api/users", headers=ORIGIN, json={
        "name": f"legacy-{protocol}-user",
        "protocol": protocol,
        "transport": transport,
    })
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert (user["protocol"], user["transport"]) == (protocol, expected)
    if protocol == "vmess":
        payload = json.loads(base64.b64decode(user["main_link"].split("://", 1)[1] + "=="))
        assert payload["net"] == "ws" and "?ed=2560" in payload["path"]
    else:
        assert "type=httpupgrade" in user["main_link"]
        assert "cdn" in user["main_link"]
    admin.delete(f"/api/users/{user['uid']}", headers=ORIGIN)


@pytest.mark.parametrize("protocol", ["shadowsocks", "hysteria2", "wireguard"])
def test_retired_protocols_normalize_to_vless_websocket_tls(admin, protocol):
    response = admin.post("/api/users", headers=ORIGIN, json={
        "name": f"retired-{protocol}", "protocol": protocol,
        "transport": "httpupgrade", "security": "tls",
    })
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert (user["protocol"], user["transport"], user["security"]) == ("vless", "ws", "tls")
    assert user["main_link"].startswith("vless://")
    assert "type=ws" in user["main_link"]
    admin.delete(f"/api/users/{user['uid']}", headers=ORIGIN)


@pytest.mark.parametrize("protocol", ["vless", "vmess", "trojan"])
def test_raw_tcp_and_reality_choices_remap_to_vless_websocket_tls(admin, protocol):
    response = admin.post("/api/users", headers=ORIGIN, json={
        "name": f"legacy-{protocol}", "protocol": protocol,
        "transport": "tcp", "security": "reality",
    })
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert user["protocol"] == "vless"
    assert user["transport"] == "ws" and user["security"] == "tls"
    assert user["main_link"].startswith("vless://")
    assert "type=tcp" not in user["main_link"] and "security=reality" not in user["main_link"]
    assert not any(f":{port}" in user["main_link"] for port in XRAY_INTERNAL_PORTS)
    admin.delete(f"/api/users/{user['uid']}", headers=ORIGIN)


def test_xray_emits_the_five_loopback_reference_inbounds(monkeypatch):
    users = [
        {"uid": "a", "uuid": "00000000-0000-0000-0000-00000000000a", "enabled": 1,
         "protocol": "vless"},
        {"uid": "b", "uuid": "00000000-0000-0000-0000-00000000000b", "enabled": 1,
         "protocol": "vmess"},
        {"uid": "c", "uuid": "00000000-0000-0000-0000-00000000000c", "enabled": 1,
         "protocol": "trojan"},
    ]
    monkeypatch.setattr(xray, "_xray_users", lambda: users)
    monkeypatch.setattr(db, "get_settings", lambda: {"restrict_ips": True})
    result = xray.generate_xray_config()
    inbounds = result["inbounds"]
    assert all(item["listen"] == "127.0.0.1" for item in inbounds)
    assert len(inbounds) == 6  # private stats/API listener + the five public-path profiles
    public = {item["tag"]: item for item in inbounds if "streamSettings" in item}
    assert set(public) == {
        "JX-VLESS-WS-1", "JX-VLESS-WS-2", "JX-TROJAN-WS", "JX-VMESS-WS", "JX-VLESS-HU"
    }
    expected = {
        "JX-VLESS-WS-1": (10001, "vless", "ws"),
        "JX-VLESS-WS-2": (10002, "vless", "ws"),
        "JX-TROJAN-WS": (10003, "trojan", "ws"),
        "JX-VMESS-WS": (10004, "vmess", "ws"),
        "JX-VLESS-HU": (10005, "vless", "httpupgrade"),
    }
    for tag, (port, protocol, network) in expected.items():
        inbound = public[tag]
        assert (inbound["port"], inbound["protocol"], inbound["streamSettings"]["network"]) == (
            port, protocol, network
        )
        assert inbound["settings"]["clients"]
        assert inbound["sniffing"]["destOverride"] == ["http", "tls", "quic"]
    assert result["routing"]["domainStrategy"] == "AsIs"
    assert "bittorrent" in str(result["routing"])
    assert "xhttp" not in str(result).lower() and "grpc" not in str(result).lower()
    assert "reality" not in str(result).lower()


def test_generated_paths_persist_and_create_matching_nginx_routes(tmp_path, monkeypatch):
    paths_file = tmp_path / "paths.json"
    include_file = tmp_path / "nginx-paths.conf"
    monkeypatch.setattr(genpaths, "PATHS_FILE", str(paths_file))
    monkeypatch.setattr(genpaths, "NGINX_PATHS_FILE", str(include_file))
    first = genpaths.get_paths()
    second = genpaths.get_paths()
    assert first == second
    assert json.loads(paths_file.read_text(encoding="utf-8")) == first
    assert list(first) == ["vless_ws", "vless_ws_2", "trojan_ws", "vmess_ws", "vless_httpupgrade"]
    assert [first[key].split("/")[1] for key in first] == ["ws", "stream", "live", "gw", "cdn"]
    nginx = include_file.read_text(encoding="utf-8")
    assert all(f"location = {path}" in nginx for path in first.values())
    assert "127.0.0.1:10001" in nginx and "127.0.0.1:10005" in nginx
    assert "Upgrade $http_upgrade" in nginx and 'Connection "upgrade"' in nginx
    assert "proxy_buffering off" in nginx and "proxy_request_buffering off" in nginx
    assert "xhttp" not in nginx.lower() and "grpc" not in nginx.lower()
    assert "location = /dns-query" not in nginx


def test_titan_path_upgrade_preserves_old_routes_and_reuses_old_vless_xhttp_path(
    tmp_path, monkeypatch
):
    paths_file = tmp_path / "paths.json"
    include_file = tmp_path / "nginx-paths.conf"
    monkeypatch.setattr(genpaths, "PATHS_FILE", str(paths_file))
    monkeypatch.setattr(genpaths, "NGINX_PATHS_FILE", str(include_file))
    legacy = {
        "vless_ws": "/vl-ws/" + "1" * 16,
        "vmess_ws": "/vm-ws/" + "2" * 16,
        "trojan_ws": "/tr-ws/" + "3" * 16,
        "xhttp": "/xhttp/" + "4" * 16,
        "vmess_xhttp": "/xhttp/vmess/" + "5" * 16,
        "grpc_service": "titan-" + "7" * 16,
    }
    paths_file.write_text(json.dumps(legacy), encoding="utf-8")

    paths = genpaths.get_paths()

    assert paths["vless_ws"] == legacy["vless_ws"]
    assert paths["vmess_ws"] == legacy["vmess_ws"]
    assert paths["trojan_ws"] == legacy["trojan_ws"]
    assert paths["vless_httpupgrade"] == legacy["xhttp"]
    assert paths["vless_ws_2"].startswith("/stream/")
    assert set(paths) == {"vless_ws", "vless_ws_2", "trojan_ws", "vmess_ws", "vless_httpupgrade"}
    assert json.loads(paths_file.read_text(encoding="utf-8")) == paths


def test_remote_node_can_adopt_authenticated_main_route_map(tmp_path, monkeypatch):
    paths_file = tmp_path / "paths.json"
    include_file = tmp_path / "nginx-paths.conf"
    monkeypatch.setattr(genpaths, "PATHS_FILE", str(paths_file))
    monkeypatch.setattr(genpaths, "NGINX_PATHS_FILE", str(include_file))
    paths = genpaths.get_paths()
    received, changed = genpaths.set_paths(paths)
    assert received == paths and changed is False
    another = {key: value.rsplit("/", 1)[0] + "/" + str(uuid.uuid4())
               for key, value in paths.items()}
    received, changed = genpaths.set_paths(another)
    assert received == another and changed is True
    assert json.loads(paths_file.read_text(encoding="utf-8")) == another
    with pytest.raises(ValueError, match="invalid-path-map"):
        genpaths.set_paths({"vless_ws": "/not/a/path"})
    duplicate = dict(another)
    duplicate["vless_ws_2"] = duplicate["vless_ws"]
    with pytest.raises(ValueError, match="invalid-path-map"):
        genpaths.set_paths(duplicate)
    paths_file.write_text(json.dumps(duplicate), encoding="utf-8")
    regenerated = genpaths.get_paths()
    assert len(set(regenerated.values())) == len(regenerated)


def test_only_ws_httpupgrade_routes_and_xray_networks_are_generated(tmp_path, monkeypatch):
    paths_file = tmp_path / "paths.json"
    include_file = tmp_path / "nginx-paths.conf"
    monkeypatch.setattr(genpaths, "PATHS_FILE", str(paths_file))
    monkeypatch.setattr(genpaths, "NGINX_PATHS_FILE", str(include_file))
    genpaths.get_paths()
    generated = include_file.read_text(encoding="utf-8")
    assert "httpupgrade" not in generated.lower()  # transport settings are applied by Xray
    monkeypatch.setattr(xray, "_xray_users", lambda: [{
        "uid": "a", "uuid": "00000000-0000-0000-0000-00000000000a",
        "enabled": 1, "protocol": "vless",
    }])
    monkeypatch.setattr(db, "get_settings", lambda: {})
    networks = {
        item["streamSettings"]["network"]
        for item in xray.generate_xray_config()["inbounds"]
        if "streamSettings" in item
    }
    assert networks == {"ws", "httpupgrade"}
    assert "xhttp" not in str(xray.generate_xray_config()).lower()


def test_subscription_status_marker_does_not_advertise_raw_tcp(admin):
    response = admin.post("/api/users", headers=ORIGIN, json={
        "name": "subscription-marker", "protocol": "vless", "transport": "ws"
    })
    assert response.status_code == 200, response.text
    uid = response.json()["user"]["uid"]
    body = base64.b64decode(admin.get(f"/sub/{uid}").text).decode()
    marker = next(line for line in body.splitlines() if "00000000-0000-0000-0000-000000000001" in line)
    assert "type=ws" in marker and "type=tcp" not in marker
    assert "headerType" not in marker
    admin.delete(f"/api/users/{uid}", headers=ORIGIN)


def test_public_nginx_front_door_keeps_dns_query_private(repo_root):
    nginx = (Path(repo_root) / "nginx.conf").read_text(encoding="utf-8")
    assert "listen ${PORT} default_server;" in nginx
    assert "server 127.0.0.1:${PANEL_PORT};" in nginx
    assert "worker_processes 2;" in nginx and "worker_connections 8192;" in nginx
    assert "limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;" in nginx
    assert "limit_req_zone $binary_remote_addr zone=sub:10m rate=5r/s;" in nginx
    assert "include /data/nginx-paths.conf;" in nginx
    assert "location = /dns-query" not in nginx
    assert "listen 10001" not in nginx and "listen 10085" not in nginx
