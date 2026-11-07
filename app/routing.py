"""Choose which healthy instance should serve a user's link."""
import json
import time
from urllib.parse import urlsplit

from . import config, db

ENDPOINT_TTL = 900.0
SYNC_TTL = 900.0


def host_port(node) -> tuple[str, int | None]:
    """Parse the host and optional port from a node address."""
    raw = ((node or {}).get("address") or "").strip()
    if not raw:
        return "", None
    if "://" not in raw:
        raw = "//" + raw
    try:
        parts = urlsplit(raw)
        return parts.hostname or "", parts.port
    except ValueError:
        return "", None


def link_host(node) -> str:
    return host_port(node)[0]


def address_port(node) -> int | None:
    return host_port(node)[1]


def _meta_json(key: str) -> dict:
    raw = db.get_meta(key)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _fresh(data: dict, ttl: float) -> bool:
    try:
        at = float(data.get("at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(at) and time.time() - at <= ttl


def measured_edge(node) -> dict:
    """Return the most recent successful HTTP endpoint probe."""
    data = _meta_json(f"node_edge:{node['id']}")
    return data if _fresh(data, ENDPOINT_TTL) else {}


def edge_port(node) -> int:
    measured = measured_edge(node)
    if measured.get("port"):
        return int(measured["port"])
    return address_port(node) or 443


def edge_scheme(node) -> str:
    scheme = str(measured_edge(node).get("scheme") or "").lower()
    return scheme if scheme in {"http", "https"} else "https"


def record_edge_probe(node_id: int, scheme: str, port: int) -> None:
    if scheme not in {"http", "https"} or not port:
        return
    db.set_meta(f"node_edge:{node_id}", json.dumps({
        "scheme": scheme,
        "port": int(port),
        "at": time.time(),
    }))


def record_latency(node_id: int, ms, online: bool) -> None:
    db.set_meta(f"node_latency:{node_id}", json.dumps({
        "ms": ms,
        "online": bool(online),
        "at": time.time(),
    }))


def latency(node_id: int):
    data = _meta_json(f"node_latency:{node_id}")
    if not data or not _fresh(data, SYNC_TTL):
        return None
    return data.get("ms"), bool(data.get("online"))


def record_sync(node_id: int, ok: bool, uids=None, err: str = "") -> None:
    db.set_meta(f"node_sync_ok:{node_id}", "1" if ok else "0")
    db.set_meta(f"node_sync_at:{node_id}", str(time.time()))
    db.set_meta(f"node_sync_err:{node_id}", (err or "")[:200])
    if ok and uids is not None:
        db.set_meta(f"node_sync_uids:{node_id}", json.dumps(sorted(uids)))


def sync_state(node_id: int) -> dict:
    raw_ok = db.get_meta(f"node_sync_ok:{node_id}")
    try:
        at = float(db.get_meta(f"node_sync_at:{node_id}") or 0)
    except (TypeError, ValueError):
        at = 0.0
    uids = None
    stored = db.get_meta(f"node_sync_uids:{node_id}")
    if stored:
        try:
            parsed = json.loads(stored)
            if isinstance(parsed, list):
                uids = {str(value) for value in parsed}
        except (ValueError, TypeError):
            uids = None
    return {
        "ok": None if raw_ok is None else raw_ok == "1",
        "at": at,
        "err": db.get_meta(f"node_sync_err:{node_id}") or "",
        "uids": uids,
    }


def node_has_user(node, uid: str) -> tuple[bool, str]:
    state = sync_state(node["id"])
    if state["ok"] is None:
        return False, "never-synced"
    if not state["ok"]:
        return False, "sync-failed"
    if state["at"] and time.time() - state["at"] > SYNC_TTL:
        return False, "sync-stale"
    if uid and state["uids"] is not None and uid not in state["uids"]:
        return False, "user-not-synced"
    return True, "ok"


def has_credential(node) -> bool:
    from . import nodes as nodesync
    return bool((node or {}).get("token")) or bool(nodesync.panel_secret(create=False))


def _enabled_remote_nodes() -> list[dict]:
    return [
        node for node in db.list_nodes()
        if not node.get("is_local") and node.get("enabled")
        and (node.get("address") or "").strip()
    ]


def auto_node():
    """Return the fastest online node with a recent successful sync."""
    best = None
    for node in _enabled_remote_nodes():
        observed = latency(node["id"])
        if not observed or not observed[1]:
            continue
        ok, _reason = node_has_user(node, "")
        if not ok:
            continue
        if best is None or (observed[0] or 10**9) < (best[0] or 10**9):
            best = (observed[0], node)
    return best[1] if best else None


def intended_node(user: dict):
    """Find the node selected for this user before health checks."""
    if config.IS_NODE:
        return None
    from . import nodes as nodesync
    node_id = nodesync.user_node_id(user)
    if node_id == 0:
        return auto_node()
    if not user.get("node_id"):
        return None
    node = db.get_node(node_id)
    return None if node and node.get("is_local") else node


def _reason(node, reason: str) -> str:
    name = node.get("name") or "node"
    details = {
        "disabled": "the node is disabled; this config is served by the panel",
        "no-credential": "the node has no sync credential; this config is served by the panel",
        "never-synced": "the node has not completed a sync; this config is served by the panel",
        "sync-failed": "the last node sync failed; this config is served by the panel",
        "sync-stale": "the node sync is stale; this config is served by the panel",
        "user-not-synced": "the user is missing from the last sync; this config is served by the panel",
    }.get(reason, f"{reason}; this config is served by the panel")
    return f"{name}: {details}"


def _panel_target(transport: str, security: str, warnings: list[str], reason: str) -> dict:
    return {
        "target": "panel",
        "node": None,
        "transport": transport,
        "security": security,
        "warnings": warnings,
        "reasons": [reason],
    }


def serving(user: dict) -> dict:
    """Select the panel or an authenticated node for one config."""
    transport = (user.get("transport") or "ws").lower()
    security = (user.get("security") or "tls").lower()
    node = intended_node(user)
    warnings: list[str] = []
    if node is None:
        return _panel_target(transport, security, warnings, "no-node")
    if not node.get("enabled"):
        warnings.append(_reason(node, "disabled"))
        return _panel_target(transport, security, warnings, "node-disabled")
    if not has_credential(node):
        warnings.append(_reason(node, "no-credential"))
        return _panel_target(transport, security, warnings, "node-no-credential")
    has_user, reason = node_has_user(node, user.get("uid") or "")
    if not has_user:
        warnings.append(_reason(node, reason))
        return _panel_target(transport, security, warnings, f"node-{reason}")
    return {
        "target": "node",
        "node": node,
        "transport": transport,
        "security": security,
        "warnings": warnings,
        "reasons": ["node-ok"],
    }
