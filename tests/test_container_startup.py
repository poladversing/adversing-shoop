"""Container manifest, listener binding and service-supervision checks."""
from pathlib import Path

import pytest

from app import config


@pytest.fixture()
def client_module():
    from app import main
    return main


def test_panel_is_configured_for_the_required_loopback_listener():
    source = Path(config.BASE_DIR, "app", "main.py").read_text(encoding="utf-8")
    block = source[source.index('if __name__ == "__main__":'):]
    assert "config.PANEL_HOST" in block
    assert "port=config.PANEL_PORT" in block
    assert config.PANEL_HOST == "127.0.0.1"
    assert config.PANEL_BIND_HOSTS == ["127.0.0.1"]


def test_entrypoint_generates_paths_configures_nginx_then_runs_watchdog(repo_root):
    script = (Path(repo_root) / "entrypoint.sh").read_text(encoding="utf-8")
    assert "export PORT=8080" in script
    assert 'export PANEL_PORT="${PANEL_PORT:-10000}"' in script
    assert 'export PANEL_HOST="127.0.0.1"' in script
    assert 'export XRAY_EXTERNAL_SUPERVISOR=1' in script
    assert "listener port collision" in script
    assert "python -m app.genpaths" in script
    assert "xray.write_xray_config()" in script
    assert "envsubst '${PORT} ${PANEL_PORT}'" in script
    assert "nginx -t" in script
    assert "exec python /app/watchdog.py" in script
    assert "python -m app.main" not in script


def test_dockerfile_installs_front_door_and_xray_and_keeps_data(repo_root):
    dockerfile = (Path(repo_root) / "Dockerfile").read_text(encoding="utf-8")
    assert "nginx" in dockerfile and "gettext-base" in dockerfile
    assert "Xray-linux-${XRAY_ARCH}.zip" in dockerfile
    assert "install -m 0755 /tmp/xray-dist/xray /usr/local/bin/xray" in dockerfile
    # Platform-managed storage is mounted at /data; Docker VOLUME breaks Railway builds.
    assert 'VOLUME ["/data"]' not in dockerfile
    assert "mkdir -p /data" in dockerfile
    assert 'ENTRYPOINT ["/app/entrypoint.sh"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/healthz" in dockerfile


def test_watchdog_checks_all_three_processes_every_five_seconds(repo_root):
    source = (Path(repo_root) / "watchdog.py").read_text(encoding="utf-8")
    assert 'TITAN_WATCHDOG_INTERVAL", "5"' in source
    assert 'RotatingFileHandler' in source and '"watchdog.log"' in source
    for service in ('Service("panel"', 'Service("xray"', 'Service("nginx"'):
        assert service in source
    assert "/healthz" in source
    assert '"/app/start_xray.sh"' in source


def test_nginx_front_door_routes_panel_api_subscriptions_and_generated_paths(repo_root):
    source = (Path(repo_root) / "nginx.conf").read_text(encoding="utf-8")
    assert "listen ${PORT} default_server;" in source
    assert "server 127.0.0.1:${PANEL_PORT};" in source
    assert "location = /" in source and "/app/static/landing" in source
    assert "limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;" in source
    assert "limit_req_zone $binary_remote_addr zone=sub:10m rate=5r/s;" in source
    assert "location ~ ^/api(?:/|$)" in source
    assert "location ~ ^/(sub|s|p)(/|$)" in source
    assert "include /data/nginx-paths.conf;" in source
    assert "location = /dns-query" not in source
    for port in (10001, 10002, 10003, 10004, 10005, 10085):
        assert f"listen {port}" not in source


def _assert_matrix_landing(response):
    assert response.status_code == 200, response.text[:300]
    assert response.headers.get("content-type", "").startswith("text/html")
    assert '<canvas id="matrixCanvas"' in response.text
    assert 'href="/login"' not in response.text
    assert 'href="/dashboard"' not in response.text


def test_root_returns_matrix_landing_without_automatic_auth_redirect(client):
    client.cookies.clear()
    response = client.get("/", follow_redirects=False)
    _assert_matrix_landing(response)
    assert response.headers.get("cache-control") == "no-store"


def test_root_stays_on_matrix_landing_even_for_an_authenticated_user(admin):
    response = admin.get("/", follow_redirects=False)
    _assert_matrix_landing(response)


def test_login_and_dashboard_are_not_opened_from_the_root_path(client):
    client.cookies.clear()
    login = client.get("/login", follow_redirects=False)
    assert login.status_code == 200
    assert "<title>TiTaN — Sign in</title>" in login.text
    assert 'id="loginForm"' in login.text
    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code in (302, 307)
    assert dashboard.headers.get("location") == "/login"


def test_authenticated_dashboard_remains_available_at_its_explicit_path(admin):
    dashboard = admin.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 200
    assert 'data-section="users"' in dashboard.text


def test_health_endpoints_are_reachable_through_the_panel(client):
    for path in ("/healthz", "/health"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.json().get("status") == "ok"


def test_unwritable_data_directory_uses_a_writable_fallback(repo_root, tmp_path):
    import os
    import subprocess
    import sys

    bad = "/proc/titan-test-not-writable"
    result = subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(c.DATA_DIR)"],
        cwd=str(repo_root),
        env={**os.environ, "TITAN_DATA_DIR": bad, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    chosen = result.stdout.strip().splitlines()[-1]
    assert chosen != bad and os.path.isdir(chosen) and os.access(chosen, os.W_OK)
    assert "unavailable" in result.stderr and "Data will not persist" in result.stderr
