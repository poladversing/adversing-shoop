"""SQLite persistence layer for TiTaN.

Thread/async safe via a process-wide lock. Uses WAL mode for concurrent reads
while writes are serialized.
"""
import json
import os
import secrets
import sqlite3
import threading
import time
from typing import Any

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def coerce_node_id(v) -> int:
    """Coerce a node_id, preserving 0 (auto / nearest node)."""
    try:
        return int(v) if v not in (None, "") else 1
    except (TypeError, ValueError):
        return 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT UNIQUE NOT NULL,
    uuid            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL DEFAULT 'User',
    note            TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    protocol        TEXT NOT NULL DEFAULT 'vless',
    transport       TEXT NOT NULL DEFAULT 'ws',
    security        TEXT NOT NULL DEFAULT 'tls',
    fingerprint     TEXT NOT NULL DEFAULT 'chrome',
    alpn            TEXT NOT NULL DEFAULT 'http/1.1',
    public_key      TEXT NOT NULL DEFAULT '',
    short_id        TEXT NOT NULL DEFAULT '',
    spider_x        TEXT NOT NULL DEFAULT '',
    max_devices     INTEGER NOT NULL DEFAULT 0,
    first_device_uid TEXT NOT NULL DEFAULT '',
    allowed_ips     TEXT NOT NULL DEFAULT '',
    quota_bytes     INTEGER NOT NULL DEFAULT 0,
    expire_at       REAL,
    created_at      REAL NOT NULL,
    used_up         INTEGER NOT NULL DEFAULT 0,
    used_down       INTEGER NOT NULL DEFAULT 0,
    request_count   INTEGER NOT NULL DEFAULT 0,
    max_requests    INTEGER NOT NULL DEFAULT 0,
    avatar          TEXT NOT NULL DEFAULT '',
    ss_method       TEXT NOT NULL DEFAULT '2022-blake3-aes-128-gcm',
    sub_transports  TEXT NOT NULL DEFAULT '',
    wg_ip           TEXT NOT NULL DEFAULT '',
    wg_priv         TEXT NOT NULL DEFAULT '',
    wg_pub          TEXT NOT NULL DEFAULT '',
    last_seen       REAL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    level      TEXT NOT NULL DEFAULT 'info',
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    user_id    INTEGER
);
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER NOT NULL,
    title      TEXT NOT NULL,
    message    TEXT NOT NULL DEFAULT '',
    type       TEXT NOT NULL DEFAULT 'info',
    created_at REAL NOT NULL,
    is_read    INTEGER NOT NULL DEFAULT 0,
    read_at    REAL,
    link       TEXT
);
CREATE TABLE IF NOT EXISTS traffic_hourly (
    bucket INTEGER PRIMARY KEY,
    up     INTEGER NOT NULL DEFAULT 0,
    down   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT 'Subscription',
    token      TEXT UNIQUE NOT NULL,
    items      TEXT NOT NULL DEFAULT '[]',
    enabled    INTEGER NOT NULL DEFAULT 1,
    note       TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0,
    last_used  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    address      TEXT NOT NULL DEFAULT '',
    city         TEXT NOT NULL DEFAULT '',
    country      TEXT NOT NULL DEFAULT '',
    country_code TEXT NOT NULL DEFAULT '',
    flag         TEXT NOT NULL DEFAULT '🏳️',
    token        TEXT NOT NULL DEFAULT '',
    wg_pub       TEXT NOT NULL DEFAULT '',
    is_local     INTEGER NOT NULL DEFAULT 0,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   REAL NOT NULL,
    last_seen    REAL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_notifications_owner_created
    ON notifications(admin_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_owner_unread
    ON notifications(admin_id, is_read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_uid ON users(uid);
"""


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
        _ensure_bootstrap()
    return _conn


def _ensure_bootstrap():
    """Generate secret key / default settings / migrations on first run."""
    c = _conn
    if not get_meta("secret_key"):
        set_meta("secret_key", secrets.token_hex(32))
    if not get_meta("created_at"):
        set_meta("created_at", str(time.time()))
    if not get_meta("backup_last_at"):
        set_meta("backup_last_at", "0")

    # migration: users.node_id (config -> node association)
    cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
    if "node_id" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN node_id INTEGER NOT NULL DEFAULT 1")
        c.commit()
    if "avatar" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
        c.commit()
    if "ss_method" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN ss_method TEXT NOT NULL DEFAULT '2022-blake3-aes-128-gcm'")
        c.commit()
    if "wg_ip" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN wg_ip TEXT NOT NULL DEFAULT ''")
        c.commit()
    if "wg_priv" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN wg_priv TEXT NOT NULL DEFAULT ''")
        c.commit()
    if "wg_pub" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN wg_pub TEXT NOT NULL DEFAULT ''")
        c.commit()
    if "sub_transports" not in cols:
        # Which of the configs the server really runs appear in the user's
        # subscription link. Empty = all of them (the behaviour before the
        # column existed), so no existing subscription changes on upgrade.
        c.execute("ALTER TABLE users ADD COLUMN sub_transports TEXT NOT NULL DEFAULT ''")
        c.commit()

    # Keep existing accounts usable while matching SuperJinX's inbound matrix.
    # Legacy VLESS XHTTP becomes VLESS HTTPUpgrade; VMess XHTTP becomes VMess WS.
    c.execute(
        "UPDATE users SET protocol=lower(protocol) "
        "WHERE lower(protocol) IN ('vless','vmess','trojan')"
    )
    c.execute(
        "UPDATE users SET protocol='vless', transport='ws', security='tls' "
        "WHERE lower(protocol) NOT IN ('vless','vmess','trojan') "
        "OR lower(security)='reality'"
    )
    c.execute(
        "UPDATE users SET transport='httpupgrade' "
        "WHERE lower(protocol)='vless' AND lower(transport)='xhttp'"
    )
    c.execute(
        "UPDATE users SET transport='ws' "
        "WHERE lower(protocol) IN ('vmess','trojan') AND lower(transport)='xhttp'"
    )
    c.execute(
        "UPDATE users SET transport='ws' "
        "WHERE lower(transport) NOT IN ('ws','httpupgrade') "
        "OR (lower(protocol) IN ('vmess','trojan') AND lower(transport)<>'ws')"
    )
    c.execute("UPDATE users SET security='tls' WHERE lower(security)<>'tls'")
    c.execute(
        "UPDATE users SET public_key='', short_id='', spider_x='', "
        "ss_method='2022-blake3-aes-128-gcm', wg_ip='', wg_priv='', wg_pub=''"
    )
    c.execute("DELETE FROM meta WHERE key LIKE 'reality_%'")
    c.execute("DELETE FROM settings WHERE key LIKE 'reality_%'")

    # Migrate the old subscription-picker keys without changing what a user had
    # explicitly included: VLESS XHTTP maps to HTTPUpgrade; VMess XHTTP maps to WS.
    for row in c.execute(
        "SELECT uid, protocol, sub_transports FROM users WHERE sub_transports<>''"
    ).fetchall():
        protocol = str(row["protocol"] or "vless").lower()
        values = [item.strip().lower() for item in str(row["sub_transports"]).split(",")
                  if item.strip()]
        if protocol == "vless":
            migrated = ["ws" if value in {"ws", "ws1"} else
                        "ws2" if value == "ws2" else
                        "httpupgrade" if value in {"xhttp", "httpupgrade"} else ""
                        for value in values]
        elif protocol == "vmess":
            migrated = ["ws"] if any(value in {"ws", "xhttp"} for value in values) else []
        else:
            migrated = ["ws"] if any(value in {"ws", "xhttp", "httpupgrade"}
                                     for value in values) else []
        migrated = list(dict.fromkeys(value for value in migrated if value))
        c.execute("UPDATE users SET sub_transports=? WHERE uid=?",
                  (",".join(migrated), row["uid"]))

    # Saved multi-user subscription bundles store profile keys in JSON. Migrate
    # those too, or old XHTTP selections would silently disappear after upgrade.
    protocols = {row["uid"]: str(row["protocol"] or "vless").lower()
                 for row in c.execute("SELECT uid, protocol FROM users").fetchall()}
    for row in c.execute("SELECT id, items FROM subscriptions").fetchall():
        try:
            items = json.loads(row["items"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(items, list):
            continue
        changed = False
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("configs"), list):
                continue
            protocol = protocols.get(str(item.get("uid") or ""), "vless")
            configs = []
            for value in item["configs"]:
                key = str(value or "").strip().lower()
                if key == "xhttp":
                    key = "httpupgrade" if protocol == "vless" else "ws"
                    changed = True
                if key and key not in configs:
                    configs.append(key)
            if configs != item["configs"]:
                item["configs"] = configs
                changed = True
        if changed:
            c.execute("UPDATE subscriptions SET items=? WHERE id=?",
                      (json.dumps(items, ensure_ascii=False), row["id"]))

    setting = c.execute(
        "SELECT value FROM settings WHERE key='default_transport'"
    ).fetchone()
    if setting is not None:
        try:
            default_transport = json.loads(setting["value"])
        except (json.JSONDecodeError, TypeError):
            default_transport = setting["value"]
        if default_transport == "xhttp":
            c.execute(
                "UPDATE settings SET value=? WHERE key='default_transport'",
                (json.dumps("httpupgrade"),),
            )
        elif default_transport not in {"ws", "httpupgrade"}:
            c.execute(
                "UPDATE settings SET value=? WHERE key='default_transport'",
                (json.dumps("ws"),),
            )
    c.commit()

    # migration: subscriptions.avatar / plan — the profile the dashboard sets for
    # a *link* (its own picture and label), shown on the public page.
    scols = [r["name"] for r in c.execute("PRAGMA table_info(subscriptions)").fetchall()]
    if "avatar" not in scols:
        c.execute("ALTER TABLE subscriptions ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
        c.commit()
    if "plan" not in scols:
        c.execute("ALTER TABLE subscriptions ADD COLUMN plan TEXT NOT NULL DEFAULT ''")
        c.commit()

    # migration: nodes.token (per-node credential issued by the main panel)
    ncols = [r["name"] for r in c.execute("PRAGMA table_info(nodes)").fetchall()]
    if "token" not in ncols:
        c.execute("ALTER TABLE nodes ADD COLUMN token TEXT NOT NULL DEFAULT ''")
        c.commit()
    if "wg_pub" not in ncols:
        c.execute("ALTER TABLE nodes ADD COLUMN wg_pub TEXT NOT NULL DEFAULT ''")
        c.commit()
    c.execute("UPDATE nodes SET wg_pub='' WHERE wg_pub<>''")
    c.commit()

    # seed the local node (this server) once
    row = c.execute("SELECT id FROM nodes WHERE is_local=1").fetchone()
    if not row:
        c.execute(
            "INSERT INTO nodes(name, address, city, country, country_code, flag, "
            "is_local, enabled, created_at, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("سرور اصلی", "", "—", "—", "", "🌐", 1, 1, time.time(), time.time()),
        )
        c.commit()

    # --- default admin — no registration required. ---------------------------
    # Fresh installations use admin/admin unless the operator supplies the
    # TITAN_ADMIN_USER / TITAN_ADMIN_PASS environment variables.
    if not get_admin():
        from . import security as _sec
        default_user = os.environ.get("TITAN_ADMIN_USER", "admin")
        default_pass = os.environ.get("TITAN_ADMIN_PASS", "admin")
        hp = _sec.hash_password(default_pass)
        set_admin(default_user, hp["hash"], hp["salt"])
        set_meta("auth_is_default", "1")
    else:
        # Migrate installations that still have a known first-run password to
        # the current defaults. A password explicitly changed by an operator is
        # never replaced; env values continue to override the new defaults.
        try:
            from . import security as _sec2  # type: ignore
            a = get_admin()
            if a and get_meta("auth_is_default") == "1":
                is_old_default = False
                for old_pass in ("", "TiTaN", "TiTaN123", "admin123"):
                    try:
                        if _sec2.verify_password(old_pass, a["salt"], a["password_hash"]):
                            is_old_default = True
                            break
                    except Exception:
                        pass
                if is_old_default:
                    default_user = os.environ.get("TITAN_ADMIN_USER", "admin")
                    default_pass = os.environ.get("TITAN_ADMIN_PASS", "admin")
                    hp = _sec2.hash_password(default_pass)
                    set_admin(default_user, hp["hash"], hp["salt"])
        except Exception:
            pass


def get_meta(key: str) -> str | None:
    with _lock:
        c = _connect()
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str):
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        c.commit()


def get_secret_key() -> str:
    key = get_meta("secret_key")
    if not key:
        key = secrets.token_hex(32)
        set_meta("secret_key", key)
    return key


def get_admin() -> dict | None:
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM admin WHERE id=1").fetchone()
        return dict(row) if row else None


def set_admin(username: str, password_hash: str, salt: str) -> None:
    with _lock:
        c = _connect()
        c.execute("DELETE FROM admin")
        c.execute(
            "INSERT INTO admin(id, username, password_hash, salt, created_at) "
            "VALUES(1, ?, ?, ?, ?)",
            (username, password_hash, salt, time.time()),
        )
        c.commit()


def get_settings() -> dict:
    with _lock:
        c = _connect()
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    settings = dict(config.DEFAULT_SETTINGS)
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            settings[row["key"]] = row["value"]
    return settings


def set_setting(key: str, value: Any) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        c.commit()


def set_settings(mapping: dict) -> None:
    for k, v in mapping.items():
        set_setting(k, v)


def list_users() -> list[dict]:
    with _lock:
        c = _connect()
        rows = c.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    return [dict(r) for r in rows]


def get_user(uid: str) -> dict | None:
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None


def get_user_by_uuid(uuid: str) -> dict | None:
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM users WHERE uuid=?", (uuid,)).fetchone()
        return dict(row) if row else None


def create_user(data: dict) -> dict:
    with _lock:
        c = _connect()
        cols = [
            "uid", "uuid", "name", "note", "enabled", "protocol", "transport",
            "security", "fingerprint", "alpn", "max_devices", "first_device_uid",
            "allowed_ips", "quota_bytes", "expire_at", "created_at", "max_requests",
            "node_id", "avatar", "sub_transports",
        ]
        now = time.time()
        values = {
            "uid": data["uid"],
            "uuid": data["uuid"],
            "name": data.get("name", "User")[:64],
            "note": data.get("note", "")[:200],
            "enabled": 1 if data.get("enabled", True) else 0,
            "protocol": data.get("protocol", "vless"),
            "transport": data.get("transport", "ws"),
            "security": data.get("security", "tls"),
            "fingerprint": data.get("fingerprint", "chrome"),
            "alpn": data.get("alpn", "http/1.1"),
            "max_devices": int(data.get("max_devices", 0) or 0),
            "first_device_uid": data.get("first_device_uid", ""),
            "allowed_ips": json.dumps(data.get("allowed_ips", []), ensure_ascii=False),
            "quota_bytes": int(data.get("quota_bytes", 0) or 0),
            "expire_at": data.get("expire_at"),
            "created_at": now,
            "max_requests": int(data.get("max_requests", 0) or 0),
            "node_id": coerce_node_id(data.get("node_id")),
            "avatar": data.get("avatar", "") or "",
            "sub_transports": ",".join(data.get("sub_transports") or []),
        }
        placeholders = ", ".join("?" for _ in cols)
        c.execute(
            f"INSERT INTO users({', '.join(cols)}) VALUES({placeholders})",
            [values[col] for col in cols],
        )
        c.commit()
    return get_user(data["uid"])


def update_user(uid: str, fields: dict) -> dict | None:
    allowed = {
        "name", "note", "enabled", "protocol", "transport", "security",
        "fingerprint", "alpn", "max_devices", "first_device_uid", "quota_bytes",
        "expire_at", "max_requests", "node_id", "avatar", "uuid",
        # allowed_ips was missing from this allowlist, so PATCH silently
        # dropped it (the handler validated it, the UPDATE never wrote it).
        "allowed_ips",
        "sub_transports",
    }
    with _lock:
        c = _connect()
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "enabled":
                v = 1 if v else 0
            if k == "allowed_ips":
                v = json.dumps(v, ensure_ascii=False)
            if k == "sub_transports" and isinstance(v, (list, tuple, set)):
                v = ",".join(str(x) for x in v)
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            vals.append(uid)
            c.execute(f"UPDATE users SET {', '.join(sets)} WHERE uid=?", vals)
            c.commit()
    return get_user(uid)


def set_user_usage(uid: str, up: int, down: int) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "UPDATE users SET used_up=?, used_down=? WHERE uid=?", (up, down, uid)
        )
        c.commit()


def add_user_usage(uid: str, up_delta: int, down_delta: int) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "UPDATE users SET used_up=used_up+?, used_down=used_down+? WHERE uid=?",
            (up_delta, down_delta, uid),
        )
        c.commit()


def reset_user_usage(uid: str) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "UPDATE users SET used_up=0, used_down=0, request_count=0 WHERE uid=?",
            (uid,),
        )
        c.commit()


def delete_user(uid: str) -> bool:
    with _lock:
        c = _connect()
        cur = c.execute("DELETE FROM users WHERE uid=?", (uid,))
        c.commit()
        return cur.rowcount > 0


def touch_last_seen(uid: str, ts: float | None = None) -> None:
    with _lock:
        c = _connect()
        c.execute("UPDATE users SET last_seen=? WHERE uid=?", (ts or time.time(), uid))
        c.commit()


def count_recently_seen(seconds: int) -> int:
    with _lock:
        c = _connect()
        row = c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE last_seen IS NOT NULL AND last_seen > ?",
            (time.time() - seconds,),
        ).fetchone()
        return row["n"]


def get_totals() -> dict:
    with _lock:
        c = _connect()
        row = c.execute(
            "SELECT COALESCE(SUM(used_up),0) AS up, COALESCE(SUM(used_down),0) AS down, "
            "COUNT(*) AS n FROM users"
        ).fetchone()
        return {"up": row["up"], "down": row["down"], "count": row["n"]}


def add_event(level: str, action: str, detail: str = "", ip: str = "", user_id: int | None = None) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO events(ts, level, action, detail, ip, user_id) VALUES(?,?,?,?,?,?)",
            (time.time(), level, action, detail[:400], ip, user_id),
        )
        c.commit()
        # keep only the last 2000 rows
        c.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 2000)"
        )
        c.commit()


def list_events(limit: int = 200, level: str | None = None) -> list[dict]:
    with _lock:
        c = _connect()
        if level:
            rows = c.execute(
                "SELECT * FROM events WHERE level=? ORDER BY id DESC LIMIT ?",
                (level, limit),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def create_notification(admin_id: int, title: str, message: str = "",
                        kind: str = "info", link: str | None = None) -> dict:
    """Persist a dashboard notification for one administrator account."""
    owner = int(admin_id)
    if owner < 1:
        raise ValueError("admin_id must be a positive account id")
    title = str(title or "").strip()[:160]
    if not title:
        raise ValueError("notification title is required")
    message = str(message or "").strip()[:600]
    kind = str(kind or "info").strip()[:32] or "info"
    link = str(link).strip()[:500] if link else None
    now = time.time()
    with _lock:
        c = _connect()
        cur = c.execute(
            "INSERT INTO notifications(admin_id, title, message, type, created_at, is_read, link) "
            "VALUES(?,?,?,?,?,0,?)",
            (owner, title, message, kind, now, link),
        )
        c.commit()
        row = c.execute(
            "SELECT id, title, message, type, created_at, is_read, read_at, link "
            "FROM notifications WHERE id=? AND admin_id=?",
            (cur.lastrowid, owner),
        ).fetchone()
    return dict(row) if row else {}


def list_notifications(admin_id: int, limit: int = 20) -> list[dict]:
    owner = int(admin_id)
    limit = max(1, min(int(limit), 100))
    with _lock:
        c = _connect()
        rows = c.execute(
            "SELECT id, title, message, type, created_at, is_read, read_at, link "
            "FROM notifications WHERE admin_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (owner, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def count_unread_notifications(admin_id: int) -> int:
    with _lock:
        c = _connect()
        row = c.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE admin_id=? AND is_read=0",
            (int(admin_id),),
        ).fetchone()
    return int(row["n"] if row else 0)


def mark_notification_read(admin_id: int, notification_id: int) -> bool:
    with _lock:
        c = _connect()
        cur = c.execute(
            "UPDATE notifications SET is_read=1, read_at=COALESCE(read_at, ?) "
            "WHERE id=? AND admin_id=?",
            (time.time(), int(notification_id), int(admin_id)),
        )
        c.commit()
    return cur.rowcount > 0


def mark_all_notifications_read(admin_id: int) -> int:
    with _lock:
        c = _connect()
        cur = c.execute(
            "UPDATE notifications SET is_read=1, read_at=COALESCE(read_at, ?) "
            "WHERE admin_id=? AND is_read=0",
            (time.time(), int(admin_id)),
        )
        c.commit()
    return int(cur.rowcount)


def clear_events() -> None:
    with _lock:
        c = _connect()
        c.execute("DELETE FROM events")
        c.commit()


# ------------------------------------------------------------------ nodes
def list_nodes() -> list[dict]:
    with _lock:
        c = _connect()
        rows = c.execute("SELECT * FROM nodes ORDER BY is_local DESC, id ASC").fetchall()
    return [dict(r) for r in rows]


def get_node(node_id: int) -> dict | None:
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return dict(row) if row else None


def create_node(data: dict) -> dict:
    with _lock:
        c = _connect()
        cur = c.execute(
            "INSERT INTO nodes(name, address, city, country, country_code, flag, token, "
            "is_local, enabled, created_at, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                data.get("name", "Node")[:64],
                data.get("address", "")[:200],
                data.get("city", "")[:64],
                data.get("country", "")[:64],
                data.get("country_code", "")[:2],
                data.get("flag", "🏳️"),
                data.get("token", ""),
                0,
                1 if data.get("enabled", True) else 0,
                time.time(),
                time.time(),
            ),
        )
        c.commit()
        return get_node(cur.lastrowid)


def get_node_by_token(token: str) -> dict | None:
    """Find a (remote) node by its per-node credential."""
    token = (token or "").strip()
    if not token:
        return None
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM nodes WHERE token=? AND is_local=0", (token,)).fetchone()
        return dict(row) if row else None


def update_node(node_id: int, fields: dict) -> dict | None:
    allowed = {"name", "address", "city", "country", "country_code", "flag", "enabled", "wg_pub"}
    with _lock:
        c = _connect()
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "enabled":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            vals.append(node_id)
            c.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id=?", vals)
            c.commit()
    return get_node(node_id)


def delete_node(node_id: int) -> bool:
    with _lock:
        c = _connect()
        cur = c.execute("DELETE FROM nodes WHERE id=? AND is_local=0", (node_id,))
        c.commit()
        return cur.rowcount > 0


def touch_node(node_id: int) -> None:
    with _lock:
        c = _connect()
        c.execute("UPDATE nodes SET last_seen=? WHERE id=?", (time.time(), node_id))
        c.commit()


def set_local_node_location(city: str, country: str, country_code: str, flag: str) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "UPDATE nodes SET city=?, country=?, country_code=?, flag=? WHERE is_local=1",
            (city, country, country_code, flag),
        )
        c.commit()



# ------------------------------------------------------------------- subscriptions
def local_node() -> dict | None:
    """The row that describes *this* process (seeded on first boot)."""
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM nodes WHERE is_local=1").fetchone()
    return dict(row) if row else None


def list_subscriptions() -> list[dict]:
    with _lock:
        c = _connect()
        rows = c.execute("SELECT * FROM subscriptions ORDER BY id ASC").fetchall()
    return [_sub_row(r) for r in rows]


def get_subscription(sub_id: int) -> dict | None:
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    return _sub_row(row) if row else None


def get_subscription_by_token(token: str) -> dict | None:
    if not token:
        return None
    with _lock:
        c = _connect()
        row = c.execute("SELECT * FROM subscriptions WHERE token=?", (token,)).fetchone()
    return _sub_row(row) if row else None


def create_subscription(name: str, token: str, items, note: str = "", enabled: bool = True,
                        avatar: str = "", plan: str = "") -> dict:
    payload = json.dumps(items or [], ensure_ascii=False)
    with _lock:
        c = _connect()
        cur = c.execute(
            "INSERT INTO subscriptions(name, token, items, enabled, note, created_at, avatar, plan) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (name, token, payload, 1 if enabled else 0, note, time.time(), avatar or "", plan or ""),
        )
        c.commit()
        new_id = cur.lastrowid
    return get_subscription(new_id) or {}


def update_subscription(sub_id: int, fields: dict) -> dict | None:
    allowed = {"name", "items", "enabled", "note", "token", "avatar", "plan"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "items":
            value = json.dumps(value or [], ensure_ascii=False)
        if key == "enabled":
            value = 1 if value else 0
        sets.append(f"{key}=?")
        vals.append(value)
    if sets:
        vals.append(sub_id)
        with _lock:
            c = _connect()
            c.execute(f"UPDATE subscriptions SET {', '.join(sets)} WHERE id=?", vals)
            c.commit()
    return get_subscription(sub_id)


def delete_subscription(sub_id: int) -> bool:
    with _lock:
        c = _connect()
        cur = c.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
        c.commit()
        return cur.rowcount > 0


def touch_subscription(sub_id: int) -> None:
    with _lock:
        c = _connect()
        c.execute("UPDATE subscriptions SET hits=hits+1, last_used=? WHERE id=?", (time.time(), sub_id))
        c.commit()


def _sub_row(row) -> dict:
    d = dict(row)
    try:
        d["items"] = json.loads(d.get("items") or "[]")
    except (TypeError, ValueError):
        d["items"] = []
    return d


def backup_bytes() -> bytes:
    """Return a consistent snapshot of the DB file."""
    with _lock:
        c = _connect()
        c.execute("PRAGMA wal_checkpoint(FULL)")
        c.commit()
    with open(config.DB_PATH, "rb") as f:
        return f.read()


def add_traffic(bucket: int, up: int, down: int) -> None:
    with _lock:
        c = _connect()
        c.execute(
            "INSERT INTO traffic_hourly(bucket, up, down) VALUES(?,?,?) "
            "ON CONFLICT(bucket) DO UPDATE SET up=up+excluded.up, down=down+excluded.down",
            (bucket, up, down),
        )
        c.execute("DELETE FROM traffic_hourly WHERE bucket < ?", (bucket - 168 * 3600,))
        c.commit()


def get_traffic(bucket_from: int) -> list[dict]:
    with _lock:
        c = _connect()
        rows = c.execute(
            "SELECT bucket, up, down FROM traffic_hourly WHERE bucket >= ? ORDER BY bucket ASC",
            (bucket_from,),
        ).fetchall()
    return [dict(r) for r in rows]


def replace_db(data: bytes) -> bool:
    """Replace the live DB with the given bytes (used by restore)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                _conn.close()
            except sqlite3.Error:
                pass
            _conn = None
        os.makedirs(config.DATA_DIR, exist_ok=True)
        tmp = config.DB_PATH + ".restore"
        with open(tmp, "wb") as f:
            f.write(data)
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(config.DB_PATH + suffix)
            except OSError:
                pass
        os.replace(tmp, config.DB_PATH)
        _connect()
        return True
