FROM python:3.12-slim

ARG TARGETARCH=amd64
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TITAN_DATA_DIR=/data \
    PORT=8080 \
    PANEL_HOST=127.0.0.1 \
    PANEL_PORT=10000 \
    XRAY_EXTERNAL_SUPERVISOR=1 \
    XRAY_LOCATION_ASSET=/usr/local/share/xray

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates curl gettext-base nginx procps unzip \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) XRAY_ARCH=64 ;; \
      arm64) XRAY_ARCH=arm64-v8a ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    mkdir -p /tmp/xray-dist /usr/local/share/xray; \
    curl -fsSL "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${XRAY_ARCH}.zip" -o /tmp/xray.zip; \
    unzip -q /tmp/xray.zip -d /tmp/xray-dist; \
    install -m 0755 /tmp/xray-dist/xray /usr/local/bin/xray; \
    install -m 0644 /tmp/xray-dist/geoip.dat /usr/local/share/xray/geoip.dat; \
    install -m 0644 /tmp/xray-dist/geosite.dat /usr/local/share/xray/geosite.dat; \
    rm -rf /tmp/xray.zip /tmp/xray-dist

COPY . /app
RUN chmod +x /app/entrypoint.sh /app/start_xray.sh \
    && mkdir -p /data /run/nginx

EXPOSE 8080
# Configure persistent storage in the hosting platform (Railway Volume mounted at /data).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD curl -fsS "http://127.0.0.1:8080/healthz" >/dev/null || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
