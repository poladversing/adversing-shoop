"""Upgrade an on-disk legacy TiTaN schema into the supported transport set."""
import json
import sqlite3

from app import db


def _legacy_connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE admin (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL, password_hash TEXT NOT NULL,
            salt TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, uid TEXT NOT NULL, uuid TEXT NOT NULL,
            protocol TEXT NOT NULL, transport TEXT NOT NULL, security TEXT NOT NULL,
            public_key TEXT NOT NULL DEFAULT '', short_id TEXT NOT NULL DEFAULT '',
            spider_x TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            sub_transports TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT 'Subscription',
            token TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
            last_used REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE nodes (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '',
            country_code TEXT NOT NULL DEFAULT '', flag TEXT NOT NULL DEFAULT '🏳️',
            is_local INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL, last_seen REAL
        );
        """
    )
    rows = [
        (1, "legacy-ss", "id-1", "shadowsocks", "tcp", "reality"),
        (2, "legacy-vless", "id-2", "vless", "tcp", "reality"),
        (3, "legacy-vmess", "id-3", "vmess", "tcp", "none"),
        (4, "legacy-trojan", "id-4", "trojan", "grpc", "tls"),
        (5, "valid-xhttp", "id-5", "vless", "xhttp", "tls"),
    ]
    connection.executemany(
        "INSERT INTO users(id,uid,uuid,protocol,transport,security,public_key,short_id,spider_x,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,0)",
        [(i, uid, uuid, protocol, transport, security, "pub", "short", "spider")
         for i, uid, uuid, protocol, transport, security in rows],
    )
    connection.execute("UPDATE users SET sub_transports='ws,xhttp' WHERE uid='valid-xhttp'")
    connection.execute("UPDATE users SET sub_transports='xhttp' WHERE uid='legacy-vmess'")
    bundle = json.dumps([
        {"uid": "valid-xhttp", "configs": ["ws", "xhttp"]},
        {"uid": "legacy-vmess", "configs": ["xhttp"]},
    ])
    connection.execute(
        "INSERT INTO subscriptions(name,token,items,created_at) VALUES('legacy','token',?,0)",
        (bundle,),
    )
    connection.execute("INSERT INTO settings(key,value) VALUES('default_transport',?)",
                       (json.dumps("tcp"),))
    connection.execute("INSERT INTO settings(key,value) VALUES('reality_private_key',?)",
                       (json.dumps("retired"),))
    connection.execute("INSERT INTO meta(key,value) VALUES('reality_public_key','retired')")
    connection.commit()
    return connection


def test_legacy_protocol_rows_and_defaults_are_migrated(monkeypatch):
    previous = db._conn
    connection = _legacy_connection()
    monkeypatch.setattr(db, "_conn", connection)
    try:
        db._ensure_bootstrap()
        rows = connection.execute(
            "SELECT uid,protocol,transport,security,public_key,short_id,spider_x,ss_method,"
            "wg_ip,wg_priv,wg_pub,node_id,avatar FROM users ORDER BY id"
        ).fetchall()
        migrated = [dict(row) for row in rows]
        assert [(row["protocol"], row["transport"], row["security"]) for row in migrated] == [
            ("vless", "ws", "tls"),
            ("vless", "ws", "tls"),
            ("vmess", "ws", "tls"),
            ("trojan", "ws", "tls"),
            ("vless", "httpupgrade", "tls"),
        ]
        assert all(not any(row[k] for k in (
            "public_key", "short_id", "spider_x", "wg_ip", "wg_priv", "wg_pub"
        )) for row in migrated)
        assert all(row["ss_method"] == "2022-blake3-aes-128-gcm" for row in migrated)
        assert all(row["node_id"] == 1 and row["avatar"] == "" for row in migrated)
        setting = connection.execute(
            "SELECT value FROM settings WHERE key='default_transport'"
        ).fetchone()
        assert json.loads(setting["value"]) == "ws"
        sub_picks = {row["uid"]: row["sub_transports"] for row in connection.execute(
            "SELECT uid,sub_transports FROM users"
        )}
        assert sub_picks["valid-xhttp"] == "ws,httpupgrade"
        assert sub_picks["legacy-vmess"] == "ws"
        migrated_bundle = json.loads(connection.execute(
            "SELECT items FROM subscriptions WHERE token='token'"
        ).fetchone()["items"])
        assert migrated_bundle == [
            {"uid": "valid-xhttp", "configs": ["ws", "httpupgrade"]},
            {"uid": "legacy-vmess", "configs": ["ws"]},
        ]
        assert connection.execute("SELECT 1 FROM meta WHERE key LIKE 'reality_%'").fetchone() is None
        assert connection.execute("SELECT 1 FROM settings WHERE key LIKE 'reality_%'").fetchone() is None
        assert {r["name"] for r in connection.execute("PRAGMA table_info(subscriptions)")} >= {
            "avatar", "plan"
        }
        assert {r["name"] for r in connection.execute("PRAGMA table_info(nodes)")} >= {
            "token", "wg_pub"
        }
    finally:
        monkeypatch.setattr(db, "_conn", previous)
        connection.close()
