#!/usr/bin/env bash
set -Eeuo pipefail

export PORT=8080
export PANEL_PORT="${PANEL_PORT:-10000}"
export PANEL_HOST="127.0.0.1"
export TITAN_DATA_DIR="${TITAN_DATA_DIR:-/data}"
export TITAN_XRAY_CONFIG="${TITAN_XRAY_CONFIG:-${TITAN_DATA_DIR}/xray.json}"
export XRAY_EXTERNAL_SUPERVISOR=1

internal_ports=(
    "$PANEL_PORT"
    "${XRAY_VLESS_WS_PORT:-10001}"
    "${XRAY_VLESS_WS_2_PORT:-${XRAY_VLESS_WS2_PORT:-10002}}"
    "${XRAY_TROJAN_WS_PORT:-10003}"
    "${XRAY_VMESS_WS_PORT:-10004}"
    "${XRAY_VLESS_HTTPUPGRADE_PORT:-10005}"
    "${XRAY_API_PORT:-10085}"
)
all_ports=("$PORT" "${internal_ports[@]}")
for port in "${all_ports[@]}"; do
    if ! [[ "$port" =~ ^[0-9]+$ ]]; then
        echo "[titan] invalid listener port: $port" >&2
        exit 1
    fi
    port_number=$((10#$port))
    if ((port_number < 1 || port_number > 65535)); then
        echo "[titan] invalid listener port: $port" >&2
        exit 1
    fi
done
for ((i = 0; i < ${#all_ports[@]}; i++)); do
    for ((j = i + 1; j < ${#all_ports[@]}; j++)); do
        if [[ "${all_ports[i]}" == "${all_ports[j]}" ]]; then
            echo "[titan] listener port collision: ${all_ports[i]}" >&2
            exit 1
        fi
    done
done

mkdir -p "$TITAN_DATA_DIR"

python -m app.genpaths
python - <<'PY'
from app import xray
xray.write_xray_config()
PY

envsubst '${PORT} ${PANEL_PORT}' < /app/nginx.conf > /etc/nginx/nginx.conf
nginx -t

exec python /app/watchdog.py
