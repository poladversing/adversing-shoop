"""Persistent, account-scoped dashboard notifications."""
import html
import re

ORIGIN = "http://testserver"


def _notification_total(db, owner_id: int) -> int:
    with db._lock:
        row = db._connect().execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE admin_id=?", (owner_id,)
        ).fetchone()
    return int(row["n"])


def test_only_a_successful_admin_login_creates_a_notification(admin, db):
    owner_id = int(db.get_admin()["id"])
    before = _notification_total(db, owner_id)

    failed = admin.post(
        "/api/login",
        json={"username": "TiTaN", "password": "not-the-admin-password"},
        headers={"Origin": ORIGIN},
    )
    assert failed.status_code == 401
    assert _notification_total(db, owner_id) == before

    succeeded = admin.post(
        "/api/login",
        json={"username": "TiTaN", "password": admin._password},
        headers={"Origin": ORIGIN},
    )
    assert succeeded.status_code == 200
    assert _notification_total(db, owner_id) == before + 1

    latest = db.list_notifications(owner_id, limit=1)[0]
    assert latest["type"] == "admin-login"
    assert latest["title"] == "ورود موفق مدیر"
    assert latest["message"] == "نشست مدیریتی جدید آغاز شد."
    assert latest["created_at"] > 0
    assert latest["is_read"] == 0
    assert latest["link"] is None
    assert admin._password not in str(latest)
    assert "token" not in latest and "password" not in latest


def test_read_state_persists_and_notifications_are_scoped_to_the_admin(admin, db):
    owner_id = int(db.get_admin()["id"])
    db.mark_all_notifications_read(owner_id)
    own = db.create_notification(
        owner_id, "تست اعلان", "متن آزمایش", kind="test"
    )
    foreign = db.create_notification(
        owner_id + 1, "اعلان حساب دیگر", "نباید نمایش داده شود", kind="test"
    )

    first = admin.get("/api/notifications?limit=12")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["unread_count"] == 1
    assert any(item["id"] == own["id"] and not item["is_read"] for item in payload["notifications"])
    assert all(item["id"] != foreign["id"] for item in payload["notifications"])

    marked = admin.post(
        f"/api/notifications/{own['id']}/read", headers={"Origin": ORIGIN}
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["unread_count"] == 0

    after_refresh = admin.get("/api/notifications?limit=12").json()
    row = next(item for item in after_refresh["notifications"] if item["id"] == own["id"])
    assert row["is_read"] is True
    assert row["read_at"] > 0
    assert after_refresh["unread_count"] == 0

    foreign_read = admin.post(
        f"/api/notifications/{foreign['id']}/read", headers={"Origin": ORIGIN}
    )
    assert foreign_read.status_code == 404
    assert db.count_unread_notifications(owner_id + 1) == 1

    with db._lock:
        conn = db._connect()
        conn.execute("DELETE FROM notifications WHERE id=?", (foreign["id"],))
        conn.commit()


def test_mark_all_read_survives_a_new_request(admin, db):
    owner_id = int(db.get_admin()["id"])
    db.mark_all_notifications_read(owner_id)
    db.create_notification(owner_id, "اول", "۱", kind="test")
    db.create_notification(owner_id, "دوم", "۲", kind="test")

    response = admin.post("/api/notifications/read-all", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    assert response.json()["marked_read"] == 2
    assert response.json()["unread_count"] == 0
    refreshed = admin.get("/api/notifications").json()
    assert refreshed["unread_count"] == 0
    assert all(item["is_read"] for item in refreshed["notifications"])


def test_notifications_and_read_state_survive_logout_then_login_again(admin, db):
    owner_id = int(db.get_admin()["id"])
    db.mark_all_notifications_read(owner_id)
    old = db.create_notification(owner_id, "قدیمی", "باید بماند", kind="test")
    assert admin.post("/api/notifications/{}/read".format(old["id"]),
                      headers={"Origin": ORIGIN}).status_code == 200

    assert admin.post("/api/logout", headers={"Origin": ORIGIN}).status_code == 200
    assert admin.get("/api/notifications").status_code == 401
    logged_in = admin.post(
        "/api/login", json={"username": "TiTaN", "password": admin._password},
        headers={"Origin": ORIGIN},
    )
    assert logged_in.status_code == 200
    payload = admin.get("/api/notifications").json()
    assert payload["unread_count"] == 1
    old_after = next(item for item in payload["notifications"] if item["id"] == old["id"])
    assert old_after["is_read"] is True and old_after["read_at"] > 0
    assert any(item["type"] == "admin-login" and not item["is_read"]
               for item in payload["notifications"])


def test_notifications_require_the_existing_admin_session(admin):
    from app import config

    cookie = admin.cookies.get(config.SESSION_COOKIE)
    admin.cookies.clear()
    try:
        assert admin.get("/api/notifications").status_code == 401
        assert admin.post(
            "/api/notifications/read-all", headers={"Origin": ORIGIN}
        ).status_code == 401
    finally:
        if cookie:
            admin.cookies.set(config.SESSION_COOKIE, cookie)


def test_dashboard_channel_card_uses_an_explicitly_configured_link(admin, monkeypatch):
    from app import config
    from app.main import _telegram_channel_url

    channel_url = "https://t.me/example_updates"
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_URL", channel_url)
    assert _telegram_channel_url() == channel_url
    response = admin.get("/dashboard")
    assert response.status_code == 200
    escaped = html.escape(channel_url, quote=True)
    assert re.search(
        rf'<a class="notification-channel" href="{re.escape(escaped)}"',
        response.text,
    )


def test_unconfigured_channel_card_stays_unlinked_instead_of_using_support_url(admin, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SUPPORT_URL", "https://t.me/support_account")
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_URL", "")
    response = admin.get("/dashboard")
    assert response.status_code == 200
    assert '<div class="notification-channel is-unavailable" aria-disabled="true">' in response.text
    assert 'href="https://t.me/support_account"' not in response.text
    assert "برای اطلاع از آپدیت‌ها و مشکلات جوین چنل تلگرام ما شوید" in response.text
    assert "با کلیک کردن روی همین پیام وارد چنل تلگرام می‌شود" in response.text


def test_telegram_link_validation_never_turns_untrusted_values_into_links(monkeypatch):
    from app import config
    from app.main import _telegram_channel_url

    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_URL", "javascript:alert(1)")
    assert _telegram_channel_url() == ""
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_URL", "http://t.me/example")
    assert _telegram_channel_url() == ""
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_URL", "https://outside.example/channel")
    assert _telegram_channel_url() == ""
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_URL", "https://t.me/project-channel")
    assert _telegram_channel_url() == "https://t.me/project-channel"
