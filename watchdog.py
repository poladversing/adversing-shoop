"""Supervise Nginx, the FastAPI panel and Xray in one container."""
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from urllib.error import URLError
from urllib.request import urlopen

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("TITAN_DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
PANEL_PORT = int(os.environ.get("PANEL_PORT", "10000"))
XRAY_API_PORT = int(os.environ.get("XRAY_API_PORT", "10085"))
INTERVAL = max(1.0, float(os.environ.get("TITAN_WATCHDOG_INTERVAL", "5")))
STARTUP_GRACE = max(10.0, float(os.environ.get("TITAN_WATCHDOG_STARTUP_GRACE", "25")))
FAILURES_TO_RESTART = max(1, int(os.environ.get("TITAN_WATCHDOG_FAILURES", "2")))


@dataclass
class Service:
    name: str
    command: list[str]
    process: subprocess.Popen | None = None
    started_at: float = 0.0
    failed_checks: int = 0


def _logger() -> logging.Logger:
    os.makedirs(DATA_DIR, exist_ok=True)
    logger = logging.getLogger("titan.watchdog")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        os.path.join(DATA_DIR, "watchdog.log"), maxBytes=2_000_000, backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


log = _logger()

SERVICES = [
    Service("panel", [sys.executable, "-m", "app.main"]),
    Service("xray", ["/app/start_xray.sh"]),
    Service("nginx", ["nginx", "-g", "daemon off;"]),
]


def _start(service: Service) -> None:
    service.process = subprocess.Popen(
        service.command,
        cwd=APP_DIR,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    service.started_at = time.monotonic()
    service.failed_checks = 0
    log.info("started %s pid=%s", service.name, service.process.pid)


def _stop(service: Service) -> None:
    process = service.process
    if not process or process.poll() is not None:
        service.process = None
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    service.process = None


def _http_ok(port: int, timeout: float = 2.0) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError, TimeoutError, ValueError):
        return False


def _tcp_ok(port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _healthy(service: Service) -> bool:
    if service.name == "panel":
        return _http_ok(PANEL_PORT)
    if service.name == "nginx":
        return _http_ok(PORT)
    return _tcp_ok(XRAY_API_PORT)


def _check(service: Service, now: float) -> None:
    process = service.process
    if process is None or process.poll() is not None:
        status = None if process is None else process.returncode
        log.warning("%s is down (exit=%s); restarting", service.name, status)
        _stop(service)
        _start(service)
        return

    if now - service.started_at < STARTUP_GRACE:
        return
    if _healthy(service):
        service.failed_checks = 0
        return

    service.failed_checks += 1
    log.warning(
        "%s health check failed (%s/%s)",
        service.name, service.failed_checks, FAILURES_TO_RESTART,
    )
    if service.failed_checks >= FAILURES_TO_RESTART:
        log.error("restarting unhealthy service %s", service.name)
        _stop(service)
        _start(service)


def _shutdown(_signum=None, _frame=None) -> None:
    log.info("watchdog shutting down services")
    for service in reversed(SERVICES):
        _stop(service)
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    os.chdir(APP_DIR)
    for service in SERVICES:
        _start(service)
    log.info("health checks active; interval=%ss", INTERVAL)
    while True:
        time.sleep(INTERVAL)
        now = time.monotonic()
        for service in SERVICES:
            _check(service, now)


if __name__ == "__main__":
    main()
