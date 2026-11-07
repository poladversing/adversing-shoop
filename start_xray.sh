#!/usr/bin/env bash
set -Eeuo pipefail

DATA_DIR="${TITAN_DATA_DIR:-/data}"
XRAY_CONFIG="${TITAN_XRAY_CONFIG:-${DATA_DIR}/xray.json}"
PID_FILE="${DATA_DIR}/xray.pid"
mkdir -p "$DATA_DIR"

child_pid=""
shutting_down=0

cleanup() {
    shutting_down=1
    if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    exit 0
}
trap cleanup TERM INT

while true; do
    if [[ ! -s "$XRAY_CONFIG" ]]; then
        sleep 1
        continue
    fi

    /usr/local/bin/xray run -c "$XRAY_CONFIG" &
    child_pid=$!
    printf '%s\n' "$child_pid" > "${PID_FILE}.tmp"
    mv "${PID_FILE}.tmp" "$PID_FILE"

    status=0
    wait "$child_pid" || status=$?
    rm -f "$PID_FILE"
    child_pid=""

    if [[ "$shutting_down" -eq 1 ]]; then
        exit 0
    fi
    printf '[titan-xray] process exited with status %s; restarting\n' "$status" >&2
    sleep 1
done
