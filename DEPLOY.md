# TiTaN deployment guide

TiTaN keeps its own FastAPI/SQLite backend, dashboard APIs, multi-node features, and watchdog. Its traffic and deployment topology follows SuperJinX: one Railway service runs Nginx, FastAPI, and Xray; public TLS reaches Nginx on service port `8080`, while every app/Xray listener stays on loopback. The Railway domain should target port `8080`.

## Railway / managed Docker platform

1. Deploy this repository with its `Dockerfile` and `entrypoint.sh`.
2. Add a Railway Volume to the service and mount it at **`/data`**. TiTaN uses that volume for the SQLite database, backups, generated config, path map, and logs. The Dockerfile intentionally has no `VOLUME` instruction; Railway manages storage separately.
3. Set the Railway domain's target port to **`8080`**. The container pins its public Nginx listener to that port; do not expose FastAPI or Xray directly.
4. Before first boot, set a unique `TITAN_ADMIN_PASS` (and optionally `TITAN_ADMIN_USER`). If neither is set, the initial login is `admin` / `admin`; change it immediately after sign-in.

`railway.json` selects the Dockerfile and checks `/healthz`. Persistent storage is configured in Railway's service settings, not in the Dockerfile. `render.yaml` remains an example Docker service; mount a persistent disk at `/data` if the plan supports it.

## Container layout

```text
TLS edge :443
  └─ TiTaN service :8080
      └─ Nginx (only public listener)
          ├─ /                         → static Matrix-code landing; never redirects to the panel
          ├─ /login, /dashboard, /api  → FastAPI 127.0.0.1:10000
          ├─ /sub/*, /s/*, /p/*        → FastAPI 127.0.0.1:10000
          └─ 5 random profile paths    → Xray on 127.0.0.1:10001–10005

Xray Stats API: 127.0.0.1:10085 (never public)
watchdog.py: checks Nginx, FastAPI and Xray every 5 seconds
```

Opening `/` displays the Matrix-code landing only; it does not link or redirect into the panel, even when a session is active. Enter `/login` or `/dashboard` explicitly. `/dashboard` still requires an authenticated session and sends unauthenticated requests to `/login`.

The generated path include is served by Nginx, not by FastAPI. In a TiTaN multi-node setup, the main panel synchronizes its private path map to authenticated TiTaN nodes so a link's path matches the node that serves it.

## SuperJinX-compatible Xray profile map

Every Xray listener binds to `127.0.0.1`; TLS terminates at the Railway edge. Nginx forwards HTTP/1.1 Upgrade requests to the selected Xray listener with buffering disabled and long-lived timeouts.

| Profile | Xray protocol / network | Loopback port | Random public route prefix |
|---|---|---:|---|
| VLESS WS profile 1 | VLESS / WebSocket | 10001 | `/ws/` |
| VLESS WS profile 2 | VLESS / WebSocket | 10002 | `/stream/` |
| Trojan WS | Trojan / WebSocket | 10003 | `/live/` |
| VMess WS | VMess / WebSocket | 10004 | `/gw/` |
| VLESS HTTPUpgrade | VLESS / HTTPUpgrade | 10005 | `/cdn/` |
| Xray Stats API | private control API | 10085 | no public route |

Client links use TLS on public port 443, `alpn=http/1.1` by default, and `ed=2560`. VLESS WS has two separately persisted paths/profiles; the other three profiles match SuperJinX's protocol/network matrix. TiTaN's user form retains its existing single protocol/transport choice. Its personal subscription picker exposes the profiles available to that chosen protocol: three VLESS profiles, one VMess WS, or one Trojan WS. Client-facing configuration remarks use `TiTaN-{username} {target-country-flag}` (for example, `TiTaN-usertest 🇳🇱`) rather than a profile/marketing suffix.

At verification time, Xray 26.3.27 accepts the generated configuration (`xray run -test`) but emits upstream deprecation warnings for WebSocket, HTTPUpgrade, Trojan, and VMess. This selected matrix is retained to match the requested SuperJinX reference; review compatibility before moving to a future Xray release that may remove those features.

## Data migration from the previous TiTaN build

- Existing VLESS WS, VMess WS, and Trojan WS private paths are retained where valid.
- The previous VLESS XHTTP path is reused for the new VLESS HTTPUpgrade listener; the transport itself changes, so clients must re-import their subscription.
- Existing VMess XHTTP selections migrate to VMess WS; VLESS XHTTP selections migrate to VLESS HTTPUpgrade.
- Saved multi-user subscription bundles are migrated too, so their per-user profile selections are preserved.
- Retired raw TCP/gRPC and invalid protocol/transport combinations normalize to that protocol's WS/TLS profile; unsupported legacy protocols normalize to VLESS WS/TLS.
- Existing user protocol/transport choices remain in TiTaN's user database; only values unsupported by the new matrix are normalized.

There is no public gRPC or XHTTP client listener and no public `/dns-query` endpoint. TiTaN's multi-node synchronization remains its authenticated HTTP API; Xray's Stats API stays private on loopback.

## Environment and listener ports

| Variable | Default | Purpose |
|---|---:|---|
| `PORT` | `8080` (forced by entrypoint) | Nginx public listener |
| `PANEL_HOST` | `127.0.0.1` (forced by entrypoint) | FastAPI bind address |
| `PANEL_PORT` | `10000` | Internal FastAPI port |
| `TITAN_DATA_DIR` | `/data` | Persistent database, backups, paths and logs |
| `TITAN_XRAY_CONFIG` | `/data/xray.json` | Generated Xray config |
| `TITAN_ADMIN_USER` | `admin` | Initial admin name |
| `TITAN_ADMIN_PASS` | `admin` | Initial password; set a unique value before first boot |
| `TITAN_TELEGRAM_CHANNEL_URL` | `https://t.me/Code_Shield` | Telegram notification channel URL for the dashboard; override with an HTTPS `t.me` or `telegram.me` URL. This setting is independent from `TITAN_SUPPORT_URL`. |
| `TITAN_NODE_SECRET` | empty | Optional multi-node sync secret |
| `XRAY_VLESS_WS_PORT` | `10001` | VLESS WS profile 1 |
| `XRAY_VLESS_WS_2_PORT` | `10002` | VLESS WS profile 2 |
| `XRAY_TROJAN_WS_PORT` | `10003` | Trojan WS |
| `XRAY_VMESS_WS_PORT` | `10004` | VMess WS |
| `XRAY_VLESS_HTTPUPGRADE_PORT` | `10005` | VLESS HTTPUpgrade |
| `XRAY_API_PORT` | `10085` | Private Xray Stats API |

The listener values can be overridden, but they must all be distinct and must not collide with public Nginx or FastAPI's port. `entrypoint.sh` validates the ports and refuses collisions. Nginx's generated include uses the same values.

## Run with Docker locally

```bash
docker build -t titan .
mkdir -p ./data
docker run --rm --name titan \
  -p 8080:8080 \
  -e TITAN_ADMIN_PASS='replace-this-password' \
  -v "$PWD/data:/data" \
  titan
```

Open `http://127.0.0.1:8080/login`. Local HTTP testing is intended for the dashboard and health endpoints; client traffic requires a public TLS hostname and a matching advertised domain/port in the panel.

## Health and troubleshooting

- `GET /healthz` is the container/platform healthcheck.
- `GET /health` is used for TiTaN node discovery and synchronization.
- `/data/watchdog.log` records starts, health failures, and restarts.
- Check that the Railway Volume is writable and mounted at exactly `/data`.
- Confirm `/data/paths.json` matches `/data/nginx-paths.conf`; both are generated from the same persisted route map.
- The panel, Xray inbounds, and Xray Stats API are not public listeners; Nginx is the only public listener.
